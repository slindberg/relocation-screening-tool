"""
Idempotent data fetchers. Every download is cached under data/raw/ so re-runs are
free. Nothing here runs in --sample mode.

If a source URL has rotated (CDC and the WRC archive occasionally re-issue paths),
drop the file into data/raw/ with the expected name and the pipeline will use it.
"""
from __future__ import annotations

import io
import os
import shutil
import time
import zipfile
from pathlib import Path

import requests

from . import config as C

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "relocation-screening/1.0"})


def _download(url: str, dest: Path, *, throttle: float = 0.0) -> Path:
    """Stream `url` to `dest` unless it already exists. Returns dest."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if throttle:
        time.sleep(throttle)
    with _SESSION.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
        tmp.rename(dest)
    return dest


def _download_zip_member(url: str, member_suffix: str, dest: Path) -> Path:
    """Download a zip and extract the first member ending in `member_suffix`."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    r = _SESSION.get(url, timeout=120)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        member = next(m for m in zf.namelist() if m.lower().endswith(member_suffix))
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(dest, "wb") as out:
            out.write(src.read())
    return dest


def _extract_zip(url: str, out_dir: Path, marker: str) -> Path:
    """Download+extract a whole zip (e.g. a shapefile set) into out_dir."""
    if (out_dir / marker).exists():
        return out_dir / marker
    r = _SESSION.get(url, timeout=300)
    r.raise_for_status()
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        zf.extractall(out_dir)
    return out_dir / marker


# --------------------------------------------------------------------------- #
# Census
# --------------------------------------------------------------------------- #
def fetch_gazetteer() -> Path:
    dest = C.DATA_RAW / "2023_Gaz_place_national.txt"
    return _download_zip_member(C.GAZETTEER_URL, ".txt", dest)


def fetch_counties() -> Path:
    """Returns the path to the extracted county .shp."""
    out_dir = C.DATA_RAW / f"tl_{C.VINTAGE_TIGER}_us_county"
    shp = f"tl_{C.VINTAGE_TIGER}_us_county.shp"
    _extract_zip(C.TIGER_COUNTY_URL, out_dir, shp)
    return out_dir / shp


# --------------------------------------------------------------------------- #
# PRISM normals
# --------------------------------------------------------------------------- #
def _prism_url(element: str, date: str) -> str:
    """Build a PRISM v2 normals direct-download URL.
    `date` = 'MMDD' (daily), 'MM' (monthly 01-12), or '14' (annual)."""
    if len(date) == 4:                 # daily MMDD
        time_scale, token = "daily", "2020" + date
    elif date == "14":                 # annual (served under monthly/)
        time_scale, token = "monthly", "2020"
    else:                              # monthly MM
        time_scale, token = "monthly", "2020" + date
    rescode = C.PRISM_RESCODE[C.PRISM_RES]
    fn = f"prism_{element}_{C.PRISM_REGION}_{rescode}_{token}_avg_30y.zip"
    return (f"{C.PRISM_NORMALS_BASE}/{C.PRISM_REGION}/{C.PRISM_RES}/"
            f"{element}/{time_scale}/{fn}")


_RASTER_EXTS = (".tif", ".tiff", ".bil")


def _find_raster(d: Path) -> Path | None:
    """First raster (COG .tif or .bil) anywhere under d, or None."""
    if not d.exists():
        return None
    for ext in _RASTER_EXTS:
        hits = sorted(d.rglob(f"*{ext}"))
        if hits:
            return hits[0]
    return None


def fetch_prism(element: str, date: str) -> Path:
    """
    Download one PRISM v2 normal and extract its raster. The v2 normals ship as
    Cloud-Optimized GeoTIFFs (.tif); older mirrors used .bil + .hdr. We extract the
    whole archive and return whichever raster it contains (rasterio reads both).
    `date` = 'MMDD' (daily), 'MM' (monthly), or '14' (annual).
    """
    tag = f"prism_{element}_{date}_{C.PRISM_RES}"
    out_dir = C.DATA_RAW / "prism" / tag
    cached = _find_raster(out_dir)
    if cached:
        return cached
    out_dir.mkdir(parents=True, exist_ok=True)
    url = _prism_url(element, date)
    content = _get_prism_zip(url, element, date)
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        zf.extractall(out_dir)
        members = zf.namelist()
    raster = _find_raster(out_dir)
    if raster is None:
        raise RuntimeError(
            f"PRISM {element}/{date}: no raster (.tif/.bil) in archive. "
            f"Members: {members}"
        )
    return raster


