"""
Politics criterion: local 2024 presidential lean, precinct level.

Each town centroid is spatially joined to the precinct that contains it (NYT national
2024 precinct-results file), and the raw metric is the Democratic share of the
two-party vote, Dem / (Dem + Rep). Precinct-level rather than county so a left-leaning
town inside a right-leaning county is captured. Scored (percentile, higher = more
Democratic-voting) as score_politics.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from . import config as C
from . import fetch


def _load_precincts(path):
    """Load the precinct file into a GeoDataFrame (EPSG:4326) with Dem/Rep vote columns.
    Reads geo formats via geopandas (incl. gzipped .topojson.gz/.geojson.gz through GDAL's
    /vsigzip/), or a CSV[.gz] with a WKT geometry column."""
    import geopandas as gpd

    p = str(path)
    name = p.lower()
    if name.endswith(".csv") or name.endswith(".csv.gz"):
        df = pd.read_csv(path, compression="infer", low_memory=False)
        geom_col = next((c for c in df.columns
                         if c.lower() in ("geometry", "wkt", "the_geom", "geom")), None)
        if geom_col is None:
            raise RuntimeError(
                f"politics: no geometry/WKT column in {os.path.basename(p)} — the NYT "
                f"'precincts-with-results.csv.gz' is results-only. Use the geometry-bearing "
                f"'precincts-with-results.topojson.gz' instead. Columns: {list(df.columns)[:20]}")
        gdf = gpd.GeoDataFrame(df, geometry=gpd.GeoSeries.from_wkt(df[geom_col]),
                               crs="EPSG:4326")
    else:
        # GDAL reads .gz geo files in place via the /vsigzip/ virtual filesystem.
        src = ("/vsigzip/" + os.path.abspath(p)) if name.endswith(".gz") else p
        gdf = gpd.read_file(src)
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        elif gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs("EPSG:4326")
    return gdf


def _pick_col(columns, candidates) -> str | None:
    low = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in low:
            return low[cand.lower()]
    return None


def attach_politics(df: pd.DataFrame) -> pd.DataFrame:
    """Attach raw_dem_two_party_pct: Democratic share of the two-party 2024
    presidential vote in each town's precinct (spatial join)."""
    import geopandas as gpd

    precincts = _load_precincts(fetch.fetch_politics_precincts())
    dem_col = _pick_col(precincts.columns, C.POLITICS_DEM_COLS)
    rep_col = _pick_col(precincts.columns, C.POLITICS_REP_COLS)
    if dem_col is None or rep_col is None:
        raise RuntimeError(
            f"politics: could not find Dem/Rep vote columns in {list(precincts.columns)[:25]}. "
            f"Set POLITICS_DEM_COLS / POLITICS_REP_COLS in config to match the file.")

    dem = pd.to_numeric(precincts[dem_col], errors="coerce")
    rep = pd.to_numeric(precincts[rep_col], errors="coerce")
    two_party = dem + rep
    precincts = precincts.assign(
        _dem_pct=np.where(two_party > 0, 100.0 * dem / two_party, np.nan))
    precincts = precincts[precincts.geometry.notna()][["_dem_pct", "geometry"]]

    pts = gpd.GeoDataFrame(
        {"_row": np.arange(len(df))},
        geometry=gpd.points_from_xy(df["lon"].to_numpy(), df["lat"].to_numpy()),
        crs="EPSG:4326",
    )
    joined = gpd.sjoin(pts, precincts, predicate="within", how="left")
    # A point on a shared edge can match >1 precinct; keep the first per town.
    joined = joined.drop_duplicates("_row", keep="first").sort_values("_row")

    out = df.copy()
    out["raw_dem_two_party_pct"] = joined["_dem_pct"].to_numpy()
    got = int(np.isfinite(out["raw_dem_two_party_pct"]).sum())
    print(f"[politics] {len(precincts):,} precincts; matched {got}/{len(df):,} towns "
          f"to a precinct")
    return out
