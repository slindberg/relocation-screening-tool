"""
Amenity access, proxied by distance to the nearest Costco warehouse.

Costco locations come from OpenStreetMap (Overpass, brand:wikidata = Costco). One
warehouse is a decent single proxy for "near everyday big-box retail / a sizable
commercial center". Great-circle distance to the nearest one; closer = better.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C
from . import fetch


def _costco_locations() -> pd.DataFrame:
    js = json.loads(Path(fetch.fetch_costco()).read_text())
    s, w, n, e = C.CONUS_BBOX
    lats, lons = [], []
    for el in js.get("elements", []):
        lat, lon = el.get("lat"), el.get("lon")
        if lat is None:
            c = el.get("center")
            if c:
                lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        if s <= lat <= n and w <= lon <= e:
            lats.append(lat)
            lons.append(lon)
    # OSM returns the same warehouse as a node + building way (+ relation), each a
    # separate element ~tens of metres apart. Dedup at ~0.02° (~1.5 km) so co-located
    # elements collapse to one store while genuinely distinct Costcos stay separate.
    df = pd.DataFrame({"lat": lats, "lon": lons})
    df = df.assign(_la=(df["lat"] / 0.02).round(), _lo=(df["lon"] / 0.02).round())
    df = df.drop_duplicates(subset=["_la", "_lo"])
    return df[["lat", "lon"]].reset_index(drop=True)


def attach_amenity(df: pd.DataFrame) -> pd.DataFrame:
    from pyproj import Transformer
    from scipy.spatial import cKDTree

    locs = _costco_locations()
    if locs.empty:
        raise RuntimeError("[amenities] no Costco locations parsed from OSM.")
    print(f"[amenities] {len(locs):,} Costco warehouses (CONUS)")
    tr = Transformer.from_crs(C.WGS84, C.EQUAL_AREA_CRS, always_xy=True)
    cx, cy = tr.transform(locs["lon"].to_numpy(), locs["lat"].to_numpy())
    tree = cKDTree(np.c_[cx, cy])

    tx, ty = tr.transform(df["lon"].to_numpy(), df["lat"].to_numpy())
    dist_m, _ = tree.query(np.c_[tx, ty], k=1)

    out = df.copy()
    out["raw_dist_to_costco_mi"] = dist_m / 1609.344
    return out
