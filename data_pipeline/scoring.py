"""
Normalization, composite scorers, matrix assembly, and metadata output.

Scores are 0-100 with "higher = better fit" everywhere. The pipeline does NOT
apply cross-criterion weights — every score is independent and un-weighted, as the
downstream weighting tool expects.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C


# --------------------------------------------------------------------------- #
# Normalizers
# --------------------------------------------------------------------------- #
def percentile_score(values: pd.Series, higher_is_better: bool) -> pd.Series:
    """Percentile rank across all towns -> 0-100. NaNs are imputed at the median
    (50) so no score column has nulls, per the spec's 'impute or flag' rule."""
    v = pd.to_numeric(values, errors="coerce")
    pct = v.rank(pct=True, method="average") * 100.0
    if not higher_is_better:
        pct = 100.0 - pct
    return pct.fillna(50.0)


def comfort_index(df: pd.DataFrame) -> pd.Series:
    """comfortable_days - 2*hot_days - cold_days (heat penalised 2x cold)."""
    comfortable = df["raw_comfortable_day_fraction"] * 365.0
    hot = df["raw_days_above_85"]
    cold = df["raw_days_below_50"]
    return comfortable - C.HEAT_PENALTY_WEIGHT * hot - C.COLD_PENALTY_WEIGHT * cold


def score_temperature_comfort(df: pd.DataFrame) -> pd.Series:
    """Percentile rank of the weighted comfort index (higher = better). Percentile
    (vs a linear map) keeps the distribution well spread and places both heat and
    cold extremes in the bottom quartile."""
    return percentile_score(comfort_index(df), higher_is_better=True)


# --------------------------------------------------------------------------- #
# Composite scorers
# --------------------------------------------------------------------------- #
def score_lyme(df: pd.DataFrame) -> pd.Series:
    """Lyme incidence -> 0-100, higher = lower risk. Lyme is strongly zero-inflated,
    so: counties with 0 reported cases get the top score (100), and the
    positive-incidence (Lyme-endemic) counties are inverse-rank-scored *among
    themselves* across the full 0-100 range (lowest positive incidence ≈ 100,
    highest ≈ 0). This avoids a cliff where a negligible-incidence county would
    otherwise drop far below a zero county."""
    inc = pd.to_numeric(df["raw_lyme_incidence_per_100k"], errors="coerce")
    score = pd.Series(np.nan, index=df.index)
    score[inc == 0] = 100.0
    pos = inc > 0
    if pos.any():
        pct = inc[pos].rank(pct=True)          # ascending among positives
        score[pos] = (1.0 - pct) * 100.0
    return score.fillna(50.0)                  # NaN incidence -> neutral


def score_nature_access(df: pd.DataFrame) -> pd.Series:
    """Nature access, higher = better: 0.5*pct(close to a large protected area) +
    0.5*pct(high natural-cover fraction nearby)."""
    dist = pd.to_numeric(df["raw_dist_to_protected_km"], errors="coerce")
    nat = pd.to_numeric(df["raw_natural_cover_pct"], errors="coerce")
    acc = 0.5 * (1.0 - dist.rank(pct=True)) + 0.5 * nat.rank(pct=True)
    return (acc * 100.0).fillna(50.0)


def score_isolation(df: pd.DataFrame) -> pd.Series:
    """Regional remoteness, higher = more isolated:
    0.5*pct(low population within radius) + 0.5*pct(far from nearest sizable city)."""
    pop = pd.to_numeric(df["raw_pop_within_50km"], errors="coerce")
    dist = pd.to_numeric(df["raw_dist_to_city_km"], errors="coerce")
    iso = 0.5 * (1.0 - pop.rank(pct=True)) + 0.5 * dist.rank(pct=True)
    return (iso * 100.0).fillna(50.0)


