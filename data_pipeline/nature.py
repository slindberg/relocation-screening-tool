"""
Nature access — proximity to large protected/wild areas (PAD-US) and natural
land-cover fraction around the town (NLCD).

  - raw_dist_to_protected_km: distance to the nearest PAD-US GAP-Status 1/2 (managed
    for biodiversity: wilderness, park cores, refuges) polygon larger than
    PROTECTED_MIN_SQKM.
  - raw_natural_cover_pct: % of land in NLCD natural classes (forest/shrub/grass/
    wetland) among sampled points within NATURE_COVER_RADIUS_M (water/nodata excluded
    from the denominator).

score_nature_access (in scoring.py) rewards being close to protected land AND having
lots of natural cover nearby.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from . import fetch


def _protected_areas():
    """GeoDataFrame (EPSG:5070) of large GAP 1/2 protected polygons, from the cached
    PAD-US feature-service GeoJSON (already filtered to GAP 1/2 and >= size threshold)."""
    import geopandas as gpd

    path = fetch.fetch_padus_geojson()
    gdf = gpd.read_file(path)            # GeoJSON is EPSG:4326
    if gdf.crs is None:
        gdf = gdf.set_crs(C.WGS84)
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()].to_crs(C.EQUAL_AREA_CRS)
    gdf["geometry"] = gdf.geometry.buffer(0)     # repair invalid rings
    return gdf[["geometry"]].reset_index(drop=True)


def _natural_cover_fraction(lon, lat, raster_path) -> np.ndarray:
    """% natural land cover among a grid of points within the radius (per town)."""
    import rasterio
    from pyproj import Transformer

    natural = np.array(C.NLCD_NATURAL_CLASSES)
    exclude = np.array(C.NLCD_WATER_NODATA)
    lon = np.asarray(lon, dtype="float64")
    lat = np.asarray(lat, dtype="float64")
    out = np.full(len(lon), np.nan)
    with rasterio.open(raster_path) as src:
        if src.crs is not None and src.crs.to_epsg() != 4326:
            tr = Transformer.from_crs(C.WGS84, src.crs, always_xy=True)
            cx, cy = tr.transform(lon, lat)
        else:
            cx, cy = lon, lat
        cx, cy = np.asarray(cx), np.asarray(cy)
        nod = src.nodata
        offs = np.linspace(-C.NATURE_COVER_RADIUS_M, C.NATURE_COVER_RADIUS_M,
                           C.NATURE_COVER_PTS)
        gx, gy = np.meshgrid(offs, offs)
        gx, gy = gx.ravel(), gy.ravel()
        for s in range(0, len(lon), 1000):
            sl = slice(s, min(s + 1000, len(lon)))
            xs = cx[sl][:, None] + gx[None, :]
            ys = cy[sl][:, None] + gy[None, :]
            pts = np.column_stack([xs.ravel(), ys.ravel()])
            vals = np.array([v[0] for v in src.sample(pts)], dtype="float64")
            vals = vals.reshape(xs.shape)                  # (chunk, k)
            land = ~np.isin(vals, exclude)
            if nod is not None:
                land &= (vals != nod)
            is_nat = np.isin(vals, natural)
            land_n = land.sum(axis=1)
            nat_n = (is_nat & land).sum(axis=1)
            out[sl] = np.where(land_n > 0, nat_n / land_n * 100.0, np.nan)
    return out


def attach_nature_access(df: pd.DataFrame) -> pd.DataFrame:
    import geopandas as gpd

    prot = _protected_areas()
    pts = gpd.GeoDataFrame(
        df.copy(), geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs=C.WGS84
    ).to_crs(C.EQUAL_AREA_CRS)
    near = gpd.sjoin_nearest(pts, prot, how="left", distance_col="_dist_m")
    near = near[~near.index.duplicated(keep="first")]

    out = df.copy()
    out["raw_dist_to_protected_km"] = near["_dist_m"].to_numpy() / 1000.0
    out["raw_natural_cover_pct"] = _natural_cover_fraction(
        df["lon"].to_numpy(), df["lat"].to_numpy(), fetch.fetch_nlcd())
    return out
