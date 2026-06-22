# Relocation Screening

A toolkit for finding a place to live in the contiguous United States by scoring every
town against a set of climate, health, environment, and access criteria — then exploring
the trade-offs interactively.

It has two parts:

1. **Data pipeline** (`tools/generate_candidate_data.py`) — builds a per-criterion scoring
   matrix for every Census-designated place in the lower 48 (~31,500 towns). For each
   criterion it produces a `raw_<name>` column (the real-world value, e.g. inches of rain)
   and a `score_<name>` column (0–100, higher = better fit), plus a `column_metadata.csv`
   describing every column's units, method, and source. No cross-criterion weighting
   happens here — the pipeline just measures each town.

2. **Weighting & visualization app** (`index.html`) — a self-contained static web page that
   loads the pipeline's output and lets you set the importance of each criterion with live
   sliders, apply hard-minimum filters, rank towns by a weighted-average composite, see the
   results on a US map, and open any town for a full per-criterion breakdown.

The pipeline writes to `output/`:

- `output/candidate_scores.csv` — one row per town: identifying columns
  (`place_geoid, name, state, county, lat, lon, elevation_ft, population, …`) followed by a
  `raw_<name>` and `score_<name>` for each criterion.
- `output/candidate_scores.parquet` — same data, columnar (for downstream analysis).
- `output/column_metadata.csv` — per-column description, units, source, and method.

## Repository layout

```
index.html                         the weighting / visualization web app
assets/                            basemap data fetched by the app (coastline, borders, rivers, relief)
output/                            generated scoring matrix + metadata (what the app reads)
tools/generate_candidate_data.py   pipeline orchestrator / CLI
tools/build_basemap.py             regenerates the app's basemap assets
tools/diagnose.py                  data-source connectivity probe
data_pipeline/                     pipeline modules (fetch, places, sampling, scoring, …)
spec/                              pipeline + app design specs
requirements.txt                   Python dependencies
```

## Generating the data

```bash
pip install -r requirements.txt        # geopandas + rasterio pull in GDAL / PROJ
python tools/generate_candidate_data.py            # full run — all ~31.5k places
python tools/generate_candidate_data.py --sample   # offline self-test on synthetic data
python tools/generate_candidate_data.py --force lyme,wildfire   # recompute specific criteria
```

`--sample` needs only pandas/numpy/pyarrow; it validates the scoring math and output schema
without any downloads (writing to `sample_run/`). Each criterion's raw values are cached
under `data/work/cache/`, so a normal run only recomputes new or changed criteria while
scores and metadata are always rebuilt from the cached raws.

### Configuration

Keys and options live in a git-ignored `.env`:

```
NREL_API_KEY=...        # optional sunlight API fallback
CENSUS_API_KEY=...      # optional; fills the population column (https://api.census.gov/data/key_signup.html)
SUNLIGHT_MODE=raster    # "raster" (sample a local GHI GeoTIFF, default) or "api" (per-point)
# CDSAPI_KEY=...         # ERA5 pressure (or use ~/.cdsapirc)
```

Most data sources download automatically and are cached under `data/raw/`. Two need a
one-time setup:

- **ERA5 pressure** (Copernicus CDS): create a free account at
  <https://cds.climate.copernicus.eu>, put your token in `~/.cdsapirc` (or `CDSAPI_KEY`),
  and accept the dataset terms once. Hourly mean-sea-level pressure is downloaded over the
  CONUS box for `ERA5_YEARS` (default 2021–2023).
- **Population & Lyme incidence**: a free Census API key (`CENSUS_API_KEY`) fills the
  population column and the per-capita Lyme rate; without it those are left null and
  everything else still runs.

If any upstream URL has rotated, drop the file into `data/raw/` under the name the fetcher
expects (see `data_pipeline/config.py`) and the pipeline will use it.

## Running the app locally

`index.html` reads its data with `fetch`, so it must be served over **http(s)** — opening
the file directly from disk (`file://`) will not load. Serve the project folder with any
static server:

```bash
python3 -m http.server 8000        # then open http://localhost:8000/
```

