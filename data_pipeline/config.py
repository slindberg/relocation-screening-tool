"""
Phase 1 configuration: paths, data-source URLs, the criterion registry, and the
anchor-town smoke-test definitions.

Everything that another module needs to know about *what* we are building lives
here so the processing code stays declarative.
"""
from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
# Project root = the folder that contains this `data_pipeline/` package.
ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = ROOT / "data" / "raw"        # cached downloads (git-ignored)
DATA_WORK = ROOT / "data" / "work"      # intermediate artifacts (git-ignored)
OUTPUT_DIR = ROOT / "output"            # pipeline deliverables (git-ignored)

OUT_PARQUET = OUTPUT_DIR / "candidate_scores.parquet"
OUT_CSV = OUTPUT_DIR / "candidate_scores.csv"
OUT_METADATA = OUTPUT_DIR / "column_metadata.csv"

for _p in (DATA_RAW, DATA_WORK, OUTPUT_DIR):
    _p.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Secrets / keys  (loaded from the project-root .env if present)
# --------------------------------------------------------------------------- #
def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

NREL_API_KEY = os.environ.get("NREL_API_KEY", "")
# Census API key is optional; ACS place-population calls work key-less at this
# volume but a key avoids throttling.
CENSUS_API_KEY = os.environ.get("CENSUS_API_KEY", "")


# --------------------------------------------------------------------------- #
# Geographic scope
# --------------------------------------------------------------------------- #
# Exclude non-contiguous states and territories. DC is kept (contiguous).
EXCLUDE_USPS = {"AK", "HI", "AS", "GU", "MP", "PR", "VI", "UM"}

# Equal-area CRS used for any distance computation in the lower 48
# (NAD83 / Conus Albers, units = metres).
EQUAL_AREA_CRS = "EPSG:5070"
WGS84 = "EPSG:4326"


# --------------------------------------------------------------------------- #
# Data-source URLs  (resolved on the machine that actually runs the fetch;
# the sandbox these were authored in cannot reach them, but yours can.)
# --------------------------------------------------------------------------- #
VINTAGE_TIGER = "2023"
GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    "2023_Gazetteer/2023_Gaz_place_national.zip"
)
TIGER_COUNTY_URL = (
    f"https://www2.census.gov/geo/tiger/TIGER{VINTAGE_TIGER}/COUNTY/"
    f"tl_{VINTAGE_TIGER}_us_county.zip"
)
# 2020 Census decennial total population (P1_001N) for every place. The decennial
# PL file covers every incorporated place + CDP, including tiny rural ones that ACS
# may not tabulate, and needs no key.
POP_URL = (
    "https://api.census.gov/data/2020/dec/pl?get=NAME,P1_001N&for=place:*"
)
POP_VAR = "P1_001N"

# Centroid elevation from Copernicus DEM GLO-90 (90 m) 1°×1° Cloud-Optimized GeoTIFF
# tiles on the AWS Open Data bucket (public, no auth, no key). Only the tiles that
# actually contain towns are downloaded and cached under data/raw/elevation/ — a
# national run pulls a few hundred small COGs once, instead of hammering a rate-limited
# API. Ocean tiles do not exist (treated as no data). Tile objects are keyed
# Copernicus_DSM_COG_30_{N|S}LL_00_{E|W}LLL_00_DEM/<same>.tif. A key column, not scored.
ELEVATION_DEM_BUCKET = "https://copernicus-dem-90m.s3.amazonaws.com"
ELEVATION_DEM_DIR = DATA_RAW / "elevation"
# Legacy rate-limited Open-Meteo Elevation API (no longer used; kept for reference).
ELEVATION_API = "https://api.open-meteo.com/v1/elevation"

# PRISM 30-year normals (1991-2020), v2 service. The old
# services.nacse.org/prism/data/public/normals/... endpoints were retired in 2024;
# normals are now served as direct files from the data directory:
#   https://data.prism.oregonstate.edu/normals/us/<res>/<element>/<daily|monthly>/
#       prism_<element>_us_<rescode>_2020<MMDD|MM|''>_avg_30y.zip
# rescode: 4km -> 25m, 800m -> 30s. Annual lives under monthly/ with token '2020'.
# NOTE: PRISM temperature rasters are in DEGREES CELSIUS. We convert to °F.
PRISM_NORMALS_BASE = "https://data.prism.oregonstate.edu/normals"
PRISM_REGION = "us"
PRISM_RES = "4km"  # 4km keeps Phase 1 "fast/minimal"; switch to 800m for fidelity.
PRISM_RESCODE = {"4km": "25m", "800m": "30s"}