def _get_prism_zip(url: str, element: str, date: str, retries: int = 3) -> bytes:
    """GET a PRISM normal and return zip bytes, with retries and a clear error if
    the server returns something that is not a zip (HTML error, rate-limit text,
    redirect page, etc.)."""
    last = ""
    for attempt in range(retries):
        if attempt:
            time.sleep(5.0)  # back off only on retry; the data dir is a file server
        r = _SESSION.get(url, timeout=120, allow_redirects=True)
        if r.status_code == 200 and r.content[:2] == b"PK":
            return r.content
        ctype = r.headers.get("Content-Type", "?")
        body = r.content[:200]
        last = (f"HTTP {r.status_code} type={ctype} bytes={len(r.content)} "
                f"first200={body!r}")
    raise RuntimeError(
        f"PRISM {element}/{date} did not return a zip after {retries} tries.\n"
        f"  URL: {url}\n  Last response: {last}\n"
        f"  Run `python3 diagnose.py` and share the output so the endpoint can be "
        f"corrected."
    )


def fetch_prism_daily_tmax() -> list[Path]:
    """365 daily tmax normals (MMDD) for the comfort-day counts."""
    days = _all_mmdd()
    return [fetch_prism("tmax", d) for d in days]


def fetch_prism_monthly(element: str) -> list[Path]:
    """12 monthly normals (01..12) for an element."""
    return [fetch_prism(element, f"{m:02d}") for m in range(1, 13)]


def fetch_prism_annual(element: str) -> Path:
    return fetch_prism(element, "14")


def _all_mmdd() -> list[str]:
    # Day counts per month for a non-leap normal year (PRISM daily normals use 365).
    dim = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    out = []
    for mi, n in enumerate(dim, start=1):
        for d in range(1, n + 1):
            out.append(f"{mi:02d}{d:02d}")
    return out


