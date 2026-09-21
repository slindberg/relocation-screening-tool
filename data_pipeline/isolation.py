"""
Isolation / regional remoteness — how few people are within reach of a town.

Two measures, computed from the places+population table already in hand plus the
Copernicus DEM (no new downloads beyond the elevation tiles), via a KD-tree on an
equal-area projection:

  - terrain-aware distance-weighted nearby population: every place within a
    straight-line cutoff contributes pop * exp(-(d_eff / bandwidth)^2), where d_eff is
    the straight-line distance inflated by the elevation change along the path,
  - effective distance to the nearest "sizable city" (population >= CITY_POP_THRESHOLD),
    likewise terrain-adjusted.

Why terrain: straight-line distance treats a town 40 km from LA as adjacent to 13M
people even when the San Gabriels sit in between, which made genuinely isolated-feeling
mountain towns score as suburbs. Inflating distance by the climb along the path (see
terrain.py) pushes a metro behind a ridge out of the kernel, while leaving a flat
suburb 40 km from the same metro essentially unchanged.

Population-in-reach is a better remoteness signal than a town's own density: a
low-density bedroom suburb inside a metro still has a large nearby population, so it is
correctly *not* isolated. Place density is kept as a context raw.

The reference universe is all places nationally. For a normal (full) run the scored df
already is that universe; for a small subset (--anchors-only) the full universe is
rebuilt so the regional sums are still correct.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from . import places
from . import terrain


def attach_isolation(df: pd.DataFrame) -> pd.DataFrame:
    from pyproj import Transformer
    from scipy.spatial import cKDTree

    universe = df
    if len(df) < 1000:  # subset run: rebuild the national reference set
        u = places.load_places()
        u = places.attach_population(u)
        universe = u

    grid = None
    if C.ISOLATION_TERRAIN:
        try:
            grid = terrain.conus_grid()
        except Exception as exc:
            print(f"[isolation] terrain grid unavailable ({exc}); "
                  f"falling back to straight-line distance")

    tr = Transformer.from_crs(C.WGS84, C.EQUAL_AREA_CRS, always_xy=True)
    ulon = universe["lon"].to_numpy(dtype="float64")
    ulat = universe["lat"].to_numpy(dtype="float64")
    ux, uy = tr.transform(ulon, ulat)
    ux, uy = np.asarray(ux), np.asarray(uy)
    upop = pd.to_numeric(universe["population"], errors="coerce").fillna(0).to_numpy()

    dlon = df["lon"].to_numpy(dtype="float64")
    dlat = df["lat"].to_numpy(dtype="float64")
    dx, dy = tr.transform(dlon, dlat)
    pts = np.c_[np.asarray(dx), np.asarray(dy)]
    n = len(pts)

    # ---- Terrain-aware gravity sum of nearby population --------------------------- #
    tree = cKDTree(np.c_[ux, uy])
    bw_m = C.ISOLATION_DECAY_BW_KM * 1000.0
    cutoff_m = C.ISOLATION_DECAY_CUTOFF_KM * 1000.0
    neigh = tree.query_ball_point(pts, r=cutoff_m)

    counts = np.fromiter((len(x) for x in neigh), dtype=np.int64, count=n)
    src = np.repeat(np.arange(n, dtype=np.int64), counts)
    dst = (np.concatenate([np.asarray(x, dtype=np.int64) for x in neigh if len(x)])
           if counts.sum() else np.empty(0, dtype=np.int64))

    horiz = np.hypot(ux[dst] - pts[src, 0], uy[dst] - pts[src, 1])
    eff = terrain.effective_distance_m(horiz, dlon[src], dlat[src],
                                       ulon[dst], ulat[dst], grid)
    contrib = upop[dst] * np.exp(-((eff / bw_m) ** 2))
    weighted_pop = np.bincount(src, weights=contrib, minlength=n)

    # ---- Effective distance to the nearest sizable city --------------------------- #
    city = upop >= C.CITY_POP_THRESHOLD
    if city.any():
        cidx = np.flatnonzero(city)
        ctree = cKDTree(np.c_[ux[cidx], uy[cidx]])
        k = int(min(C.ISOLATION_CITY_CANDIDATES, len(cidx)))
        cd_m, ck = ctree.query(pts, k=k)
        if k == 1:                       # cKDTree drops the k axis when k == 1
            cd_m = cd_m[:, None]
            ck = ck[:, None]
        cand = cidx[ck]                                     # (n, k) universe indices
        flat_src = np.repeat(np.arange(n, dtype=np.int64), k)
        flat_dst = cand.ravel()
        eff_city = terrain.effective_distance_m(
            cd_m.ravel(), dlon[flat_src], dlat[flat_src],
            ulon[flat_dst], ulat[flat_dst], grid)
        eff_city_km = (eff_city.reshape(n, k) / 1000.0).min(axis=1)
        dist_city_km = cd_m.min(axis=1) / 1000.0
    else:
        eff_city_km = np.full(n, np.nan)
        dist_city_km = np.full(n, np.nan)

    if grid is not None:
        infl = np.divide(eff_city_km, dist_city_km,
                         out=np.ones_like(eff_city_km), where=dist_city_km > 0)
        print(f"[isolation] terrain-aware: nearest-city distance inflated "
              f"x{np.nanmedian(infl):.2f} (median), x{np.nanmax(infl):.1f} (max)")

    out = df.copy()
    out["raw_weighted_pop_nearby"] = weighted_pop
    out["raw_eff_dist_to_city_km"] = eff_city_km
    out["raw_dist_to_city_km"] = dist_city_km
    pop = pd.to_numeric(df["population"], errors="coerce")
    area = pd.to_numeric(df["land_area_sqmi"], errors="coerce")
    out["raw_place_density_per_sqmi"] = (pop / area).replace([np.inf, -np.inf], np.nan)
    return out
