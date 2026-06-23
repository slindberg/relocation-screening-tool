"""
Build the base table of candidate towns: parse the Census Gazetteer, filter to
the lower 48, attach ACS population, spatially join each centroid to its county
(name + 5-digit FIPS), and compute distance-to-coast for the continentality proxy.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
import requests

from . import config as C
from . import fetch

# Census place class descriptors are written in lowercase ("Phoenix city",
# "Santa Fe city"), while proper-noun parts stay capitalised ("Carson City").
# Stripping only a trailing *lowercase* descriptor cleans the display name without
# mangling places whose actual name ends in City/Town.
_CLASS = (r"(?:city and borough|consolidated government|unified government|"
          r"metropolitan government|metro government|charter township|"
          r"municipality|township|borough|village|plantation|comunidad|"
          r"zona urbana|city|town|CDP|gore|grant|reservation)")


def _clean_name(n: str) -> str:
    n = str(n).strip()
    n = re.sub(r"\s*\(balance\)$", "", n)
    n = re.sub(rf"\s+{_CLASS}$", "", n)  # case-sensitive: lowercase descriptor only
    return n.strip()


def load_places() -> pd.DataFrame:
    """Census Gazetteer places -> base DataFrame with keys (no scores yet)."""
    path = fetch.fetch_gazetteer()
    df = pd.read_csv(path, sep="\t", dtype={"GEOID": str}, encoding="latin-1")
    df.columns = [c.strip() for c in df.columns]  # gazetteer pads header names
    df = df.rename(
        columns={
            "USPS": "state",
            "GEOID": "place_geoid",
            "NAME": "name",
            "INTPTLAT": "lat",
            "INTPTLONG": "lon",
            "ALAND_SQMI": "land_area_sqmi",
        }
    )
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df["name"] = df["name"].map(_clean_name)
    df = df[~df["state"].isin(C.EXCLUDE_USPS)]
    df = df.dropna(subset=["lat", "lon"]).copy()
    df["place_geoid"] = df["place_geoid"].str.zfill(7)
    return df[["place_geoid", "name", "state", "lat", "lon", "land_area_sqmi"]]


def attach_population(df: pd.DataFrame) -> pd.DataFrame:
    """Join 2020 decennial total population by place GEOID. Population may be null
    for a few unmatched places; that is allowed (it is a key, not a score).

    The Census data API now requires a free key. If CENSUS_API_KEY is unset we skip
    population (leave it null) rather than fail — get a key at
    https://api.census.gov/data/key_signup.html and add it to .env to populate it."""
    if not C.CENSUS_API_KEY:
        print("[places] CENSUS_API_KEY not set; leaving population null "
              "(free key: https://api.census.gov/data/key_signup.html)")
        df = df.copy()
        df["population"] = pd.NA
        return df
    try:
        url = C.POP_URL + f"&key={C.CENSUS_API_KEY}"
        r = requests.get(url, timeout=120)
        ctype = r.headers.get("Content-Type", "")
        if r.status_code != 200 or "json" not in ctype.lower():
            raise RuntimeError(f"HTTP {r.status_code}, type={ctype}: {r.text[:160]!r}")
        rows = r.json()
        pop = pd.DataFrame(rows[1:], columns=rows[0])
        pop["place_geoid"] = (pop["state"].str.zfill(2) + pop["place"].str.zfill(5))
        pop["population"] = pd.to_numeric(pop[C.POP_VAR], errors="coerce")
        df = df.merge(pop[["place_geoid", "population"]], on="place_geoid", how="left")
    except Exception as exc:  # unreachable -> leave null, flagged in metadata
        print(f"[places] population unavailable ({exc}); leaving null")
        df["population"] = pd.NA
    return df


def attach_elevation(df: pd.DataFrame) -> pd.DataFrame:
    """Centroid elevation (feet) sampled from Copernicus DEM GLO-90 (90 m) 1°×1° COG
    tiles on the AWS Open Data bucket. Towns are grouped by the tile that contains
    them, so only the few hundred tiles with towns are downloaded (and cached under
    data/raw/elevation/) — no rate-limited API, no full-CONUS mosaic. A key column;
    towns whose tile can't be fetched are left null (and flagged by the coverage
    smoke test) rather than failing the run."""
    import math
    from . import sampling

    lats = df["lat"].to_numpy(dtype="float64")
    lons = df["lon"].to_numpy(dtype="float64")
    elev_m = np.full(len(df), np.nan)

    # Bucket town row-indices by the 1°×1° DEM tile (lower-left integer corner).
    tiles: dict[tuple[int, int], list[int]] = {}
    for i in range(len(df)):
        tiles.setdefault((math.floor(lats[i]), math.floor(lons[i])), []).append(i)

    missing_tiles = 0
    for (lat0, lon0), idxs in tiles.items():
        tile_path = fetch.fetch_dem_tile(lat0, lon0)
        if tile_path is None:
            missing_tiles += 1
            continue
        ii = np.asarray(idxs)
        # DEM tiles are EPSG:4326, so sample_raster samples lon/lat with no reprojection.
        elev_m[ii] = sampling.sample_raster(lons[ii], lats[ii], tile_path)

    out = df.copy()
    out["elevation_ft"] = np.round(elev_m * 3.28084)
    got = int(np.isfinite(elev_m).sum())
    print(f"[elevation] {len(tiles)} DEM tiles for {len(df):,} towns; "
          f"resolved {got}/{len(df)}"
          + (f"; {missing_tiles} tile(s) unavailable" if missing_tiles else ""))
    return out


def attach_county(df: pd.DataFrame) -> pd.DataFrame:
    """Spatial-join centroids to TIGER counties -> county name + 5-digit FIPS.

    Points are created in WGS84 and the county layer is reprojected to match, so
    there is no CRS mismatch (the #1 cause of a sample landing in the wrong state).
    Centroids that fall just outside any polygon (coastal rounding) get a
    nearest-county fallback.
    """
    import geopandas as gpd

    counties = gpd.read_file(fetch.fetch_counties())[["GEOID", "NAME", "geometry"]]
    counties = counties.rename(columns={"GEOID": "county_fips", "NAME": "county"})
    counties = counties.to_crs(C.WGS84)

    pts = gpd.GeoDataFrame(
        df.copy(),
        geometry=gpd.points_from_xy(df["lon"], df["lat"]),
        crs=C.WGS84,
    )
    joined = gpd.sjoin(pts, counties, predicate="within", how="left").drop(
        columns=["index_right"]
    )

    missing = joined["county_fips"].isna()
    if missing.any():
        # nearest county for the few unmatched coastal/border points
        near = gpd.sjoin_nearest(
            pts[missing.values].to_crs(C.EQUAL_AREA_CRS),
            counties.to_crs(C.EQUAL_AREA_CRS),
            how="left",
        ).drop(columns=["index_right"])
        joined.loc[missing, "county_fips"] = near["county_fips"].values
        joined.loc[missing, "county"] = near["county"].values

    out = pd.DataFrame(joined.drop(columns="geometry"))
    return out
