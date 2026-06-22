"""
Airport proximity — great-circle distance to the nearest US large/medium commercial
airport (OurAirports). Closer = better air-travel access.

We filter OurAirports to US airports of type large_airport/medium_airport with
scheduled commercial service (the spec's "large + medium hubs"), then take the
nearest-airport distance via a KD-tree on an equal-area projection.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from . import fetch


def _hub_airports() -> pd.DataFrame:
    df = pd.read_csv(fetch.fetch_airports(), dtype=str, low_memory=False)
    df = df[(df["iso_country"] == "US")
            & (df["type"].isin(C.AIRPORT_TYPES))
            & (df["scheduled_service"].str.lower() == "yes")].copy()
    df["lat"] = pd.to_numeric(df["latitude_deg"], errors="coerce")
    df["lon"] = pd.to_numeric(df["longitude_deg"], errors="coerce")
    df = df.dropna(subset=["lat", "lon"])
    code = df["iata_code"].fillna("").where(df["iata_code"].notna(), df["ident"])
    df["code"] = code.where(code.str.len() > 0, df["ident"])
    return df[["lat", "lon", "code"]].reset_index(drop=True)


def attach_airport(df: pd.DataFrame) -> pd.DataFrame:
    from pyproj import Transformer
    from scipy.spatial import cKDTree

    hubs = _hub_airports()
    print(f"[airport] {len(hubs):,} US large/medium commercial airports")
    tr = Transformer.from_crs(C.WGS84, C.EQUAL_AREA_CRS, always_xy=True)
    ax, ay = tr.transform(hubs["lon"].to_numpy(), hubs["lat"].to_numpy())
    tree = cKDTree(np.c_[ax, ay])

    tx, ty = tr.transform(df["lon"].to_numpy(), df["lat"].to_numpy())
    dist_m, idx = tree.query(np.c_[tx, ty], k=1)

    out = df.copy()
    out["raw_dist_to_airport_mi"] = dist_m / 1609.344
    out["raw_nearest_airport_iata"] = hubs["code"].to_numpy()[idx]
    return out