# Wildfire burn probability, CONUS. We use the 270m FSim product (RDS-2016-0034-3,
# a single 1.53 GB zip) rather than the 30m Wildfire-Risk-to-Communities raster
# (RDS-2020-0016-2), whose BP_CONUS download is 32 GB — overkill for a one-pixel-
# per-town screen. The archive contains CONUS_BP.tif; we extract just that.
# Override the extracted raster via WRC_BP_PATH if you already have it locally.
WRC_BP_URL = (
    "https://www.fs.usda.gov/rds/archive/products/"
    "RDS-2016-0034-3/RDS-2016-0034-3.zip"
)
WRC_BP_MEMBER = "CONUS_BP.tif"

# CDC Lyme disease: reported case counts by county, 2023 (direct CSV from the CDC
# Lyme case-map page). We convert counts to an incidence RATE (cases/100k) using
# county population, then percentile-score it — this is the scored Lyme signal.
CDC_LYME_URL = (
    "https://www.cdc.gov/lyme/media/files/2025/02/"
    "LD_Case_Counts_by_County_2023_updated.csv"
)
CDC_LYME_FALLBACK = DATA_RAW / "cdc_lyme_cases_by_county_2023.csv"
# County population, to turn Lyme case counts into a rate. We use the ACS 5-year
# (current geography) rather than the 2020 decennial because Connecticut replaced its
# 8 counties with 9 planning regions in 2022: TIGER 2023 and the CDC 2023 Lyme file
# both key CT on the planning regions, so the population denominator must too (the
# 2020 decennial still uses the old CT counties -> no match -> NaN rate for CT).
COUNTY_POP_URL = (
    "https://api.census.gov/data/2023/acs/acs5?get=NAME,B01003_001E&for=county:*"
)
COUNTY_POP_VAR = "B01003_001E"
# CDC Ixodes established-county table (Excel) — kept only as an informational raw
# (the Lyme vector's presence), not the score. CDC re-issues this with a dated name.
CDC_TICK_URL = (
    "https://www.cdc.gov/ticks/media/files/2026/04/"
    "Public_Use_Ixodes_County_Table_2026_03252026.xlsx"
)
CDC_TICK_FALLBACK = DATA_RAW / "cdc_ticks_established.xlsx"

# Sunlight (annual GHI).
#   mode="raster" (default, scales to 30k): auto-download the Global Solar Atlas
#       global GHI GeoTIFF (Solargis/World Bank, 250 m, EPSG:4326, kWh/m²/day,
#       long-term average of daily totals) and sample it at each centroid.
#   mode="api": NREL Solar Resource API, one call per point. Deprecated/limited and
#       does not scale to 30k; kept only as a last-resort fallback.
SUNLIGHT_MODE = os.environ.get("SUNLIGHT_MODE", "raster")
GHI_ZIP_URL = (
    "https://api.globalsolaratlas.info/download/World/"
    "World_GHI_GISdata_LTAy_AvgDailyTotals_GlobalSolarAtlas-v2_GEOTIFF.zip"
)
GHI_RASTER = DATA_RAW / "gsa_ghi_ltay_avgdailytotals.tif"   # extracted from the zip
NREL_SOLAR_API = "https://developer.nrel.gov/api/solar/solar_resource/v1.json"

# Wildfire: the 270m FSim burn-probability raster is nodata over non-burnable
# (developed/water/ag) land, so a town centroid often lands on nodata. We sample a
# small grid of points around each town (via point sampling) and average the valid
# (burnable) ones — a town's wildfire exposure comes from surrounding wildland.
WILDFIRE_NEIGHBORHOOD_M = 2700      # half-width of the sampled grid (~10 px at 270 m)
WILDFIRE_NEIGHBORHOOD_PTS = 5       # 5x5 = 25 sample points per town