def score_dryness(df: pd.DataFrame) -> pd.Series:
    """Mould-propensity composite: wetness = 0.5*pct(annual precip) + 0.5*pct(annual
    mean RH), both higher = wetter. Inverted so drier (less rain + lower humidity)
    scores higher. Percentile components make it robust to units/outliers."""
    pct_p = pd.to_numeric(df["raw_annual_precip_in"], errors="coerce").rank(pct=True)
    pct_rh = pd.to_numeric(df["raw_mean_relative_humidity_pct"], errors="coerce").rank(pct=True)
    wetness = 0.5 * pct_p + 0.5 * pct_rh
    return ((1.0 - wetness) * 100.0).fillna(50.0)


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def assemble(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Apply every criterion's normalizer, returning (matrix, metadata_rows)."""
    meta: list[dict] = []
    out = df.copy()

    # Key-column metadata
    key_units = {
        "place_geoid": "Census place GEOID (7-digit)", "name": "place name",
        "state": "USPS state", "county": "county name",
        "county_fips": "county FIPS (5-digit)", "lat": "degrees",
        "lon": "degrees", "elevation_ft": "feet", "population": "persons",
        "land_area_sqmi": "square miles",
    }
    key_sources = {
        "elevation_ft": "Open-Meteo Elevation API (Copernicus DEM GLO-90, 90 m)",
        "population": "2020 Census decennial (P1_001N)",
    }
    for k in C.KEY_COLUMNS:
        meta.append({
            "column": k, "description": f"key: {k}", "units": key_units.get(k, ""),
            "source": key_sources.get(k, "Census Gazetteer 2023 / TIGER 2023"),
            "source_date": "2020-2023", "normalization_method": "none (key)",
        })

    for crit in C.CRITERIA:
        # Omit any criterion whose stage was skipped (its raw columns are absent),
        # so the matrix is still produced from the criteria that did compute.
        if not any(rc in out.columns for rc, _ in crit["raw_cols"]):
            continue
        method = crit["score_method"]
        col = crit["score_col"]

        if method == "composite" and crit["name"] == "temperature_comfort":
            out[col] = score_temperature_comfort(out)
            norm = "percentile rank of weighted comfort index (comfortable-2*hot-cold)"
        elif method == "percentile":
            out[col] = percentile_score(out[crit["raw_for_score"]], crit["higher_is_better"])
            direction = "higher" if crit["higher_is_better"] else "lower"
            norm = f"percentile rank ({direction} raw = better); NaN imputed at 50"
        elif method == "composite" and crit["name"] == "dryness":
            out[col] = score_dryness(out)
            norm = "inverted percentile of 0.5*pct(annual precip)+0.5*pct(annual RH)"
        elif method == "composite" and crit["name"] == "lyme":
            out[col] = score_lyme(out)
            norm = "0 reported Lyme = 100; positive incidence inverse-rank-scored"
        elif method == "composite" and crit["name"] == "isolation":
            out[col] = score_isolation(out)
            norm = "0.5*pct(low pop within 50km) + 0.5*pct(far from city >=50k)"
        elif method == "composite" and crit["name"] == "nature_access":
            out[col] = score_nature_access(out)
            norm = "0.5*pct(close to large protected area) + 0.5*pct(high natural cover)"
        else:
            raise ValueError(f"unknown score method for {crit['name']}")

        # raw-column metadata
        for raw_col, raw_desc in crit["raw_cols"]:
            meta.append({
                "column": raw_col, "description": raw_desc, "units": _raw_units(raw_col),
                "source": crit["source"], "source_date": crit["source_date"],
                "normalization_method": "none (raw value)",
            })
        # score-column metadata
        meta.append({
            "column": col, "description": crit["description"], "units": crit["units"],
            "source": crit["source"], "source_date": crit["source_date"],
            "normalization_method": norm,
        })

    # Column order: keys, then for each criterion its raws then its score.
    ordered = list(C.KEY_COLUMNS)
    for crit in C.CRITERIA:
        ordered += [rc for rc, _ in crit["raw_cols"]]
        ordered.append(crit["score_col"])
    ordered = [c for c in ordered if c in out.columns]
    out = out[ordered]
    return out, meta


def _raw_units(col: str) -> str:
    table = {
        "raw_comfortable_day_fraction": "fraction (0-1)",
        "raw_days_above_85": "days/yr", "raw_days_below_50": "days/yr",
        "raw_annual_precip_in": "inches/yr",
        "raw_mean_dewpoint_f": "°F", "raw_mean_relative_humidity_pct": "%",
        "raw_burn_probability": "probability (0-1)",
        "raw_smoke_days_per_yr": "days/yr", "raw_nearest_pm25_monitor_km": "km",
        "raw_pop_within_50km": "persons", "raw_dist_to_city_km": "km",
        "raw_place_density_per_sqmi": "persons/sq mi",
        "raw_dist_to_protected_km": "km", "raw_natural_cover_pct": "%",
        "raw_dist_to_airport_mi": "miles", "raw_nearest_airport_iata": "code",
        "raw_dist_to_costco_mi": "miles",
        "raw_pressure_diurnal_hpa": "hPa/day", "raw_pressure_synoptic_hpa": "hPa",
        "raw_lyme_incidence_per_100k": "cases/100k", "raw_lyme_cases_2023": "cases/yr",
        "raw_tick_established": "Y/N", "raw_annual_ghi": "kWh/m²/day",
    }
    return table.get(col, "")


def write_outputs(matrix: pd.DataFrame, meta: list[dict]) -> None:
    matrix.to_parquet(C.OUT_PARQUET, index=False)
    matrix.to_csv(C.OUT_CSV, index=False)
    pd.DataFrame(meta, columns=[
        "column", "description", "units", "source", "source_date", "normalization_method"
    ]).to_csv(C.OUT_METADATA, index=False)
