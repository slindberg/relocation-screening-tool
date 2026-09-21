"""
Terrain-aware effective distance.

Straight-line distance calls a town 40 km from a large metro "close" even when a
mountain range sits in between — which distorted the isolation score (towns over the
San Gabriels still counted all of LA as nearby). This module inflates a straight-line
distance by the elevation change along the path, so crossing a range costs extra
"effective kilometres":

    effective_d = horizontal_d + TERRAIN_PENALTY_M_PER_M * Σ|Δelevation| along the path

The penalty factor is a Naismith-style horizontal-equivalent for climb (a metre of
up-or-down costs roughly TERRAIN_PENALTY_M_PER_M metres of flat travel). It is a
*proxy* for travel friction, not a routed drive time: it follows the straight line, so
it can overstate a barrier a road skirts easily, and understate a winding canyon route.

Elevation comes from a coarse CONUS grid assembled once from the Copernicus DEM GLO-90
tiles already cached for the `elevation_ft` column, then stored under data/work/ so the
build happens only on the first run.
"""
from __future__ import annotations

import numpy as np

from . import config as C
from . import fetch


def _grid_path():
    return C.DATA_WORK / f"conus_dem_grid_{C.TERRAIN_GRID_DEG:g}.npz"


def conus_grid(force: bool = False):
    """Return (elev, north, west, res): a coarse CONUS elevation grid (metres, float32,
    north-up) plus its georeferencing. Built once from the DEM tiles and cached."""
    path = _grid_path()
    if path.exists() and not force:
        z = np.load(path)
        return z["elev"], float(z["north"]), float(z["west"]), float(z["res"])

    import rasterio
    from rasterio.enums import Resampling

    res = float(C.TERRAIN_GRID_DEG)
    south, west, north, east = C.CONUS_BBOX          # (S, W, N, E)
    rows = int(round((north - south) / res))
    cols = int(round((east - west) / res))
    per = int(round(1.0 / res))                       # grid cells per 1° tile
    elev = np.zeros((rows, cols), dtype="float32")

    lat_range = range(int(np.floor(south)), int(np.ceil(north)))
    lon_range = range(int(np.floor(west)), int(np.ceil(east)))
    total = len(lat_range) * len(lon_range)
    got = 0
    print(f"[terrain] building {rows}x{cols} CONUS elevation grid "
          f"({total} DEM tiles to check; cached afterwards) ...")
    seen = 0
    for lat0 in lat_range:
        for lon0 in lon_range:
            seen += 1
            if seen % 250 == 0:
                print(f"[terrain]   {seen}/{total} tiles checked, {got} loaded")
            tile = fetch.fetch_dem_tile(lat0, lon0)
            if tile is None:                          # ocean / unavailable → sea level
                continue
            try:
                with rasterio.open(tile) as src:
                    a = src.read(1, out_shape=(per, per), resampling=Resampling.average)
            except Exception as exc:
                print(f"[terrain]   tile {lat0},{lon0} unreadable ({exc}); skipped")
                continue
            a = np.asarray(a, dtype="float32")
            a[~np.isfinite(a)] = 0.0
            a[a < -1000.0] = 0.0                      # nodata guard
            r0 = int(round((north - (lat0 + 1)) / res))
            c0 = int(round((lon0 - west) / res))
            r1, c1 = r0 + per, c0 + per
            if r1 <= 0 or c1 <= 0 or r0 >= rows or c0 >= cols:
                continue
            rs, re_ = max(r0, 0), min(r1, rows)
            cs, ce = max(c0, 0), min(c1, cols)
            elev[rs:re_, cs:ce] = a[rs - r0:re_ - r0, cs - c0:ce - c0]
            got += 1

    if got == 0:
        raise RuntimeError("terrain: no DEM tiles could be loaded for the CONUS grid")
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, elev=elev, north=north, west=west, res=res)
    print(f"[terrain] grid built from {got} tiles -> {path.name}")
    return elev, north, west, res


def path_climb_m(lon_a, lat_a, lon_b, lat_b, grid) -> np.ndarray:
    """Total absolute elevation change (m) along the straight line a->b, sampled from
    the coarse grid. Vectorized over arrays of endpoint coordinates."""
    elev, north, west, res = grid
    n_s = int(C.TERRAIN_PATH_SAMPLES)
    t = np.linspace(0.0, 1.0, n_s, dtype="float32")
    lon_a = np.asarray(lon_a, dtype="float32")
    lat_a = np.asarray(lat_a, dtype="float32")
    lon_b = np.asarray(lon_b, dtype="float32")
    lat_b = np.asarray(lat_b, dtype="float32")

    lons = lon_a[:, None] + (lon_b - lon_a)[:, None] * t[None, :]
    lats = lat_a[:, None] + (lat_b - lat_a)[:, None] * t[None, :]
    r = np.clip(((north - lats) / res).astype(np.int32), 0, elev.shape[0] - 1)
    c = np.clip(((lons - west) / res).astype(np.int32), 0, elev.shape[1] - 1)
    z = elev[r, c]
    return np.abs(np.diff(z, axis=1)).sum(axis=1).astype("float64")


def effective_distance_m(horiz_m, lon_a, lat_a, lon_b, lat_b, grid,
                         chunk: int = 250_000) -> np.ndarray:
    """Terrain-inflated distance (m): horizontal + penalty × Σ|Δelevation| on the path.
    Returns `horiz_m` unchanged when no grid is available. Chunked to bound memory."""
    horiz_m = np.asarray(horiz_m, dtype="float64")
    if grid is None or horiz_m.size == 0:
        return horiz_m
    out = np.empty_like(horiz_m)
    for s in range(0, horiz_m.size, chunk):
        e = min(s + chunk, horiz_m.size)
        climb = path_climb_m(lon_a[s:e], lat_a[s:e], lon_b[s:e], lat_b[s:e], grid)
        out[s:e] = horiz_m[s:e] + C.TERRAIN_PENALTY_M_PER_M * climb
    return out