# --------------------------------------------------------------------------- #
# Air quality — chronic annual-mean PM2.5 (gridded satellite surface)
# --------------------------------------------------------------------------- #
# A general air-quality criterion based on long-term ground-level fine-particulate
# (PM2.5) concentration, rather than episodic wildfire-smoke days (which largely
# duplicated score_wildfire). Chronic annual-mean PM2.5 is the standard exposure
# metric in air-pollution epidemiology and reflects persistent particulate burden
# (traffic, industry, agriculture, wood smoke, secondary aerosols) — a different
# geography from local burn hazard, with full-grid coverage so rural towns aren't
# tied to a distant monitor.
#
# Provide-once gridded surface (like NLCD): the research-grade products are portal/
# Box-hosted with no stable direct URL. Download a recent CONUS annual-mean surface
# PM2.5 file in µg/m³ — e.g. Washington University ACAG "Surface PM2.5" (van Donkelaar
# et al., satellite-derived, ~0.01°),
# https://sites.wustl.edu/acag/satellites/surface-pm2-5-archive/ — as a GeoTIFF (global
# GWR) or a NetCDF (North-America regional). Drop the .tif/.nc in data/raw/pm25/ or set
# the PM25_RASTER env var to its path. If you have a direct download URL, set
# PM25_GRID_URL to use it. (air_quality.py samples either format.)
PM25_GRID_URL = os.environ.get("PM25_GRID_URL", "")    # optional direct .tif/.zip URL
PM25_RASTER = DATA_RAW / "pm25_annual_mean.tif"        # default provide-once raster
PM25_NEIGHBORHOOD_M = 5000.0   # coastal-nodata fallback: sample a small grid …
PM25_NEIGHBORHOOD_PTS = 3      # … 3x3 points within ±5 km, mean of the valid cells


# --------------------------------------------------------------------------- #
# Isolation / regional remoteness (Phase 2) — computed from the places table
# --------------------------------------------------------------------------- #
CITY_POP_THRESHOLD = 50000     # a "sizable city" for the distance-to-city metric
ISOLATION_RADIUS_KM = 50.0     # population summed within this radius of a town


# --------------------------------------------------------------------------- #
# Airport proximity (Phase 2) — OurAirports (public domain, no key)
# --------------------------------------------------------------------------- #
AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
# OurAirports "type" values counted as a meaningful commercial airport; combined with
# scheduled_service == "yes" this is the spec's "large + medium hubs".
AIRPORT_TYPES = ("large_airport", "medium_airport")


# --------------------------------------------------------------------------- #
# Amenities (Phase 2) — distance to nearest Costco (proxy), OSM Overpass (no key)
# --------------------------------------------------------------------------- #
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
COSTCO_WIKIDATA = "Q715583"        # Costco Wholesale brand (OSM brand:wikidata)
CONUS_BBOX = (24.0, -125.0, 50.0, -66.0)   # (S, W, N, E) for filtering OSM results


# --------------------------------------------------------------------------- #
# Nature access (Phase 2) — PAD-US protected areas + NLCD natural cover
# --------------------------------------------------------------------------- #
# PAD-US protected areas via the public USGS ArcGIS feature service (no captcha, no
# 1.7 GB download — the ScienceBase geodatabase is now captcha-gated). We query only
# GAP Status 1 & 2 (managed for biodiversity = wilderness/park-core/refuge) polygons
# above a size threshold = "large protected/wild areas", paginated as GeoJSON, then
# distance-to-nearest. Geometry is generalized in the query to keep the payload small.
PADUS_SERVICE = (
    "https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services/"
    "PADUS_Protection_Status_by_GAP_Status_Code/FeatureServer/0"
)
PADUS_GAP_STATUSES = ("1", "2")    # strict protection / managed for biodiversity
PROTECTED_MIN_ACRES = 24710        # "large" wild area ~ 100 km²
PADUS_SIMPLIFY_DEG = 0.01          # query-side geometry generalization (~1 km)

# NLCD land cover (CONUS, 30 m). MRLC only distributes CONUS as multi-year bundle zips
# (no single-file/COG direct URL we can rely on), so this is a provide-once local file:
# download a recent CONUS Annual NLCD land-cover GeoTIFF (MRLC Direct Download Site or
# Viewer, https://www.mrlc.gov/data) and either drop the .tif in data/raw/nlcd/ or set
# the NLCD_RASTER env var to its path. If you have a direct URL, set NLCD_URL to use it.
NLCD_URL = os.environ.get("NLCD_URL", "")        # optional direct .zip/.tif URL
NLCD_RASTER = DATA_RAW / "nlcd_land_cover.tif"   # extracted/seeded raster
# NLCD classes counted as "natural" land cover (forest, shrub, grassland, wetlands).
NLCD_NATURAL_CLASSES = (41, 42, 43, 51, 52, 71, 72, 73, 74, 90, 95)
NLCD_WATER_NODATA = (0, 11, 12, 250)   # excluded from the denominator (water / nodata)
NATURE_COVER_RADIUS_M = 10000.0        # natural-cover fraction within ~10 km
NATURE_COVER_PTS = 11                  # 11x11 = 121 sample points per town


