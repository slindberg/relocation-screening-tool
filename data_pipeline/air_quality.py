"""
Chronic air-quality criterion: annual-mean ground-level PM2.5.

Samples a gridded satellite-derived annual-mean PM2.5 surface (provide-once raster;
see config.PM25_GRID_URL / PM25_RASTER) at each town centroid. Long-term mean PM2.5
is the standard chronic-exposure metric in air-pollution epidemiology and reflects
persistent particulate pollution rather than episodic wildfire smoke — so it is a
genuinely independent axis from local burn hazard (sampling.wildfire / score_wildfire),
which the former EPA-AQS smoke-days metric largely duplicated. Scored as score_air_quality.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from . import fetch
from . import sampling


def _sample_netcdf(lon: np.ndarray, lat: np.ndarray, nc_path) -> np.ndarray:
    """Pointwise nearest-neighbour sample of an ACAG PM2.5 NetCDF (regular WGS84
    lat/lon grid, e.g. the V5.NA / North-America 'GWRPM25' surface)."""
    import xarray as xr

    ds = xr.open_dataset(nc_path)
    try:
        name = ("GWRPM25" if "GWRPM25" in ds.data_vars
                else next((v for v in ds.data_vars if "PM25" in v.upper()), None)
                or (list(ds.data_vars)[0] if len(ds.data_vars) == 1 else None))
        if name is None:
            raise RuntimeError(
                f"PM2.5 NetCDF {getattr(nc_path, 'name', nc_path)}: cannot identify the "
                f"PM2.5 variable among {list(ds.data_vars)}.")
        da = ds[name]
        qlon = xr.DataArray(np.asarray(lon, dtype="float64"), dims="pts")
        qlat = xr.DataArray(np.asarray(lat, dtype="float64"), dims="pts")
        samp = da.sel(lon=qlon, lat=qlat, method="nearest")
        pm = np.asarray(samp.values, dtype="float64")
        # `nearest` always snaps to an edge cell, so flag points that fell outside the
        # grid footprint (e.g. south of ~25°N) by distance to their snapped cell.
        cell = float(abs(ds["lat"].values[1] - ds["lat"].values[0]))
        outside = ((np.abs(samp["lon"].values - np.asarray(lon)) > 2 * cell)
                   | (np.abs(samp["lat"].values - np.asarray(lat)) > 2 * cell))
        pm[outside] = np.nan
        return pm
    finally:
        ds.close()


def attach_air_quality(df: pd.DataFrame) -> pd.DataFrame:
    """Attach raw_annual_pm25_ugm3 (gridded annual-mean PM2.5, µg/m³). Accepts either a
    GeoTIFF (sampled via rasterio) or a NetCDF (sampled via xarray)."""
    out = df.copy()
    grid = fetch.fetch_pm25_grid()
    lon, lat = df["lon"].to_numpy(), df["lat"].to_numpy()

    if str(grid).lower().endswith(".nc"):
        pm = _sample_netcdf(lon, lat, grid)
    else:
        pm = sampling.sample_raster(lon, lat, grid)
        # Coastal centroids can land on an ocean/nodata cell; backfill those from a
        # small neighborhood of valid land cells before deciding the sample failed.
        missing = np.isnan(pm)
        if missing.any():
            nb = sampling.sample_raster_neighborhood(
                lon[missing], lat[missing], grid,
                radius_m=C.PM25_NEIGHBORHOOD_M, n_side=C.PM25_NEIGHBORHOOD_PTS, agg="mean")
            pm[missing] = nb

    nan_frac = float(np.isnan(pm).mean())
    if nan_frac > 0.5:
        raise RuntimeError(
            f"Air quality: {nan_frac:.0%} of towns sampled empty from {raster}. The "
            f"PM2.5 raster is likely missing/corrupt or in an unexpected CRS/units."
        )
    print(f"[air_quality] annual-mean PM2.5 sampled: "
          f"range {np.nanmin(pm):.1f}-{np.nanmax(pm):.1f} µg/m³")
    out["raw_annual_pm25_ugm3"] = pm
    return out
