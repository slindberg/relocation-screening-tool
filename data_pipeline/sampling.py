"""
Raster/point sampling and the raw climate metrics derived from PRISM.

Every sampler reprojects the candidate points into the *raster's own CRS* before
sampling. This is deliberate: the spec warns that the most common failure is a
coordinate-system mismatch that drops points into the ocean or the next state.
PRISM temperature rasters are in DEGREES CELSIUS and are converted to °F here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from . import fetch


def _c_to_f(c: np.ndarray) -> np.ndarray:
    return c * 9.0 / 5.0 + 32.0


def sample_raster(lon: np.ndarray, lat: np.ndarray, raster_path) -> np.ndarray:
    """Sample one raster band at lon/lat (WGS84). Returns float array with NaN for
    nodata / off-grid points. Points are transformed into the raster CRS first."""
    import rasterio
    from pyproj import Transformer

    with rasterio.open(raster_path) as src:
        if src.crs is not None and src.crs.to_string() not in (C.WGS84, "EPSG:4326"):
            tr = Transformer.from_crs(C.WGS84, src.crs, always_xy=True)
            xs, ys = tr.transform(lon, lat)
        else:
            xs, ys = lon, lat
        nodata = src.nodata
        vals = np.array([v[0] for v in src.sample(np.c_[xs, ys])], dtype="float64")
    if nodata is not None:
        vals = np.where(vals == nodata, np.nan, vals)
    # PRISM uses -9999; guard regardless of declared nodata.
    vals = np.where(vals <= -9990, np.nan, vals)
    return vals


def sample_raster_neighborhood(lon, lat, raster_path, radius_m: float,
                               n_side: int = 5, agg: str = "mean"):
    """Aggregate valid raster values over a small grid of offset points around each
    location, using point sampling (src.sample) rather than windowed reads.

    Used for rasters that are nodata over developed land (e.g. FSim burn
    probability): a single centroid pixel may be nodata, but nearby burnable cells
    carry the town's real exposure. An (n_side x n_side) grid spanning +-radius_m is
    sampled per location; valid (non-nodata) values are aggregated. Returns NaN only
    where the whole neighborhood is nodata/off-grid. Points are transformed into the
    raster CRS first."""
    import rasterio
    from pyproj import Transformer

    lon = np.asarray(lon, dtype="float64")
    lat = np.asarray(lat, dtype="float64")
    out = np.full(len(lon), np.nan)
    with rasterio.open(raster_path) as src:
        if src.crs is not None and src.crs.to_epsg() != 4326:
            tr = Transformer.from_crs(C.WGS84, src.crs, always_xy=True)
            cx, cy = tr.transform(lon, lat)
        else:
            cx, cy = lon, lat
        cx = np.asarray(cx, dtype="float64")
        cy = np.asarray(cy, dtype="float64")
        nodata = src.nodata
        offs = np.linspace(-radius_m, radius_m, n_side)
        gx, gy = np.meshgrid(offs, offs)
        gx, gy = gx.ravel(), gy.ravel()           # (k,) offsets
        k = gx.size

        for start in range(0, len(lon), 2000):     # chunk to bound memory
            sl = slice(start, min(start + 2000, len(lon)))
            xs = (cx[sl][:, None] + gx[None, :])    # (chunk, k)
            ys = (cy[sl][:, None] + gy[None, :])
            pts = np.column_stack([xs.ravel(), ys.ravel()])
            vals = np.array([v[0] for v in src.sample(pts)], dtype="float64")
            vals = vals.reshape(xs.shape)           # (chunk, k)
            if nodata is not None:
                vals = np.where(vals == nodata, np.nan, vals)
            vals = np.where(vals <= -9990, np.nan, vals)  # guard huge-neg nodata
            all_nan = np.isnan(vals).all(axis=1)
            with np.errstate(all="ignore"):
                agg_vals = (np.nanmax(vals, axis=1) if agg == "max"
                            else np.nanmean(vals, axis=1))
            agg_vals = np.where(all_nan, np.nan, agg_vals)
            out[sl] = agg_vals
    return out


# --------------------------------------------------------------------------- #
# PRISM-derived raw metrics
# --------------------------------------------------------------------------- #
def temperature_comfort(df: pd.DataFrame) -> pd.DataFrame:
    """Comfortable / hot / cold day counts from 365 daily tmax normals (°C->°F)."""
    lon, lat = df["lon"].to_numpy(), df["lat"].to_numpy()
    n = len(df)
    comfortable = np.zeros(n)
    hot = np.zeros(n)
    cold = np.zeros(n)
    valid = np.zeros(n)

    for bil in fetch.fetch_prism_daily_tmax():
        tmax_f = _c_to_f(sample_raster(lon, lat, bil))
        ok = ~np.isnan(tmax_f)
        valid += ok
        comfortable += ((tmax_f >= C.COMFORT_LOW_F) & (tmax_f <= C.COMFORT_HIGH_F) & ok)
        hot += ((tmax_f > C.COMFORT_HIGH_F) & ok)
        cold += ((tmax_f < C.COMFORT_LOW_F) & ok)

    # Normalise to a 365-day year in case a handful of days were nodata.
    scale = np.where(valid > 0, 365.0 / valid, np.nan)
    comfortable *= scale
    hot *= scale
    cold *= scale

    out = df.copy()
    out["raw_comfortable_day_fraction"] = comfortable / 365.0
    out["raw_days_above_85"] = hot
    out["raw_days_below_50"] = cold
    return out


def _rh_from_magnus(td_c: np.ndarray, t_c: np.ndarray) -> np.ndarray:
    """Relative humidity (%) from dewpoint and temperature (°C), Magnus over water."""
    e = 6.112 * np.exp(17.62 * td_c / (243.12 + td_c))
    es = 6.112 * np.exp(17.62 * t_c / (243.12 + t_c))
    return np.clip(100.0 * e / es, 0, 100)


def dryness(df: pd.DataFrame) -> pd.DataFrame:
    """Mould-propensity inputs: annual precipitation (wetness) and annual mean
    relative humidity (persistent dampness). Both drive mould growth, and using the
    ANNUAL figures captures year-round damp climates (e.g. the Pacific NW, whose mould
    problem is cool wet conditions most of the year, not summer mugginess). The
    composite score is built in scoring.score_dryness. Annual mean dewpoint is kept
    as an extra raw column."""
    lon, lat = df["lon"].to_numpy(), df["lat"].to_numpy()
    ppt_mm = sample_raster(lon, lat, fetch.fetch_prism_annual("ppt"))
    td_c = sample_raster(lon, lat, fetch.fetch_prism_annual("tdmean"))
    t_c = sample_raster(lon, lat, fetch.fetch_prism_annual("tmean"))
    out = df.copy()
    out["raw_annual_precip_in"] = ppt_mm / 25.4
    out["raw_mean_relative_humidity_pct"] = _rh_from_magnus(td_c, t_c)
    out["raw_mean_dewpoint_f"] = _c_to_f(td_c)
    return out


# --------------------------------------------------------------------------- #
# Wildfire
# --------------------------------------------------------------------------- #
def wildfire(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    raster = fetch.fetch_wildfire()
    bp = sample_raster_neighborhood(
        df["lon"].to_numpy(), df["lat"].to_numpy(), raster,
        radius_m=C.WILDFIRE_NEIGHBORHOOD_M, n_side=C.WILDFIRE_NEIGHBORHOOD_PTS,
        agg="mean",
    )
    # Most towns have some burnable land within the neighborhood, so a very high
    # empty fraction means the raster failed to read (missing/corrupt/CRS), not that
    # the whole country is non-burnable. Fail loudly rather than emit a flat column.
    nan_frac = float(np.isnan(bp).mean())
    if nan_frac > 0.7:
        raise RuntimeError(
            f"Wildfire: {nan_frac:.0%} of towns sampled empty from {raster}. The "
            f"burn-probability raster is likely missing/corrupt. Delete "
            f"data/raw/wrc_CONUS_BP.tif (and data/raw/RDS-2016-0034-3.zip if present) "
            f"and re-run to re-fetch."
        )
    # Where no burnable cell exists within the neighborhood (deep urban / cropland /
    # offshore), wildfire exposure is effectively negligible -> 0 burn probability.
    out["raw_burn_probability"] = np.where(np.isnan(bp), 0.0, bp)
    return out


# --------------------------------------------------------------------------- #
# Sunlight
# --------------------------------------------------------------------------- #
def sunlight(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    ghi_raster = fetch.fetch_ghi_raster() if C.SUNLIGHT_MODE == "raster" else None
    if ghi_raster is not None:
        out["raw_annual_ghi"] = sample_raster(
            df["lon"].to_numpy(), df["lat"].to_numpy(), ghi_raster
        )
    else:
        out["raw_annual_ghi"] = _sunlight_api(df)
    return out


def _sunlight_api(df: pd.DataFrame) -> np.ndarray:
    """NREL Solar Resource API, one call per point (annual avg GHI, kWh/m²/day).
    Cached to disk so re-runs are free. Slow for 30k points — intended for the
    anchor set / shortlist, or as a fallback when no GHI raster is staged."""
    import json
    import time
    import requests

    cache_path = C.DATA_WORK / "nrel_ghi_cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    vals = np.full(len(df), np.nan)
    for i, (lon, lat) in enumerate(zip(df["lon"], df["lat"])):
        key = f"{lat:.4f},{lon:.4f}"
        if key not in cache:
            try:
                r = requests.get(
                    C.NREL_SOLAR_API,
                    params={"api_key": C.NREL_API_KEY, "lat": lat, "lon": lon},
                    timeout=30,
                )
                j = r.json()
                cache[key] = j["outputs"]["avg_ghi"]["annual"]
                time.sleep(0.2)
            except Exception:
                cache[key] = None
            if i % 200 == 0:
                cache_path.write_text(json.dumps(cache))
        v = cache.get(key)
        vals[i] = np.nan if v is None else float(v)
    cache_path.write_text(json.dumps(cache))
    return vals