# --------------------------------------------------------------------------- #
# ERA5 pressure (Copernicus CDS)
# --------------------------------------------------------------------------- #
# Requires a free CDS account + personal access token. Either put it in
# ~/.cdsapirc (url: https://cds.climate.copernicus.eu/api  /  key: <token>) or set
# CDSAPI_URL / CDSAPI_KEY in .env. You must also accept the dataset's Terms of Use
# once on its CDS page. The pressure sub-scores are computed from hourly MSLP over
# the CONUS box, then sampled at each centroid.
ERA5_DATASET = "reanalysis-era5-single-levels"
ERA5_VARIABLE = "mean_sea_level_pressure"
ERA5_AREA = [50, -125, 24, -66]   # [N, W, S, E] — conterminous US
# Years of hourly data to characterise pressure variability. More years = more
# robust + larger/slower download; 3 recent years is a good default.
ERA5_YEARS = ["2021", "2022", "2023"]
CDS_URL = os.environ.get("CDSAPI_URL", "https://cds.climate.copernicus.eu/api")
CDS_KEY = os.environ.get("CDSAPI_KEY", "") or os.environ.get("CDS_API_KEY", "")


# --------------------------------------------------------------------------- #
# Temperature-comfort thresholds (°F)
# --------------------------------------------------------------------------- #
COMFORT_LOW_F = 50.0
COMFORT_HIGH_F = 85.0
HEAT_PENALTY_WEIGHT = 2.0   # days >85°F penalised at 2x days <50°F
COLD_PENALTY_WEIGHT = 1.0


