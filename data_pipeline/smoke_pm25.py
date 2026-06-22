"""
Observed wildfire-smoke exposure from EPA AQS PM2.5.

For each AQS monitor, count days per year whose 24-hour PM2.5 mean exceeds the
threshold (default 35 µg/m³), averaged over recent years -> smoke-days/yr. Then
assign each town its nearest monitor's smoke-days/yr (distance recorded as context).

This is observed air quality, so it captures transported smoke (e.g. the 2023
Canadian-wildfire episodes over the East/Midwest), which the local burn-probability
hazard (sampling.wildfire) cannot. Scored separately as score_smoke.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from . import fetch

_USECOLS = ["State Code", "County Code", "Site Num",
            "Latitude", "Longitude", "Date Local", "Arithmetic Mean"]


def _monitor_smoke_days() -> pd.DataFrame:
    """One row per AQS monitor: lat, lon, mean smoke-days/yr over AQS_YEARS."""
    site_year = []   # (site, year, days, lat, lon)
    for param in C.AQS_PARAMS:
        for year in C.AQS_YEARS:
            try:
                path = fetch.fetch_aqs_daily(param, year)
            except Exception as exc:
                print(f"[smoke] {param}/{year} unavailable ({exc}); skipping")
                continue
            df = pd.read_csv(path, usecols=_USECOLS,
                             dtype={"State Code": str, "County Code": str, "Site Num": str})
            df["site"] = (df["State Code"].str.zfill(2) + df["County Code"].str.zfill(3)
                          + df["Site Num"].str.zfill(4))
            df["val"] = pd.to_numeric(df["Arithmetic Mean"], errors="coerce")
            # Max 24-hr mean per site-day (collapses multiple POCs / standards), then
            # count exceedance days for the year.
            day_max = df.groupby(["site", "Date Local"])["val"].max()
            exdays = (day_max > C.PM25_SMOKE_THRESHOLD).groupby("site").sum()
            coords = df.groupby("site")[["Latitude", "Longitude"]].first()
            sub = coords.assign(days=exdays).reset_index()
            sub["days"] = sub["days"].fillna(0)
            sub["year"] = year
            site_year.append(sub)

    if not site_year:
        return pd.DataFrame(columns=["site", "lat", "lon", "smoke_days"])

    allsy = pd.concat(site_year, ignore_index=True)
    # A site reporting under both params in one year -> take the max exceedance count.
    sy = allsy.groupby(["site", "year"]).agg(
        days=("days", "max"),
        Latitude=("Latitude", "first"),
        Longitude=("Longitude", "first"),
    ).reset_index()
    mon = sy.groupby("site").agg(
        smoke_days=("days", "mean"),       # average over the years the site reported
        lat=("Latitude", "first"),
        lon=("Longitude", "first"),
    ).reset_index()
    return mon


def attach_smoke(df: pd.DataFrame) -> pd.DataFrame:
    """Attach raw_smoke_days_per_yr + raw_nearest_pm25_monitor_km (nearest AQS monitor)."""
    import geopandas as gpd

    mon = _monitor_smoke_days()
    if mon.empty:
        raise RuntimeError("[smoke] no EPA AQS PM2.5 data could be downloaded.")
    print(f"[smoke] {len(mon):,} AQS monitors; "
          f"mean smoke-days/yr range {mon['smoke_days'].min():.0f}-{mon['smoke_days'].max():.0f}")

    mg = gpd.GeoDataFrame(
        mon, geometry=gpd.points_from_xy(mon["lon"], mon["lat"]), crs=C.WGS84
    ).to_crs(C.EQUAL_AREA_CRS)
    pts = gpd.GeoDataFrame(
        df.copy(), geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs=C.WGS84
    ).to_crs(C.EQUAL_AREA_CRS)

    near = gpd.sjoin_nearest(pts, mg[["smoke_days", "geometry"]], how="left",
                             distance_col="_dist_m")
    near = near[~near.index.duplicated(keep="first")]

    out = df.copy()
    out["raw_smoke_days_per_yr"] = near["smoke_days"].to_numpy()
    out["raw_nearest_pm25_monitor_km"] = near["_dist_m"].to_numpy() / 1000.0
    return out
