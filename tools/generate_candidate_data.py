#!/usr/bin/env python3
"""
Candidate-data generator / Step-1 orchestrator (with a per-criterion compute cache).

  python tools/generate_candidate_data.py                # full run; unchanged criteria load from cache
  python tools/generate_candidate_data.py --anchors-only # full pipeline restricted to the 6 anchor towns
  python tools/generate_candidate_data.py --sample       # no-network self-test on synthetic data
  python tools/generate_candidate_data.py --seed-cache   # populate the cache from existing candidate_scores.parquet
  python tools/generate_candidate_data.py --force a,b     # force-recompute stages a,b (or "all"); "base" allowed

Outputs (output/, git-ignored): candidate_scores.parquet, candidate_scores.csv, column_metadata.csv

Caching: each criterion's raw columns are cached in data/work/cache/ keyed by the
stage's logic version + its config dependencies + the place set/coordinates. Change
a criterion's inputs (or bump its version) and only it recomputes; add a new
criterion and only it computes. Scores/metadata are always recomputed from the
cached raws. See data_pipeline/cache.py.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# This script lives in tools/; add the project root so `data_pipeline` imports resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipeline import config as C
from data_pipeline import cache, scoring, smoke


# Stage metadata (no heavy imports here — fn is resolved lazily). `deps` lists the
# config values whose change should invalidate the stage's cache; bump `version`
# when the stage's *computation* changes.
def stage_specs() -> list[dict]:
    return [
        {"name": "temperature_comfort",
         "out": ["raw_comfortable_day_fraction", "raw_days_above_85", "raw_days_below_50"],
         "deps": [C.PRISM_RES, C.PRISM_REGION, C.COMFORT_LOW_F, C.COMFORT_HIGH_F],
         "version": 1},
        {"name": "dryness",
         "out": ["raw_annual_precip_in", "raw_mean_relative_humidity_pct", "raw_mean_dewpoint_f"],
         "deps": [C.PRISM_RES, C.PRISM_REGION],
         "version": 1},
        {"name": "pressure",
         "out": ["raw_pressure_diurnal_hpa", "raw_pressure_synoptic_hpa"],
         "deps": [C.ERA5_DATASET, C.ERA5_VARIABLE, C.ERA5_AREA, C.ERA5_YEARS],
         "version": 1},
        {"name": "wildfire",
         "out": ["raw_burn_probability"],
         "deps": [C.WRC_BP_URL, C.WILDFIRE_NEIGHBORHOOD_M, C.WILDFIRE_NEIGHBORHOOD_PTS],
         "version": 1},
        {"name": "air_quality",
         "out": ["raw_annual_pm25_ugm3"],
         "deps": [C.PM25_GRID_URL],
         "version": 1},
        {"name": "lyme",
         "out": ["raw_lyme_incidence_per_100k", "raw_lyme_cases_2023", "raw_tick_established"],
         "deps": [C.CDC_LYME_URL, C.COUNTY_POP_URL, C.CDC_TICK_URL],
         "version": 2},  # v2: invalidates a count-based cache; rate-only now
        {"name": "nature_access",
         "out": ["raw_dist_to_protected_km", "raw_natural_cover_pct"],
         "deps": [C.PADUS_SERVICE, C.PADUS_GAP_STATUSES, C.PROTECTED_MIN_ACRES,
                  C.NLCD_URL, C.NLCD_NATURAL_CLASSES, C.NATURE_COVER_RADIUS_M,
                  C.NATURE_COVER_PTS],
         "version": 1},
        {"name": "airport",
         "out": ["raw_dist_to_airport_mi", "raw_nearest_airport_iata"],
         "deps": [C.AIRPORTS_URL, C.AIRPORT_TYPES],
         "version": 1},
        {"name": "amenities",
         "out": ["raw_dist_to_costco_mi"],
         "deps": [C.OVERPASS_URL, C.COSTCO_WIKIDATA],
         "version": 2},  # v2: dedup co-located OSM elements per Costco
        {"name": "isolation",
         "out": ["raw_pop_within_50km", "raw_dist_to_city_km", "raw_place_density_per_sqmi"],
         "deps": [C.CITY_POP_THRESHOLD, C.ISOLATION_RADIUS_KM, C.POP_URL],
         "version": 1},
        {"name": "sunlight",
         "out": ["raw_annual_ghi"],
         "deps": [C.GHI_ZIP_URL, C.SUNLIGHT_MODE],
         "version": 1},
    ]


def _base_path(scope: str):
    sig = cache._h("base", scope, C.VINTAGE_TIGER, C.POP_URL, sorted(C.EXCLUDE_USPS))[:12]
    return cache.CACHE_DIR / f"base_{scope}_{sig}.parquet"


def build_or_load_base(anchors_only: bool, force: bool = False):
    """Keys table (place, state, county, FIPS, lat/lon, population, land area). Cached
    because it includes a network population join + a 31k-point county spatial join."""
    import pandas as pd
    from data_pipeline import places

    scope = "anchors" if anchors_only else "full"
    path = _base_path(scope)
    cache.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        print(f"[base] cached ✓ (places + population + county join, scope={scope})")
        return pd.read_parquet(path)

    print("[base] Census places ...")
    df = places.load_places()
    if anchors_only:
        keep = [(a["name"], a["state"]) for a in C.ANCHORS]
        df = df[df.apply(lambda r: (r["name"], r["state"]) in keep, axis=1)].copy()
    print(f"       {len(df):,} candidate places")
    print("[base] population ...")
    df = places.attach_population(df)
    print("[base] county spatial join ...")
    df = places.attach_county(df)
    for old in cache.CACHE_DIR.glob(f"base_{scope}_*.parquet"):
        try:
            old.unlink()
        except OSError:
            pass
    df.to_parquet(path, index=False)
    return df


def build_real_matrix(anchors_only: bool = False, force: set[str] | None = None):
    from data_pipeline import (places, sampling, ticks, era5, air_quality, isolation, nature,
                        airports, amenities)

    force = force or set()
    all_ = "all" in force
    t0 = time.time()

    base = build_or_load_base(anchors_only, force=all_ or "base" in force)
    coords_sig = cache.coords_signature(base)

    # Basic geographic key column (cached like a stage; not a scored criterion).
    df0 = cache.run_stage(
        {"name": "elevation", "fn": places.attach_elevation,
         "out": ["elevation_ft"], "deps": [C.ELEVATION_DEM_BUCKET], "version": 2},
        base, coords_sig, force=all_ or "elevation" in force)
    base = df0

    fns = {
        "temperature_comfort": sampling.temperature_comfort,
        "dryness": sampling.dryness,
        "pressure": era5.sample_pressure,
        "wildfire": sampling.wildfire,
        "air_quality": air_quality.attach_air_quality,
        "lyme": ticks.attach_lyme,
        "nature_access": nature.attach_nature_access,
        "airport": airports.attach_airport,
        "amenities": amenities.attach_amenity,
        "isolation": isolation.attach_isolation,
        "sunlight": sampling.sunlight,
    }
    df = base
    print("[criteria] (cached stages are skipped)")
    skipped = []
    for spec in stage_specs():
        spec = dict(spec, fn=fns[spec["name"]])
        try:
            df = cache.run_stage(spec, df, coords_sig,
                                 force=all_ or spec["name"] in force)
        except Exception as exc:
            msg = (str(exc).splitlines() or [type(exc).__name__])[0]
            print(f"      [{spec['name']}] SKIPPED — data unavailable: {msg}")
            skipped.append(spec["name"])

    if skipped:
        print(f"\n  NOTE: skipped (no data, omitted from output): {', '.join(skipped)}."
              f"\n  Provide the data and re-run to add them.")
    print(f"      raw layers ready in {time.time()-t0:.0f}s")
    return df


def seed_cache() -> int:
    """Populate base + per-stage caches from an existing candidate_scores.parquet so
    a normal run recomputes nothing already done."""
    import pandas as pd

    if not C.OUT_PARQUET.exists():
        print(f"No {C.OUT_PARQUET.name} to seed from."); return 1
    m = pd.read_parquet(C.OUT_PARQUET)
    base_cols = [c for c in C.KEY_COLUMNS if c in m.columns]
    base = m[base_cols].copy()
    cache.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    base.to_parquet(_base_path("full"), index=False)
    coords_sig = cache.coords_signature(base)
    seeded = []
    for spec in stage_specs():
        if cache.write_stage_cache(spec, coords_sig, m):
            seeded.append(spec["name"])
    print(f"Seeded base + stages from {C.OUT_PARQUET.name}: {', '.join(seeded)}")
    print("A normal `python tools/generate_candidate_data.py` will now skip these unless their inputs change.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true",
                    help="no-network self-test on synthetic data")
    ap.add_argument("--anchors-only", action="store_true",
                    help="full pipeline restricted to the 6 anchor towns")
    ap.add_argument("--seed-cache", action="store_true",
                    help="populate the cache from candidate_scores.parquet, then exit")
    ap.add_argument("--force", default="",
                    help="comma-separated stage names to recompute (or 'all'; 'base' allowed)")
    args = ap.parse_args()

    if args.seed_cache:
        return seed_cache()

    if args.sample:
        from data_pipeline import sample_data
        print("=== SAMPLE MODE (synthetic data, no network) ===")
        raw = sample_data.make_sample()
        out_dir = C.ROOT / "sample_run"
        out_dir.mkdir(exist_ok=True)
        C.OUT_PARQUET = out_dir / "SAMPLE_candidate_scores.parquet"  # type: ignore
        C.OUT_CSV = out_dir / "SAMPLE_candidate_scores.csv"          # type: ignore
        C.OUT_METADATA = out_dir / "SAMPLE_column_metadata.csv"      # type: ignore
    else:
        force = {s.strip() for s in args.force.split(",") if s.strip()}
        raw = build_real_matrix(anchors_only=args.anchors_only, force=force)

    matrix, meta = scoring.assemble(raw)
    scoring.write_outputs(matrix, meta)
    d = C.OUT_PARQUET.parent.name
    print(f"\nWrote {d}/{C.OUT_PARQUET.name}, {d}/{C.OUT_CSV.name}, {d}/{C.OUT_METADATA.name}")
    print(f"Matrix shape: {matrix.shape[0]} rows x {matrix.shape[1]} cols")

    report = smoke.run_smoke_tests(matrix)
    smoke.print_report(report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