# --------------------------------------------------------------------------- #
# Criterion registry
# --------------------------------------------------------------------------- #
# Each entry drives both normalization and the metadata file. `score_method`:
#   - "percentile": percentile rank across all towns; `higher_is_better` sets sign
#   - "linear":     a documented linear map (params in `linear`)
#   - "composite":  produced by a bespoke function in scoring.py (ticks, synoptic)
#
# `raw_cols` lists the human-readable raw columns kept alongside the score.
CRITERIA = [
    {
        "name": "temperature_comfort",
        "score_col": "score_temperature_comfort",
        "raw_cols": [
            ("raw_comfortable_day_fraction", "fraction of days with daytime high 50-85°F"),
            ("raw_days_above_85", "days/yr with daytime high > 85°F"),
            ("raw_days_below_50", "days/yr with daytime high < 50°F"),
        ],
        "units": "percentile (0-100)",
        "source": "PRISM daily normals 1991-2020 (tmax, 4km)",
        "source_date": "1991-2020 normals",
        "score_method": "composite",  # scoring.score_temperature_comfort
        "description": (
            "Daytime-high comfort. comfort_index = comfortable_days "
            "- 2*hot_days - cold_days (heat penalised 2x cold), then "
            "percentile-ranked across all towns so both heat and cold extremes "
            "land low and the distribution is well spread."
        ),
    },
    {
        "name": "sunlight",
        "score_col": "score_sunlight",
        "raw_cols": [("raw_annual_ghi", "annual global horizontal irradiance (kWh/m²/day)")],
        "units": "percentile (0-100)",
        "source": "Global Solar Atlas annual GHI (Solargis/World Bank)",
        "source_date": "GSA v2 (2020)",
        "score_method": "percentile",
        "higher_is_better": True,  # more sun is better
        "raw_for_score": "raw_annual_ghi",
        "description": "Sunlight. Higher annual GHI scores higher.",
    },
    {
        "name": "dryness",
        "score_col": "score_dryness",
        "raw_cols": [
            ("raw_annual_precip_in", "annual precipitation (inches/yr)"),
            ("raw_mean_relative_humidity_pct", "annual mean relative humidity (%)"),
            ("raw_mean_dewpoint_f", "annual mean dewpoint (°F)"),
        ],
        "units": "percentile of wetness composite, inverted (0-100)",
        "source": "PRISM annual precipitation (ppt) + annual mean RH (tdmean + tmean), 4km normals",
        "source_date": "1991-2020 normals",
        "score_method": "composite",  # scoring.score_dryness
        "description": (
            "Mould-growth propensity. Wetness composite = 0.5*percentile(annual "
            "precipitation) + 0.5*percentile(annual mean relative humidity); drier "
            "(less rain + lower humidity) scores higher. Annual figures are used so "
            "year-round damp climates (e.g. the Pacific NW, wet/cool most of the year "
            "rather than summer-muggy) are captured. Annual mean dewpoint is also "
            "retained as a raw column."
        ),
    },
    {
        "name": "pressure_diurnal",
        "score_col": "score_pressure_diurnal",
        "raw_cols": [("raw_pressure_diurnal_hpa", "mean daily MSLP range (hPa/day)")],
        "units": "percentile (0-100)",
        "source": f"ERA5 hourly MSLP, {ERA5_YEARS[0]}-{ERA5_YEARS[-1]} (Copernicus CDS)",
        "source_date": f"ERA5 {ERA5_YEARS[0]}-{ERA5_YEARS[-1]}",
        "score_method": "percentile",
        "higher_is_better": False,  # smaller daily pressure swing is better
        "raw_for_score": "raw_pressure_diurnal_hpa",
        "description": (
            "Daily pressure swing: mean over days of (daily max - daily min MSLP). "
            "Smaller swing scores higher. Directly measured from ERA5."
        ),
    },
    {
        "name": "pressure_synoptic",
        "score_col": "score_pressure_synoptic",
        "raw_cols": [("raw_pressure_synoptic_hpa", "std. dev. of daily-mean MSLP (hPa)")],
        "units": "percentile (0-100)",
        "source": f"ERA5 hourly MSLP, {ERA5_YEARS[0]}-{ERA5_YEARS[-1]} (Copernicus CDS)",
        "source_date": f"ERA5 {ERA5_YEARS[0]}-{ERA5_YEARS[-1]}",
        "score_method": "percentile",
        "higher_is_better": False,  # less day-to-day variability is better
        "raw_for_score": "raw_pressure_synoptic_hpa",
        "description": (
            "Synoptic (day-to-day) pressure variability: standard deviation of "
            "daily-mean MSLP across all days. Lower variability scores higher. "
            "Captures frontal frequency directly (e.g. flags the stormy Pacific NW), "
            "which the former continentality proxy could not."
        ),
    },
    {
        "name": "air_quality",
        "score_col": "score_air_quality",
        "raw_cols": [
            ("raw_annual_pm25_ugm3", "annual-mean ground-level PM2.5 (µg/m³)"),
        ],
        "units": "percentile (0-100)",
        "source": "Satellite-derived annual-mean surface PM2.5 (WashU ACAG, van Donkelaar et al.), ~0.01° CONUS",
        "source_date": "ACAG V5 (most recent annual mean provided)",
        "score_method": "percentile",
        "higher_is_better": False,  # lower chronic PM2.5 is better
        "raw_for_score": "raw_annual_pm25_ugm3",
        "description": (
            "Chronic air quality: long-term annual-mean ground-level fine-particulate "
            "(PM2.5) concentration, sampled from a gridded satellite-derived surface. "
            "Lower PM2.5 scores higher. This is the standard air-pollution-epidemiology "
            "exposure metric and reflects persistent particulate burden (traffic, "
            "industry, agriculture, wood smoke, secondary aerosols) — a different "
            "geography from local wildfire burn hazard (score_wildfire), which the "
            "former episodic smoke-days metric largely duplicated. Full-grid coverage, "
            "so rural towns are not tied to a distant monitor."
        ),
    },
    {
        "name": "wildfire",
        "score_col": "score_wildfire",
        "raw_cols": [("raw_burn_probability", "mean annual burn probability near town (unitless)")],
        "units": "percentile (0-100)",
        "source": "USDA FSim 270 m burn probability, CONUS (RDS-2016-0034-3)",
        "source_date": "2023 (LF2020)",
        "score_method": "percentile",
        "higher_is_better": False,  # lower burn probability is better
        "raw_for_score": "raw_burn_probability",
        "description": (
            "Local wildfire hazard: mean modelled annual burn probability in the "
            "neighborhood of the town. Lower scores higher. Distinct from chronic "
            "particulate exposure (score_air_quality), which measures long-term PM2.5."
        ),
    },
    {
        "name": "lyme",
        "score_col": "score_lyme",
        "raw_cols": [
            ("raw_lyme_incidence_per_100k", "Lyme incidence (reported cases / 100k, 2023)"),
            ("raw_lyme_cases_2023", "reported Lyme cases in county (2023)"),
            ("raw_tick_established", "Lyme-vector tick (I. scapularis/pacificus) established in county (Y/N) — context only"),
        ],
        "units": "0-100 (0 reported Lyme = 100; positive incidence inverse-ranked)",
        "source": "CDC reported Lyme cases by county (2023) / 2020 county population; CDC Ixodes established (context)",
        "source_date": "CDC 2023 Lyme; 2020 decennial",
        "score_method": "composite",  # scoring.score_lyme
        "description": (
            "Lyme disease risk as a weight (never a veto). County reported Lyme "
            "cases (2023) converted to an incidence rate (cases/100k) using county "
            "population. Lyme is strongly zero-inflated (most US counties report "
            "≈none), so a county with 0 reported cases gets the top score (100, "
            "lowest risk) and only positive-incidence (Lyme-endemic) counties are "
            "inverse-rank-scored below that — higher incidence → lower score. Joined "
            "by county FIPS. Tick establishment is kept as a context raw, not scored."
        ),
    },
    {
        "name": "nature_access",
        "score_col": "score_nature_access",
        "raw_cols": [
            ("raw_dist_to_protected_km", "distance to nearest large protected/wild area (km)"),
            ("raw_natural_cover_pct", "natural land-cover fraction within 10 km (%)"),
        ],
        "units": "0-100 (closer + more natural = higher)",
        "source": "USGS PAD-US 4.0 (GAP 1/2, ≥100 km²) + NLCD land cover (CONUS, 30 m)",
        "source_date": "PAD-US 4.0 (2024); NLCD 2021",
        "score_method": "composite",  # scoring.score_nature_access
        "description": (
            "Access to wild nature. 0.5*percentile(close to nearest large GAP 1/2 "
            "protected area, ≥100 km²) + 0.5*percentile(high natural land-cover "
            "fraction — forest/shrub/grass/wetland — within 10 km). Higher = more "
            "natural surroundings and closer to protected wild land. Paired with isolation."
        ),
    },
    {
        "name": "airport",
        "score_col": "score_airport",
        "raw_cols": [
            ("raw_dist_to_airport_mi", "great-circle distance to nearest large/medium hub airport (mi)"),
            ("raw_nearest_airport_iata", "nearest hub airport code — context"),
        ],
        "units": "percentile (0-100)",
        "source": "OurAirports — US large/medium airports with scheduled service",
        "source_date": "OurAirports (current)",
        "score_method": "percentile",
        "higher_is_better": False,  # closer to an airport is better
        "raw_for_score": "raw_dist_to_airport_mi",
        "description": (
            "Air-travel access. Great-circle distance to the nearest US large- or "
            "medium-hub commercial airport (OurAirports, scheduled service). Closer "
            "scores higher."
        ),
    },
    {
        "name": "amenities",
        "score_col": "score_amenities",
        "raw_cols": [
            ("raw_dist_to_costco_mi", "great-circle distance to nearest Costco (mi)"),
        ],
        "units": "percentile (0-100)",
        "source": "OpenStreetMap (Overpass) — Costco warehouses",
        "source_date": "OSM (current)",
        "score_method": "percentile",
        "higher_is_better": False,  # closer is better
        "raw_for_score": "raw_dist_to_costco_mi",
        "description": (
            "Everyday amenity access, proxied by distance to the nearest Costco "
            "warehouse (OpenStreetMap). Closer scores higher. Low weight by design."
        ),
    },
    {
        "name": "isolation",
        "score_col": "score_isolation",
        "raw_cols": [
            ("raw_pop_within_50km", "population within 50 km (sum of nearby places)"),
            ("raw_dist_to_city_km", "distance to nearest city ≥ 50k population (km)"),
            ("raw_place_density_per_sqmi", "place population density (persons/sq mi) — context"),
        ],
        "units": "0-100 (more isolated = higher)",
        "source": "Census places + 2020 decennial population (derived; no new download)",
        "source_date": "2020-2023",
        "score_method": "composite",  # scoring.score_isolation
        "description": (
            "Regional remoteness / isolation. 0.5*percentile(low population within "
            "50 km) + 0.5*percentile(far from nearest city ≥ 50k). Higher = fewer "
            "people around. Population-within-radius captures true remoteness (a "
            "low-density suburb inside a metro is correctly not isolated); place "
            "density is kept only as a context raw. Paired with nature access."
        ),
    },
]

