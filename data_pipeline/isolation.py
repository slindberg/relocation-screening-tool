"""
Isolation / regional remoteness — how few people are around a town.

Two measures, both computed from the places+population table already in hand (no new
downloads), via a KD-tree on an equal-area projection:

  - population within ISOLATION_RADIUS_KM of the town (sum of nearby place pops),
  - distance to the nearest "sizable city" (place with population >= CITY_POP_THRESHOLD).

Population within a radius is the better remoteness signal than a town's own density:
a low-density bedroom suburb inside a metro still has a large nearby population, so it
is correctly *not* isolated. Place density is kept as a context raw.

The reference universe is all places nationally. For a normal (full) run the scored
df already is that universe; for a small subset (--anchors-only) the full universe is
rebuilt so the regional sums are still correct.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from . import places


def attach_isolation(df: pd.DataFrame) -> pd.DataFrame:
    from pyproj import Transformer
    from scipy.spatial import cKDTree

    universe = df
    if len(df) < 1000:  # subset run: rebuild the national reference set
        u = places.load_places()
        u = places.attach_population(u)
        universe = u

    tr = Transformer.from_crs(C.WGS84, C.EQUAL_AREA_CRS, always_xy=True)
    ux, uy = tr.transform(universe["lon"].to_numpy(), universe["lat"].to_numpy())
    upop = pd.to_numeric(universe["population"], errors="coerce").fillna(0).to_numpy()
    dx, dy = tr.transform(df["lon"].to_numpy(), df["lat"].to_numpy())
    pts = np.c_[dx, dy]

    # Population within the radius (includes the town and its neighbours).
    tree = cKDTree(np.c_[ux, uy])
    radius_m = C.ISOLATION_RADIUS_KM * 1000.0
    pop_within = np.array(
        [upop[ix].sum() for ix in tree.query_ball_point(pts, r=radius_m)],
        dtype="float64",
    )

    # Distance to the nearest sizable city.
    city = upop >= C.CITY_POP_THRESHOLD
    if city.any():
        ctree = cKDTree(np.c_[ux[city], uy[city]])
        dist_m, _ = ctree.query(pts, k=1)
        dist_city_km = dist_m / 1000.0
    else:
        dist_city_km = np.full(len(df), np.nan)

    out = df.copy()
    out["raw_pop_within_50km"] = pop_within
    out["raw_dist_to_city_km"] = dist_city_km
    pop = pd.to_numeric(df["population"], errors="coerce")
    area = pd.to_numeric(df["land_area_sqmi"], errors="coerce")
    out["raw_place_density_per_sqmi"] = (pop / area).replace([np.inf, -np.inf], np.nan)
    return out
