"""
Per-criterion compute cache.

The expensive part of the pipeline is sampling each criterion's raw values at ~31k
points (PRISM rasters, ERA5 stats, wildfire neighborhoods, joins). This module
caches each stage's raw output columns to data/work/cache/ keyed by a signature of:

  - the stage's logic version (bump to invalidate after a code change),
  - the config values the stage depends on (URLs, thresholds, years, ...),
  - the candidate place set and their coordinates / county FIPS.

If nothing a stage depends on changed, its cache loads instantly and the stage is
skipped. Adding a new criterion (a new stage) computes only that stage; the rest
load from cache. Scores and metadata are always recomputed downstream from the
cached raws, so changing a *scoring* method does not force re-sampling.
"""
from __future__ import annotations

import hashlib

import pandas as pd

from . import config as C

CACHE_DIR = C.DATA_WORK / "cache"


def _h(*parts) -> str:
    return hashlib.sha256(repr(parts).encode()).hexdigest()


def coords_signature(base: pd.DataFrame) -> str:
    """Stable signature of the place set + coordinates + county FIPS. Any change
    (different towns, shifted centroids, new county vintage) invalidates all stages."""
    key = base[["place_geoid", "lat", "lon", "county_fips"]].copy()
    key = key.sort_values("place_geoid")
    key["lat"] = key["lat"].round(6)
    key["lon"] = key["lon"].round(6)
    return hashlib.sha256(key.to_csv(index=False).encode()).hexdigest()


def stage_sig(spec: dict, coords_sig: str) -> str:
    return _h(spec["name"], spec.get("version", 1), spec["deps"], coords_sig)[:12]


def stage_path(spec: dict, coords_sig: str):
    return CACHE_DIR / f"{spec['name']}_{stage_sig(spec, coords_sig)}.parquet"


def run_stage(spec: dict, df: pd.DataFrame, coords_sig: str,
              force: bool = False) -> pd.DataFrame:
    """Return df with the stage's raw columns added, from cache if valid else by
    computing spec['fn'](df) and caching the result."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = stage_path(spec, coords_sig)
    name = spec["name"]
    if path.exists() and not force:
        cached = pd.read_parquet(path)
        print(f"      [{name}] cached ✓ — skipping recompute")
        return df.merge(cached, on="place_geoid", how="left")

    print(f"      [{name}] computing ...")
    result = spec["fn"](df)
    keep = ["place_geoid"] + [c for c in spec["out"] if c in result.columns]
    # Drop stale caches for this stage (old signatures) before writing the new one.
    for old in CACHE_DIR.glob(f"{name}_*.parquet"):
        try:
            old.unlink()
        except OSError:
            pass
    result[keep].to_parquet(path, index=False)
    return result


def write_stage_cache(spec: dict, coords_sig: str, source: pd.DataFrame) -> bool:
    """Seed a stage cache from an existing frame (e.g. candidate_scores.parquet)."""
    cols = [c for c in spec["out"] if c in source.columns]
    if "place_geoid" not in source.columns or not cols:
        return False
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    source[["place_geoid"] + cols].to_parquet(stage_path(spec, coords_sig), index=False)
    return True