KEY_COLUMNS = [
    "place_geoid", "name", "state", "county", "county_fips",
    "lat", "lon", "elevation_ft", "population", "land_area_sqmi",
]


# --------------------------------------------------------------------------- #
# Data-coverage expectations (smoke test)
# --------------------------------------------------------------------------- #
# A column that is present in the matrix is expected to be (near-)complete: a stage
# that ran should have produced a value for essentially every town. Partial coverage
# — e.g. an elevation API that silently dropped most of its batches — is a real
# failure that the median-imputation in the score_ columns would otherwise hide. The
# smoke test fails if a checked column's non-null fraction falls below its threshold.
COVERAGE_MIN_KEY = 0.99    # key columns that must be essentially complete
COVERAGE_MIN_RAW = 0.95    # a criterion's raw columns (slack for grid-edge NaNs)
# Key columns that should have data for every town. `population` is intentionally
# excluded: it is optional (needs a Census API key) and may be legitimately empty.
COVERAGE_KEY_COLUMNS = [
    "place_geoid", "name", "state", "lat", "lon", "land_area_sqmi", "elevation_ft",
]
# Per-column overrides for legitimately partial coverage (column -> min fraction).
COVERAGE_OVERRIDES: dict[str, float] = {
    # e.g. a layer with a known coastal/edge gap:
    # "raw_annual_pm25_ugm3": 0.90,
}


