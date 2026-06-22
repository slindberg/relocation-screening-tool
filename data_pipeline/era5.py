"""
ERA5 hourly mean-sea-level pressure -> the two pressure sub-scores, replacing the
Phase-1 temperature proxies with directly-measured pressure variability.

  pressure_diurnal  = mean over days of (daily max MSLP - daily min MSLP)   [hPa/day]
  pressure_synoptic = std. dev. of daily-mean MSLP across all days          [hPa]

Method: download hourly MSLP for the CONUS box (chunked by year, cached), compute
both statistics once per ERA5 grid cell (vectorised over the whole grid), then
sample those two 2-D fields at each town centroid (nearest cell). 31k towns share
~25k cells, so per-cell-then-sample is far cheaper than per-town time series.

Requires a Copernicus CDS account + API token (see README / .env). The heavy part is
the download + CDS queue; processing the cached files is fast.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def _client():
    import cdsapi
    if C.CDS_KEY:
        return cdsapi.Client(url=C.CDS_URL, key=C.CDS_KEY)
    return cdsapi.Client()  # falls back to ~/.cdsapirc


def fetch_era5_year(year: str) -> Path:
    """Download one year of hourly CONUS MSLP as NetCDF (cached)."""
    dest = C.DATA_RAW / "era5" / f"era5_msl_{year}.nc"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = {
        "product_type": ["reanalysis"],
        "variable": ["mean_sea_level_pressure"],
        "year": [year],
        "month": [f"{m:02d}" for m in range(1, 13)],
        "day": [f"{d:02d}" for d in range(1, 32)],
        "time": [f"{h:02d}:00" for h in range(24)],
        "area": C.ERA5_AREA,          # [N, W, S, E]
        "data_format": "netcdf",
        "download_format": "unarchived",
    }
    tmp = dest.with_suffix(".nc.part")
    _client().retrieve(C.ERA5_DATASET, request, str(tmp))
    tmp.rename(dest)
    return dest


def fetch_era5() -> list[Path]:
    return [fetch_era5_year(y) for y in C.ERA5_YEARS]


# --------------------------------------------------------------------------- #
# Compute per-cell statistics
# --------------------------------------------------------------------------- #
def compute_pressure_fields(files: list[Path]):
    """Return an xarray.Dataset with 2-D 'diurnal' (mean daily range, hPa) and
    'synoptic' (std of daily-mean, hPa) fields on the ERA5 grid."""
    import xarray as xr

    ds = xr.open_mfdataset([str(f) for f in files], combine="by_coords",
                           chunks={"valid_time": 24}, engine="netcdf4")
    # New CDS NetCDF names the time axis 'valid_time'; older files use 'time'.
    tname = "valid_time" if "valid_time" in ds.dims else "time"
    msl = ds["msl"] / 100.0  # Pa -> hPa
    daily = msl.resample({tname: "1D"})
    diurnal = (daily.max() - daily.min()).mean(tname)
    synoptic = daily.mean().std(tname)
    out = xr.Dataset({"diurnal": diurnal, "synoptic": synoptic}).compute()
    return out


def sample_pressure(df: pd.DataFrame) -> pd.DataFrame:
    """Attach raw_pressure_diurnal_hpa and raw_pressure_synoptic_hpa to df."""
    import xarray as xr

    fields = compute_pressure_fields(fetch_era5())
    lat_name = "latitude" if "latitude" in fields.coords else "lat"
    lon_name = "longitude" if "longitude" in fields.coords else "lon"

    lons = df["lon"].to_numpy(dtype="float64")
    lats = df["lat"].to_numpy(dtype="float64")
    # ERA5 longitudes may be 0-360; convert query lons to match the grid.
    if float(fields[lon_name].max()) > 180.0:
        lons = np.where(lons < 0, lons + 360.0, lons)

    sel = {
        lon_name: xr.DataArray(lons, dims="pts"),
        lat_name: xr.DataArray(lats, dims="pts"),
    }
    di = fields["diurnal"].sel(**sel, method="nearest").to_numpy()
    sy = fields["synoptic"].sel(**sel, method="nearest").to_numpy()

    out = df.copy()
    out["raw_pressure_diurnal_hpa"] = di
    out["raw_pressure_synoptic_hpa"] = sy
    return out