The app loads `output/candidate_scores.csv`, `output/column_metadata.csv`, and the basemap
files in `assets/`. It is **metadata-driven**: any `score_<name>` column present in the CSV
automatically appears as its own toggle, importance slider, map layer, distribution
histogram, and town-detail row — so regenerating the data with a new criterion needs no
changes to the app. To update what the app shows, re-run the pipeline and refresh
`output/candidate_scores.csv` and `output/column_metadata.csv`.

The basemap files (`assets/basemap.json`, `assets/relief.png`) are generated separately by
`tools/build_basemap.py` and only need rebuilding if the geography layers change.

## Criteria

Every criterion is scored 0–100, where **higher is a better fit**. Percentile scores are
ranked across all towns. Each town keeps both the raw measured value and the score.

| Criterion | Measures | Source |
|---|---|---|
| **Temperature comfort** | Daytime-high comfort. A comfort index of (comfortable days − 2×hot days − cold days), penalizing heat twice as heavily as cold, percentile-ranked. | PRISM 1991–2020 daily maximum-temperature normals (4 km) |
| **Sunlight** | Annual solar resource; sunnier scores higher. | Global Solar Atlas annual GHI (Solargis / World Bank) |
| **Dryness** | Mould-growth propensity from a wetness composite of annual precipitation and annual mean relative humidity; drier scores higher. | PRISM 1991–2020 precipitation + dewpoint/temperature normals (4 km) |
| **Pressure — diurnal** | Day-to-day comfort for the pressure-sensitive: mean daily swing (max − min) of sea-level pressure; smaller swings score higher. | ERA5 hourly mean-sea-level pressure, 2021–2023 (Copernicus CDS) |
| **Pressure — synoptic** | Frontal frequency: standard deviation of daily-mean sea-level pressure; steadier scores higher (correctly flags stormy maritime regions). | ERA5 hourly mean-sea-level pressure, 2021–2023 (Copernicus CDS) |
| **Smoke** | Observed air quality: mean days per year with a 24-hour PM2.5 average above 35 µg/m³ at the nearest monitor; fewer smoke-days score higher. Captures transported wildfire smoke, not just local fire. | EPA AQS daily PM2.5 (parameters 88101 + 88502), 2021–2023 |
| **Wildfire** | Local fire hazard: modelled annual burn probability around the town; lower scores higher. | USDA FSim 270 m burn probability, CONUS (RDS-2016-0034-3) |
| **Lyme disease** | Tick-borne disease risk: county Lyme incidence (reported cases per 100k). Since most counties report essentially none, zero-case counties get the top score and only endemic counties are ranked below. | CDC reported Lyme cases by county (2023) ÷ county population |
| **Nature access** | Access to wild land: nearness to a large protected area (GAP 1/2, ≥100 km²) combined with the fraction of natural land cover within 10 km; closer + wilder scores higher. | USGS PAD-US 4.0 + NLCD land cover (CONUS, 30 m) |
| **Isolation** | Regional remoteness: low population within 50 km combined with distance to the nearest city of ≥50k; more isolated scores higher (a low-density suburb inside a metro is correctly not isolated). | Census places + 2020 decennial population (derived) |
| **Airport** | Air-travel access: great-circle distance to the nearest large- or medium-hub commercial airport with scheduled service; closer scores higher. | OurAirports (US large/medium hubs) |
| **Amenities** | Everyday big-box amenity access, proxied by distance to the nearest Costco warehouse; closer scores higher. | OpenStreetMap (Overpass) |

### Method notes

- Every raster sampler reprojects each town into the raster's own coordinate system before
  sampling, so points never drift into the ocean or a neighboring state.
- PRISM temperatures are converted from °C to °F before applying comfort thresholds.
- Town-to-county joins use a point-in-polygon match with a nearest-county fallback for
  coastal rounding; Lyme is joined on county FIPS. Connecticut's 2022 switch from counties
  to planning regions is handled by keying TIGER 2023, the CDC 2023 file, and ACS 2023 all
  on the same regions.
- Yes this is entirely vibe coded, so direct code judgments towards Claude, and AI use judgment towards me.