# --------------------------------------------------------------------------- #
# Anchor smoke test (from the spec). Direction is always "higher score = better
# fit". Each axis is checked for top-quartile ("high") or bottom-quartile ("low")
# membership of that score column's empirical distribution.
#
# `proxy_caveat=True` marks checks that depend on a Phase-1 proxy that cannot
# fully reproduce the Phase-2 (ERA5) behaviour the spec anchor was written for;
# these are reported but do not count as hard failures.
# --------------------------------------------------------------------------- #
ANCHORS = [
    {
        "name": "Olympia", "state": "WA",
        "high": [], "low": [
            ("score_sunlight", False),
            ("score_dryness", False),
            # ERA5 std-of-daily-mean measures swing AMPLITUDE; maritime air moderates
            # PNW amplitude though fronts are frequent, so Olympia ranks mid. Accepted.
            ("score_pressure_synoptic", "accepted"),
        ],
    },
    {
        "name": "San Luis Obispo", "state": "CA",
        "high": [
            ("score_temperature_comfort", False),
            ("score_pressure_diurnal", False),
            ("score_pressure_synoptic", False),
        ],
        "low": [],
    },
    {
        "name": "Santa Fe", "state": "NM",
        "high": [
            ("score_sunlight", False),
            ("score_dryness", False),
            # NM is non-endemic; Santa Fe still reports a case or two (often
            # travel-associated by county of residence), so it scores very low risk
            # (~85) but not the exactly-zero top mass. Zero-inflation: >half of US
            # counties report 0 Lyme, so q75=100 and "top quartile" = zero-case only.
            ("score_lyme", "accepted"),
        ],
        "low": [("score_pressure_diurnal", False)],
    },
    {
        "name": "Phoenix", "state": "AZ",
        "high": [("score_sunlight", False), ("score_dryness", False)],
        "low": [("score_temperature_comfort", False)],
    },
    {
        "name": "International Falls", "state": "MN",
        "high": [],
        "low": [
            # The spec's 2x heat penalty crowds the bottom quartile with desert heat,
            # so a cold-extreme (not hot) town lands ~37th pct. Accepted per that rule.
            ("score_temperature_comfort", "accepted"),
            ("score_sunlight", False),
        ],
    },
    {
        "name": "Hartford", "state": "CT",
        "high": [],
        "low": [
            ("score_lyme", False),
            # The Northeast feels humid, but nationally Hartford is only ~mid-pack on
            # the rainfall+humidity composite; the wettest/most-mould-prone quartile is
            # the SE / Gulf / Appalachia / Pacific NW. Accepted.
            ("score_dryness", "accepted"),
        ],
    },
]
