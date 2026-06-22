"""
Synthetic dataset for the no-network self-test (`tools/generate_candidate_data.py --sample`).

This bypasses all downloads and raster sampling and injects plausible RAW values
directly, so it exercises the parts of the pipeline that do not need the internet:
normalization, the composite scorers, the output schema, the metadata writer, and
the anchor smoke-test harness. The six spec anchor towns are hand-set to extreme
raw values so their directional expectations are met if (and only if) the scoring
math is correct.

It is NOT a substitute for a real run — the random towns are noise, not real places.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RAW_COLS = [
    "raw_comfortable_day_fraction", "raw_days_above_85", "raw_days_below_50",
    "raw_annual_precip_in", "raw_mean_relative_humidity_pct", "raw_mean_dewpoint_f",
    "raw_burn_probability", "raw_smoke_days_per_yr", "raw_nearest_pm25_monitor_km",
    "raw_pressure_diurnal_hpa", "raw_pressure_synoptic_hpa",
    "raw_lyme_incidence_per_100k", "raw_lyme_cases_2023", "raw_tick_established",
    "raw_dist_to_protected_km", "raw_natural_cover_pct",
    "raw_dist_to_airport_mi", "raw_nearest_airport_iata", "raw_dist_to_costco_mi",
    "raw_pop_within_50km", "raw_dist_to_city_km", "raw_place_density_per_sqmi",
    "raw_annual_ghi",
]


def _random_towns(n: int, rng: np.random.Generator) -> pd.DataFrame:
    # Wide spreads on purpose: real PRISM comfort spans Phoenix-to-Duluth, so the
    # synthetic towns should too, otherwise the spread guard fires on noise alone.
    comf = np.clip(rng.normal(0.55, 0.10, n), 0.2, 0.90)
    hot = np.clip(rng.normal(45, 32, n), 0, 165)
    cold = np.clip(rng.normal(75, 38, n), 0, 200)
    return pd.DataFrame({
        "place_geoid": [f"99{i:05d}" for i in range(n)],
        "name": [f"Town{i}" for i in range(n)],
        "state": rng.choice(list("ABCDEFGH"), n),  # dummy states, never an anchor
        "county": "Sample County",
        "county_fips": [f"{rng.integers(1,57):02d}{rng.integers(1,200):03d}" for _ in range(n)],
        "lat": rng.uniform(25, 49, n),
        "lon": rng.uniform(-124, -67, n),
        "elevation_ft": np.round(np.clip(rng.normal(1500, 1500, n), 0, 11000)),
        "population": rng.integers(200, 200000, n),
        "land_area_sqmi": rng.uniform(0.5, 60, n),
        "raw_comfortable_day_fraction": comf,
        "raw_days_above_85": hot,
        "raw_days_below_50": cold,
        "raw_annual_precip_in": np.clip(rng.normal(38, 14, n), 4, 75),
        "raw_mean_relative_humidity_pct": np.clip(rng.normal(60, 15, n), 20, 95),
        "raw_mean_dewpoint_f": np.clip(rng.normal(45, 6, n), 20, 65),
        "raw_burn_probability": np.clip(rng.normal(0.012, 0.01, n), 0, 0.08),
        # smoke-days: zero-ish in much of the country, higher in the West / 2023 plume.
        "raw_smoke_days_per_yr": np.where(
            rng.random(n) < 0.4, 0.0, np.clip(rng.exponential(6, n), 0, 45)),
        "raw_nearest_pm25_monitor_km": np.clip(rng.normal(35, 25, n), 1, 200),
        "raw_pressure_diurnal_hpa": np.clip(rng.normal(4.0, 1.2, n), 1, 9),
        "raw_pressure_synoptic_hpa": np.clip(rng.normal(6.5, 2.0, n), 2, 13),
        # Lyme is zero-inflated: most counties report ~no cases; the NE/upper-Midwest
        # are high. Mimic that so the percentile score isn't artificially uniform.
        "raw_lyme_incidence_per_100k": np.where(
            rng.random(n) < 0.55, 0.0, np.clip(rng.exponential(25, n), 0, 200)),
        "raw_lyme_cases_2023": rng.integers(0, 300, n),
        "raw_tick_established": rng.choice(["Y", "N"], n, p=[0.45, 0.55]),
        "raw_dist_to_protected_km": np.clip(rng.exponential(40, n), 0, 400),
        "raw_natural_cover_pct": np.clip(rng.normal(55, 25, n), 0, 100),
        "raw_dist_to_airport_mi": np.clip(rng.exponential(45, n), 1, 350),
        "raw_nearest_airport_iata": rng.choice(["DEN", "ORD", "ATL", "DFW", "SEA"], n),
        "raw_dist_to_costco_mi": np.clip(rng.exponential(30, n), 1, 300),
        "raw_pop_within_50km": np.clip(rng.exponential(300000, n), 200, 8e6),
        "raw_dist_to_city_km": np.clip(rng.exponential(60, n), 0, 400),
        "raw_place_density_per_sqmi": np.clip(rng.exponential(1500, n), 5, 20000),
        "raw_annual_ghi": np.clip(rng.normal(4.5, 0.55, n), 2.8, 6.5),
    })


def _anchor(name, state, county, fips, lat, lon, **raw) -> dict:
    base = {
        "place_geoid": f"AN{abs(hash((name, state))) % 100000:05d}",
        "name": name, "state": state, "county": county, "county_fips": fips,
        "lat": lat, "lon": lon, "population": 50000, "land_area_sqmi": 20.0,
    }
    base.update(raw)
    return base


def make_sample() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    towns = _random_towns(300, rng)

    anchors = [
        # Olympia, WA — wet, cloudy, marine. Synoptic LOW is proxy-limited (expected note).
        _anchor("Olympia", "WA", "Thurston", "53067", 47.04, -122.89,
                raw_comfortable_day_fraction=0.58, raw_days_above_85=2, raw_days_below_50=120,
                raw_annual_precip_in=52.0, raw_mean_relative_humidity_pct=82.0,
                raw_mean_dewpoint_f=52.0, raw_burn_probability=0.004,
                raw_pressure_diurnal_hpa=4.2, raw_pressure_synoptic_hpa=9.8,
                raw_tick_established="Y",
                raw_lyme_incidence_per_100k=3.0, raw_lyme_cases_2023=8,
                raw_annual_ghi=3.3),
        # San Luis Obispo, CA — mild marine, tiny diurnal swing, near coast.
        _anchor("San Luis Obispo", "CA", "San Luis Obispo", "06079", 35.28, -120.66,
                raw_comfortable_day_fraction=0.90, raw_days_above_85=4, raw_days_below_50=14,
                raw_annual_precip_in=22.0, raw_mean_relative_humidity_pct=72.0,
                raw_mean_dewpoint_f=46.0, raw_burn_probability=0.010,
                raw_pressure_diurnal_hpa=2.0, raw_pressure_synoptic_hpa=3.8,
                raw_tick_established="Y",
                raw_lyme_incidence_per_100k=4.0, raw_lyme_cases_2023=10,
                raw_annual_ghi=5.3),
        # Santa Fe, NM — sunny, dry, high-desert big diurnal swing, no established ticks.
        _anchor("Santa Fe", "NM", "Santa Fe", "35049", 35.69, -105.94,
                raw_comfortable_day_fraction=0.62, raw_days_above_85=20, raw_days_below_50=110,
                raw_annual_precip_in=14.0, raw_mean_relative_humidity_pct=38.0,
                raw_mean_dewpoint_f=30.0, raw_burn_probability=0.020,
                raw_pressure_diurnal_hpa=6.6, raw_pressure_synoptic_hpa=6.0,
                raw_tick_established="N",
                raw_lyme_incidence_per_100k=0.0, raw_lyme_cases_2023=0,
                raw_annual_ghi=6.0),
        # Phoenix, AZ — extreme heat, very dry, very sunny.
        _anchor("Phoenix", "AZ", "Maricopa", "04013", 33.45, -112.07,
                raw_comfortable_day_fraction=0.40, raw_days_above_85=175, raw_days_below_50=8,
                raw_annual_precip_in=8.0, raw_mean_relative_humidity_pct=27.0,
                raw_mean_dewpoint_f=33.0, raw_burn_probability=0.006,
                raw_pressure_diurnal_hpa=3.4, raw_pressure_synoptic_hpa=4.4,
                raw_tick_established="N",
                raw_lyme_incidence_per_100k=0.3, raw_lyme_cases_2023=2,
                raw_annual_ghi=6.3),
        # International Falls, MN — brutal cold, low sun.
        _anchor("International Falls", "MN", "Koochiching", "27071", 48.60, -93.41,
                raw_comfortable_day_fraction=0.34, raw_days_above_85=2, raw_days_below_50=238,
                raw_annual_precip_in=24.0, raw_mean_relative_humidity_pct=68.0,
                raw_mean_dewpoint_f=33.0, raw_burn_probability=0.003,
                raw_pressure_diurnal_hpa=4.6, raw_pressure_synoptic_hpa=9.6,
                raw_tick_established="Y",
                raw_lyme_incidence_per_100k=25.0, raw_lyme_cases_2023=5,
                raw_annual_ghi=3.6),
        # Hartford, CT — humid east, Lyme epicenter.
        _anchor("Hartford", "CT", "Hartford", "09003", 41.76, -72.67,
                raw_comfortable_day_fraction=0.50, raw_days_above_85=18, raw_days_below_50=120,
                raw_annual_precip_in=48.0, raw_mean_relative_humidity_pct=72.0,
                raw_mean_dewpoint_f=54.0, raw_burn_probability=0.002,
                raw_pressure_diurnal_hpa=4.0, raw_pressure_synoptic_hpa=8.2,
                raw_tick_established="Y",
                raw_lyme_incidence_per_100k=120.0, raw_lyme_cases_2023=900,
                raw_annual_ghi=3.9),
    ]
    df = pd.concat([towns, pd.DataFrame(anchors)], ignore_index=True)
    return df