# --------------------------------------------------------------------------- #
# Wildfire, CDC, sunlight
# --------------------------------------------------------------------------- #
def fetch_wildfire() -> Path:
    """Download the 270m FSim archive (1.53 GB) and extract just CONUS_BP.tif.
    The zip is deleted after extraction to reclaim space."""
    dest = C.DATA_RAW / "wrc_CONUS_BP.tif"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    override = os.environ.get("WRC_BP_PATH", "").strip()
    if override and Path(override).exists():
        return Path(override)

    zip_path = C.DATA_RAW / "RDS-2016-0034-3.zip"
    _download(C.WRC_BP_URL, zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        member = next(
            (m for m in zf.namelist()
             if m.replace("\\", "/").rsplit("/", 1)[-1] == C.WRC_BP_MEMBER),
            None,
        )
        if member is None:
            raise RuntimeError(
                f"WRC: {C.WRC_BP_MEMBER} not found in archive. "
                f"Members: {zf.namelist()[:25]}"
            )
        # Extract to a temp file then atomically rename, so an interrupted extraction
        # can never leave a corrupt-but-full-size .tif that gets cached as valid.
        tmp = dest.with_suffix(".tif.part")
        with zf.open(member) as src, open(tmp, "wb") as out:
            shutil.copyfileobj(src, out)
        tmp.rename(dest)
    try:
        zip_path.unlink()  # reclaim ~1.5 GB; CONUS_BP.tif is cached
    except OSError:
        pass
    return dest


def fetch_cdc_ticks() -> Path | None:
    if C.CDC_TICK_FALLBACK.exists():
        return C.CDC_TICK_FALLBACK
    try:
        return _download(C.CDC_TICK_URL, C.CDC_TICK_FALLBACK)
    except Exception:
        return None


def fetch_cdc_lyme() -> Path | None:
    if C.CDC_LYME_FALLBACK.exists():
        return C.CDC_LYME_FALLBACK
    if not C.CDC_LYME_URL:
        return None  # no static county-Lyme file; optional drop-in only
    try:
        return _download(C.CDC_LYME_URL, C.CDC_LYME_FALLBACK)
    except Exception:
        return None


def fetch_costco() -> Path:
    """Query OSM Overpass for Costco warehouses (brand:wikidata) and cache the raw
    JSON response. Returns the JSON path."""
    dest = C.DATA_RAW / "costco_overpass.json"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    query = (f'[out:json][timeout:180];'
             f'nwr["brand:wikidata"="{C.COSTCO_WIKIDATA}"];out center;')
    r = _SESSION.post(C.OVERPASS_URL, data={"data": query}, timeout=180)
    r.raise_for_status()
    if '"elements"' not in r.text:
        raise RuntimeError(f"Overpass returned no elements: {r.text[:200]}")
    dest.write_text(r.text)
    return dest


def fetch_airports() -> Path:
    """Download the OurAirports global airports CSV (public domain). Cached."""
    dest = C.DATA_RAW / "ourairports_airports.csv"
    return _download(C.AIRPORTS_URL, dest)


# Geometry-bearing formats are preferred over the results-only CSV (listed last).
_POLITICS_EXTS = (".gpkg", ".shp", ".geojson", ".json", ".topojson",
                  ".topojson.gz", ".geojson.gz", ".json.gz", ".csv.gz", ".csv")


def fetch_politics_precincts() -> Path:
    """Return a local precinct-results file for the politics criterion. Resolution order:
       1. POLITICS_PRECINCT_FILE env override, 2. any precinct file already in
       data/raw/politics/, 3. download POLITICS_PRECINCT_URL (the NYT national 2024
       file, ~1 GB) once. politics.attach_politics reads geo files via geopandas or a
       CSV with a WKT geometry column."""
    override = os.environ.get("POLITICS_PRECINCT_FILE", "")
    if override and Path(override).exists():
        return Path(override)
    out_dir = C.POLITICS_DIR
    if out_dir.exists():
        found = [p for ext in _POLITICS_EXTS for p in out_dir.glob(f"*{ext}")]
        if found:
            return found[0]
    if C.POLITICS_PRECINCT_URL:
        url = C.POLITICS_PRECINCT_URL
        # keep the URL's own extension (.csv.gz / .topojson.gz / …) for the cache file
        suffix = "".join(Path(url.split("?")[0]).suffixes) or ".csv.gz"
        dest = out_dir / f"precincts_2024{suffix}"
        try:
            return _download(url, dest)
        except Exception as exc:
            print(f"[politics] precinct download failed ({exc}); falling back to manual.")
    raise RuntimeError(
        "2024 precinct results file not found. Provide it once:\n"
        "  • Download the NYT national 2024 presidential precinct file with geometry — "
        "'precincts-with-results.topojson.gz' (NOT the .csv.gz, which is results-only / "
        "has no geometry) — from "
        "https://github.com/nytimes/presidential-precinct-map-2024#download-national-data\n"
        "  • Drop it in data/raw/politics/ (or set POLITICS_PRECINCT_FILE / "
        "POLITICS_PRECINCT_URL). Accepted: .topojson[.gz]/.geojson[.gz]/.gpkg/.shp, or a "
        ".csv[.gz] that actually contains a WKT geometry column."
    )


def dem_tile_basename(lat0: int, lon0: int) -> str:
    """Copernicus GLO-90 object/tile basename for the 1°×1° tile whose lower-left
    corner is (lat0, lon0) — e.g. (47, -123) -> Copernicus_DSM_COG_30_N47_00_W123_00_DEM."""
    ns = "N" if lat0 >= 0 else "S"
    ew = "E" if lon0 >= 0 else "W"
    return (f"Copernicus_DSM_COG_30_{ns}{abs(lat0):02d}_00_"
            f"{ew}{abs(lon0):03d}_00_DEM")


def fetch_dem_tile(lat0: int, lon0: int) -> Path | None:
    """Local path to the Copernicus DEM GLO-90 tile whose lower-left corner is
    (lat0, lon0), downloading it from the public AWS bucket if not already cached.
    Returns None if the tile does not exist (ocean — HTTP 404) or cannot be fetched."""
    name = dem_tile_basename(lat0, lon0)
    out_dir = C.ELEVATION_DEM_DIR
    # Accept an already-staged tile: flat, or the `aws s3 sync` folder layout.
    for cand in (out_dir / f"{name}.tif", out_dir / name / f"{name}.tif"):
        if cand.exists() and cand.stat().st_size > 0:
            return cand
    dest = out_dir / f"{name}.tif"
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"{C.ELEVATION_DEM_BUCKET}/{name}/{name}.tif"
    try:
        with _SESSION.get(url, stream=True, timeout=120) as r:
            if r.status_code == 404:
                return None          # ocean / no tile here
            r.raise_for_status()
            tmp = dest.with_suffix(".tif.part")
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
            tmp.rename(dest)
        return dest
    except Exception as exc:
        print(f"[elevation] DEM tile {name} fetch failed ({exc})")
        return None


def fetch_pm25_grid() -> Path:
    """Return the annual-mean PM2.5 GeoTIFF path. Resolution order:
       1. PM25_RASTER env override, 2. any .tif in data/raw/pm25/, 3. the default
       PM25_RASTER path if present, 4. download from PM25_GRID_URL if set.
       Otherwise raise with clear provide-once instructions (the research-grade
       PM2.5 surfaces are portal/Box-hosted with no stable direct URL)."""
    override = os.environ.get("PM25_RASTER", "")
    if override and Path(override).exists():
        return Path(override)
    out_dir = C.DATA_RAW / "pm25"
    # Accept a GeoTIFF (.tif) or a NetCDF (.nc) — the ACAG North-America product ships
    # as NetCDF; air_quality.attach_air_quality samples either format.
    found = (sorted(out_dir.glob("*.tif")) + sorted(out_dir.glob("*.nc"))
             if out_dir.exists() else [])
    if found:
        return found[0]
    if C.PM25_RASTER.exists() and C.PM25_RASTER.stat().st_size > 0:
        return C.PM25_RASTER
    if C.PM25_GRID_URL:
        try:
            dest = C.DATA_RAW / ("pm25_dl" + Path(C.PM25_GRID_URL).suffix)
            _download(C.PM25_GRID_URL, dest)
            out_dir.mkdir(parents=True, exist_ok=True)
            if dest.suffix.lower() == ".zip":
                with zipfile.ZipFile(dest) as zf:
                    zf.extractall(out_dir)
                rasters = [p for p in out_dir.rglob("*.tif")]
                if rasters:
                    dest.unlink(missing_ok=True)
                    return rasters[0]
            else:  # a direct .tif
                target = out_dir / "pm25_annual_mean.tif"
                dest.rename(target)
                return target
        except Exception as exc:
            print(f"[air_quality] PM25_GRID_URL download failed ({exc}); falling back to manual.")
    raise RuntimeError(
        "PM2.5 annual-mean grid not found. Provide it once:\n"
        "  • Download a recent CONUS annual-mean *surface PM2.5* file in µg/m³ — e.g. "
        "WashU ACAG 'Surface PM2.5' (van Donkelaar et al.), "
        "https://sites.wustl.edu/acag/satellites/surface-pm2-5-archive/ — as a GeoTIFF "
        "(global GWR) or a NetCDF (North-America regional).\n"
        "  • Then set env PM25_RASTER=/path/to/pm25.(tif|nc) (or drop the .tif/.nc in "
        "data/raw/pm25/), or set PM25_GRID_URL to a direct download link."
    )


def fetch_padus_geojson() -> Path:
    """Query the PAD-US ArcGIS feature service for large GAP 1/2 protected polygons
    (paginated GeoJSON, generalized geometry) and cache them to one .geojson file."""
    import json

    dest = C.DATA_RAW / "padus_gap12_large.geojson"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    gaps = ",".join(f"'{g}'" for g in C.PADUS_GAP_STATUSES)
    where = f"GAP_Sts IN ({gaps}) AND GIS_Acres >= {int(C.PROTECTED_MIN_ACRES)}"
    page, offset, feats = 2000, 0, []
    while True:
        params = {
            "where": where, "outFields": "GAP_Sts", "returnGeometry": "true",
            "outSR": "4326", "maxAllowableOffset": str(C.PADUS_SIMPLIFY_DEG),
            "geometryPrecision": "4", "f": "geojson",
            "orderByFields": "OBJECTID", "resultOffset": offset,
            "resultRecordCount": page,
        }
        r = _SESSION.get(C.PADUS_SERVICE + "/query", params=params, timeout=180)
        r.raise_for_status()
        js = r.json()
        if "features" not in js:
            raise RuntimeError(f"PAD-US service error: {str(js)[:200]}")
        batch = js["features"]
        feats.extend(batch)
        if len(batch) < page:
            break
        offset += page
        if offset > 500000:
            break
    print(f"[nature] PAD-US: {len(feats):,} large GAP 1/2 protected polygons")
    dest.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return dest


def fetch_nlcd() -> Path:
    """Return the NLCD CONUS land-cover raster path. Resolution order:
       1. NLCD_RASTER env override, 2. any .tif/.img already in data/raw/nlcd/,
       3. download from NLCD_URL if set. Otherwise raise with clear instructions
       (MRLC distributes CONUS only as multi-year bundles, so this is provide-once)."""
    override = os.environ.get("NLCD_RASTER", "")
    if override and Path(override).exists():
        return Path(override)
    out_dir = C.DATA_RAW / "nlcd"
    found = ([p for p in out_dir.glob("*.tif")] + [p for p in out_dir.glob("*.img")]
             if out_dir.exists() else [])
    if found:
        return found[0]
    if C.NLCD_URL:
        try:
            dest = C.DATA_RAW / ("nlcd_dl" + Path(C.NLCD_URL).suffix)
            _download(C.NLCD_URL, dest)
            out_dir.mkdir(parents=True, exist_ok=True)
            if dest.suffix.lower() == ".zip":
                with zipfile.ZipFile(dest) as zf:
                    zf.extractall(out_dir)
                rasters = ([p for p in out_dir.rglob("*.tif")]
                           + [p for p in out_dir.rglob("*.img")])
                if rasters:
                    dest.unlink(missing_ok=True)
                    return rasters[0]
            else:  # a direct .tif
                target = out_dir / "nlcd_land_cover.tif"
                dest.rename(target)
                return target
        except Exception as exc:
            print(f"[nature] NLCD_URL download failed ({exc}); falling back to manual.")
    raise RuntimeError(
        "NLCD land-cover raster not found. MRLC distributes CONUS only as multi-year "
        "bundles, so provide it once:\n"
        "  • Download a recent CONUS Annual NLCD *Land Cover* GeoTIFF from "
        "https://www.mrlc.gov/data (Annual NLCD → Land Cover bundle; unzip and keep the "
        "most recent year's .tif), or clip CONUS via https://www.mrlc.gov/viewer .\n"
        f"  • Then set env NLCD_RASTER=/path/to/landcover.tif  (or drop the .tif in "
        f"{out_dir}/), and re-run. PAD-US is already cached, so only NLCD recomputes."
    )


def fetch_ghi_raster() -> Path | None:
    """Annual GHI GeoTIFF for raster-mode sunlight. Auto-downloads the Global Solar
    Atlas global GHI archive (268 MB) and extracts the .tif. Returns None only if
    the download/extract fails (caller then falls back to the API)."""
    if C.GHI_RASTER.exists() and C.GHI_RASTER.stat().st_size > 0:
        return C.GHI_RASTER
    try:
        zip_path = C.DATA_RAW / "gsa_ghi.zip"
        _download(C.GHI_ZIP_URL, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            member = next((m for m in zf.namelist() if m.lower().endswith(".tif")), None)
            if member is None:
                raise RuntimeError(f"GSA GHI: no .tif in archive: {zf.namelist()[:20]}")
            with zf.open(member) as src, open(C.GHI_RASTER, "wb") as out:
                shutil.copyfileobj(src, out)
        try:
            zip_path.unlink()
        except OSError:
            pass
        return C.GHI_RASTER
    except Exception as exc:
        print(f"[sunlight] GHI raster unavailable ({exc}); will try NREL API")
        return None
