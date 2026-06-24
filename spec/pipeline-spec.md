# Relocation Screening Pipeline — Specification

## Purpose

Produce a **scoring matrix**: one row per candidate town, with columns holding a normalized 0–100 suitability score for each criterion (plus the raw underlying value). This matrix feeds a downstream interactive weighting tool where weights are adjusted live.

The pipeline does **not** apply weights or pick winners. It only computes per-criterion scores. All trade-off weighting happens downstream in the tool. Keep every score independent and un-weighted.

## Candidate set

US Census **places** (incorporated places + Census Designated Places), lower 48 states.

- Source: Census Bureau Gazetteer "Places" file (TSV with name, state, land area, centroid lat/lon). ~31.5k rows — computationally trivial. Place names are cleaned of their lowercase class descriptor ("Phoenix city" → "Phoenix"). Population is joined from the **2020 decennial** file (the Gazetteer has no population) and needs a free Census API key.
- **No population floor** — rural and isolated towns must stay in scope.
- Spatially join each centroid to its county using the Census TIGER/Line county shapefile, so county-level datasets (ticks, Lyme) can be joined by FIPS code.

## Criteria → data source → processing

For every criterion, produce **two** columns: `raw_<name>` (human-readable value with units) and `score_<name>` (0–100, where higher always means a better fit). Keep both — the tool displays raw values, scores drive the math.

| Criterion | Source (format) | Processing | Raw metric | Direction |
|---|---|---|---|---|
| Temperature comfort | PRISM daily normals 1991–2020, tmax (4km raster) | Sample at centroid; count days with daytime high in 50–85°F. Penalize days >85°F at 2× the weight of days <50°F (heat is harder to mitigate than cold). Score = **percentile rank** of the weighted comfort index (comfortable − 2·hot − cold). | Comfortable-day fraction; days >85°F; days <50°F | Higher index → higher |
| Dryness / mold | PRISM annual precipitation `ppt` + mean RH (derived from `tdmean` + `tmean`) (4km) | Wetness composite = 0.5·pct(annual precip) + 0.5·pct(annual mean RH), inverted. Captures mould propensity (year-round wetness + dampness); annual mean dewpoint retained as a raw. | Annual precip (in/yr); annual mean RH (%); mean dewpoint (°F) | Lower (drier) → higher |
| Wildfire (local hazard) | USDA **FSim 270 m** burn probability (RDS-2016-0034-3, `CONUS_BP.tif`) | Sample a small grid of points around the centroid and average the valid (burnable) cells — a town's exposure comes from surrounding wildland. | Burn probability | Lower → higher |
| Air quality (chronic PM2.5) | Satellite-derived **annual-mean surface PM2.5** (~0.01°, WashU ACAG) | Sample the gridded annual-mean PM2.5 surface at the town centroid (small-neighborhood fallback for coastal nodata). Chronic long-term exposure, independent of local burn hazard. | Annual-mean PM2.5 (µg/m³) | Lower → higher |
| ~~Pollen~~ *(too location-specific to pre-compute — see below)* | — | No national column: no trustworthy free national layer exists | — | — |
| Pressure — **diurnal swing** | ERA5 hourly MSLP (Copernicus CDS) — measured directly | Mean over days of (daily max − daily min) MSLP, per grid cell, sampled at centroid | hPa/day | Lower → higher |
| Pressure — **synoptic** | ERA5 hourly MSLP (Copernicus CDS) — measured directly | Std. dev. of daily-mean MSLP across all days, per grid cell, sampled at centroid | hPa variability | Lower → higher |
| Lyme disease | CDC reported Lyme cases by county (2023) ÷ 2020 county population → incidence rate; CDC *Ixodes* established (context only) | Join by county FIPS. 0-case counties = top score; positive-incidence counties inverse-rank-scored | Lyme incidence (cases/100k); cases; tick established (Y/N, context) | Lower incidence → higher |
| Sunlight | Global Solar Atlas annual GHI raster (Solargis/World Bank); NREL NSRDB API as fallback | Sample GHI at centroid | Annual GHI (kWh/m²/day) | Higher → higher |
| Nature access | USGS PAD-US (GAP 1/2, ≥100 km², via ArcGIS feature service) + NLCD land cover (30 m) | Distance to nearest large protected/wild area; natural-cover fraction within 10 km | km to wild land; % natural cover | Closer + more natural → higher |
| Isolation | Census places + 2020 population (derived) | Population within 50 km; distance to nearest city ≥ 50k; (place density as context) | persons within 50 km; km to city | Fewer people around → higher |
| Airport proximity | OurAirports — US large/medium airports w/ scheduled service | Great-circle distance to nearest hub (drive-time via OSRM is location-specific, not pre-computed here) | miles | Closer → higher |
| Amenities *(low weight)* | Costco warehouses (OSM Overpass, brand:wikidata) | Distance to nearest Costco (Costco alone used as the amenity proxy; cafés dropped) | miles | Closer → higher |
| Politics | NYT national 2024 presidential **precinct** results + boundaries | Spatial-join each centroid to its precinct; Democratic share of the two-party vote = Dem / (Dem + Rep). Precinct-level (not county) catches a blue town in a red county. | Dem two-party vote (%) | More Democratic → higher |

### Pressure decomposition (important)

Keep the two pressure sub-scores as **separate columns** (`score_pressure_diurnal`, `score_pressure_synoptic`) so they can be weighted independently. They behave differently by region: a coastal/marine location scores low (good) on **both**; a high-desert location tends to score well on synoptic stability but worse on diurnal swing. This split is the whole point — a single "pressure" number would hide the trade-off.

Note on synoptic: the ERA5 metric (std. dev. of daily-mean MSLP) measures pressure-swing **amplitude**. Maritime air moderates amplitude even where fronts are frequent, so the Pacific Northwest ranks mid rather than worst (the largest-amplitude swings are in the continental north). A frequency metric (mean |ΔP| day-to-day) would rank the PNW worse; this is a documented accepted divergence (see self-validation).

### Lyme criterion is a weight, not a veto

Lyme incidence reduces a town's score on that one axis; it does **not** eliminate the town. The weighting tool decides how much it matters. (The criterion is Lyme-disease incidence, not ticks per se; tick establishment is retained only as an informational raw.)

### Not pre-computed — too location-specific (property/listing-level, not town-level)

These depend on the specific address, lot, or building rather than the town, so they can't be
meaningfully pre-calculated across all candidates. They're evaluated per-location once a town is
being seriously considered, not baked into this matrix.

- **VOCs** — building age / materials (a per-listing filter)
- **Transportation noise** — DOT/BTS National Transportation Noise Map (too local for a centroid-level screen)
- **Immediate out-the-door nature** and specific lot adjacency (per-property, not per-town)
- **Pollen** — *wanted as a screening criterion, but deliberately skipped at this stage
  because no trustworthy free national layer exists.* The measured gold standard (NAB/AAAAI)
  is only ~80–100 stations and gated; EPA's modeled CONUS pollen is not publicly downloadable
  and covers only oak + ragweed; Open-Meteo/CAMS pollen is Europe-only; commercial APIs (Ambee,
  IBM) are paid; Google Pollen is forecast-only (≤5 days, no climatology). The land-cover proxy
  is *misleading* (measures vegetation, not allergenic airborne pollen — can rank towns backwards,
  silently), and misleading data is worse than no data. So pollen is evaluated **per-location**,
  once a town is being seriously considered, via real lookups (Google Pollen API live/seasonal,
  or nearest NAB station), where a handful of queries are cheap and meaningful — **not** as a
  fabricated national column. Revisit if a credible free national pollen-climatology layer
  becomes available.

## Normalization

Map each raw metric to 0–100. Default to **percentile rank** across all candidate towns (robust to outliers), or a documented piecewise/composite curve where a real threshold or multi-input blend matters. Record the chosen method per column in the metadata file. Always retain raw values so the tool can show real numbers ("19 days/yr over 85°F"), not just an abstract score.

As built: **temperature comfort** is the percentile rank of the weighted comfort index (`comfortable − 2·hot − cold`) — percentile rather than a linear map, so both heat and cold extremes spread to the bottom and the distribution isn't compressed. **Dryness** is a composite: `0.5·pct(annual precip) + 0.5·pct(annual mean RH)`, inverted. **Lyme** is zero-inflated, so 0-case counties get the top score (100) and positive-incidence counties are inverse-rank-scored among themselves. All other criteria are plain percentile rank.

## Output

`output/candidate_scores.parquet` (and a `.csv` mirror), one row per town (the
`output/` directory is git-ignored):

- **Keys:** place_geoid, name, state, county, county_fips, lat, lon, **elevation_ft**, population, land_area_sqmi
- **Per criterion:** `raw_<criterion>` and `score_<criterion>`
- **Pressure:** the two sub-score columns above (with raws)

Plus `column_metadata.csv`: column, description, units, source, source_date, normalization_method. The weighting tool reads this file to label its sliders.

## Status

All geographic criteria are implemented: Census places + county join, PRISM (temperature comfort, dryness composite), **ERA5 pressure (both sub-scores, measured directly — no proxies)**, CDC Lyme incidence, FSim 270 m wildfire, Global Solar Atlas sunlight, chronic PM2.5 air quality, nature access, isolation, airport proximity, and Costco amenities. Pollen is intentionally excluded here (see above). The only remaining items are inputs too location-specific to pre-compute per town — OSRM drive-times and per-property filters — which are evaluated per-location later.

## Prerequisites

- **Python 3.11+** with: pandas, numpy, pyarrow, geopandas, shapely, pyproj, rasterio, requests, openpyxl (CDC Excel), and **xarray + netcdf4 + dask + cdsapi** (ERA5). See `requirements.txt`.
- **Free keys:** Copernicus CDS account + personal access token in `~/.cdsapirc` (ERA5 pressure — accept the dataset Terms of Use once); Census API key (`CENSUS_API_KEY`) to fill the population column; NREL key only if using the sunlight API fallback.
- **Disk:** PRISM normals, the FSim wildfire raster (~1.5 GB zip), the GHI raster (~268 MB), and ERA5 (~2.6 GB for 3 years) are multi-GB — ensure space in the project folder.
- All data sources are free and public.

## Self-validation / smoke test (run before declaring done)

After scoring, verify these named anchor towns land where their climate makes obvious. Each row lists axes whose expected percentile is unambiguous; confirm the town's `score_<axis>` falls in the stated band (top or bottom quartile across all candidates). These are **directional** checks, not exact targets. Score direction is always "higher = better fit," so low Lyme *risk* means a high `score_lyme`.

| Town | Expected HIGH (top quartile) | Expected LOW (bottom quartile) |
|---|---|---|
| Olympia, WA | — | sun; dryness; pressure-synoptic |
| San Luis Obispo, CA | temperature comfort; pressure-diurnal; pressure-synoptic | — |
| Santa Fe, NM | sun; dryness; Lyme (low risk) | pressure-diurnal |
| Phoenix, AZ | sun; dryness | temperature comfort |
| International Falls, MN | — | temperature comfort; sun |
| Hartford, CT | — | Lyme (high risk); dryness |

Coverage: this set exercises every major data layer — sun (Olympia low vs. Phoenix high), dryness (Phoenix high vs. Olympia low), the **heat tail** (Phoenix) and **cold tail** (International Falls) of temperature comfort, both pressure sub-scores (SLO high on both, Olympia synoptic-low, Santa Fe diurnal-low), and the Lyme-incidence county join (Hartford high-risk vs. Santa Fe low-risk).

**If an anchor fails:** the usual culprits are a coordinate-system mismatch when sampling a raster (points get sampled in the wrong projection and land in the ocean or the next state over), a units error, or a bad county-FIPS join. Investigate and re-run before trusting any rankings — do not ship the matrix with a failing anchor left unexplained.

**Accepted divergences (investigated, documented, approved — reported by the smoke test as `<accepted>`, not failures):**

- **Olympia, WA — pressure-synoptic.** ERA5 std-of-daily-mean MSLP measures swing *amplitude*; maritime air moderates the PNW's amplitude though fronts are frequent, so Olympia ranks mid (~57th pct). The largest-amplitude swings are the continental north.
- **International Falls, MN — temperature comfort.** The 2× heat penalty crowds the bottom quartile with desert heat, so a cold-extreme (not hot) town lands ~37th pct — the honest result of that weighting.
- **Hartford, CT — dryness.** On the rainfall+humidity composite the Northeast is only ~mid-pack nationally; the wettest/most-mould-prone quartile is the SE / Gulf / Appalachia / Pacific NW.
- **Santa Fe, NM — Lyme.** Scores ~85 (very low risk). >50% of US counties report zero Lyme (all scoring 100), so q75=100 and strict "top quartile" requires zero cases; non-endemic NM still reports a case or two, so Santa Fe sits just below the zero mass.

All other anchors pass.

Also verify:
- No nulls in any `score_` column (impute or flag explicitly).
- Score distributions are spread across the 0–100 range, not all clustered.
- **Data coverage:** every key column expected to be complete (incl. `elevation_ft`) and every criterion's `raw_` column that ran must clear a non-null-fraction threshold (default 99% for keys, 95% for raws). `score_` columns are median-imputed, so a silent upstream gap (e.g. an elevation source that dropped most rows) only shows up in the raw/key coverage — this guard fails the run rather than shipping a sparse column.

## Approved revisions (2026-06-15)

Changes made and approved during implementation, captured here so the spec is the source of truth:

1. **Pressure → ERA5.** Removed the PRISM temperature/continentality proxies; both sub-scores are now measured directly from ERA5 hourly MSLP (diurnal = mean daily max−min; synoptic = std of daily means). Reason: temperature is a poor proxy for the health-relevant frontal-pressure signal, especially in maritime storm tracks.
2. **Dryness → rainfall + humidity composite** (was annual mean dewpoint). `0.5·pct(annual precip) + 0.5·pct(annual mean RH)`, inverted; dewpoint kept as a raw. Reason: dewpoint (absolute humidity) is temperature-confounded and conflates cold-dry with arid-dry; mould propensity tracks year-round moisture, which precip + RH capture. (Annual RH, not warm-season — the PNW's mould risk is year-round dampness, not summer mugginess.)
3. **Temperature comfort → percentile rank** of the weighted index (was a linear map). Keeps the 2× heat penalty but spreads the distribution and puts both extremes low.
4. **Wildfire → FSim 270 m burn probability** (RDS-2016-0034-3, 1.5 GB) instead of the 30 m Wildfire-Risk-to-Communities raster (RDS-2020-0016, 32 GB) — identical for a centroid screen; neighborhood point-sampled to handle nodata over developed land.
5. **Sunlight → Global Solar Atlas GHI raster** (the NREL per-point API doesn't scale to 31k and was unreliable); NREL kept as a fallback.
6. **Lyme** (renamed from "ticks") is scored on Lyme-disease **incidence rate**: CDC reported Lyme cases by county (2023) ÷ 2020 county population (cases/100k). Zero-inflated, so 0-case counties get the top score and positive-incidence counties are inverse-rank-scored among themselves. Tick establishment is retained only as a context raw. The rate denominator is ACS 2023 5-year county population (current geography), not the 2020 decennial, so it aligns with TIGER 2023 and the CDC 2023 file on Connecticut's planning regions. (Approved 2026-06-16: the user cares about Lyme incidence / Lyme-carrying ticks, not ticks per se.)
7. **PRISM** is read from the v2 data directory (`data.prism.oregonstate.edu`); the old NACSE web-service endpoints were retired in 2024.
8. Four **accepted anchor divergences** documented above (Olympia synoptic, International Falls comfort, Hartford dryness, Santa Fe Lyme) — metric-vs-anchor tensions, not bugs; metrics left unmanipulated.

Synoptic was kept as the spec's std-of-daily-mean (not switched to a frequency metric), and temperature comfort kept the 2× heat penalty — both approved as-is.

### Incremental additions (2026-06-17)

`tools/generate_candidate_data.py` now caches each criterion's raw columns (`data_pipeline/cache.py`) so adding a criterion recomputes only the new one; scores/metadata always recompute from cached raws. `--seed-cache` populates the cache from an existing matrix; `--force` recomputes named stages.

1. **Smoke (observed PM2.5)** — added as an **independent** `score_smoke` alongside `score_wildfire` (approved: local burn hazard and breathing transported smoke are different concerns, like the two pressure sub-scores). EPA AQS daily PM2.5 (88101 + 88502, 2021–2023): days/yr with 24-hr mean > 35 µg/m³ per monitor, averaged over years, assigned to each town's nearest monitor (monitor distance kept as a context raw; coverage is sparse/urban-biased). *(Superseded 2026-06-22 — replaced by chronic PM2.5 air quality; see below.)*
2. **Pollen excluded from the matrix** (approved 2026-06-17: "misleading data is worse than no data"). No national column — too location-specific to pre-compute; see the not-pre-computed list above for the data-availability rationale.
3. **Elevation** added as a key column (`elevation_ft`), Copernicus DEM GLO-90. Computed as a cached step so it does not invalidate the base or any criterion cache. *(Source changed 2026-06-22 — see below; originally the Open-Meteo Elevation API.)*
4. **Nature access / isolation split into two independent sub-scores** (approved: they diverge — a national-park gateway town has great access but isn't isolated). **`score_isolation` is done**: regional remoteness = 0.5·pct(low population within 50 km) + 0.5·pct(far from nearest city ≥ 50k pop), derived from the places/population table (no new download). Population-within-radius is used rather than the town's own density so a low-density suburb inside a metro is correctly not isolated. **`score_nature_access` is done**: 0.5·pct(close to nearest large GAP 1/2 protected area ≥100 km², PAD-US 4.0) + 0.5·pct(natural land-cover fraction within 10 km, NLCD — forest/shrub/grass/wetland, water/nodata excluded from the denominator). PAD-US is read from the public ArcGIS feature service (the ScienceBase GDB is captcha-gated); NLCD is a provide-once local raster (MRLC ships CONUS only as multi-year bundles).
5. **`score_airport`** — great-circle distance to the nearest US large/medium commercial airport (OurAirports, type large/medium + scheduled service), percentile-ranked, closer = higher. Drive-time (OSRM) is location-specific and not pre-computed here.
7. **`score_amenities`** — distance to the nearest Costco warehouse (OSM Overpass, brand:wikidata Q715583), percentile-ranked, closer = higher. Per the user, Costco alone is used as the amenity proxy (the OSM-café half was dropped). Low weight by design.
6. **Graceful degradation:** a criterion whose data source is unavailable is skipped with a warning and omitted from the matrix, rather than aborting the whole run (so e.g. a missing NLCD raster doesn't block the other criteria).

### Air quality → chronic PM2.5 (2026-06-22)

Replaced the observed wildfire-**smoke-days** criterion (EPA AQS PM2.5 days > 35 µg/m³, nearest monitor; `score_smoke`) with **chronic annual-mean PM2.5** sampled from a gridded satellite-derived surface (WashU ACAG, van Donkelaar et al., ~0.01° CONUS); the criterion is renamed **`score_air_quality`** (lower PM2.5 → higher score, percentile-ranked).

Reason: the smoke-days metric largely **duplicated `score_wildfire`** — PM2.5 spikes are mostly smoke, which tracks fire-prone geography — and it was monitor-limited and urban-biased (rural towns inherited a distant monitor), measuring acute episodes rather than the chronic exposure that drives long-term health burden. Annual-mean PM2.5 de-correlates from wildfire (its geography is traffic/industry/agriculture/wood-smoke/secondary aerosols — Central Valley, Ohio Valley, the mid-Atlantic corridor), gives full rural grid coverage, and is the standard air-pollution-epidemiology exposure metric.

Trade-offs accepted: (a) loses the **transported-smoke episode** signal the observed metric caught (e.g. the June 2023 Canadian smoke over the Northeast/Midwest, which the US FSim wildfire layer can't see) — an acute-event concern better handled per-location later; (b) **ozone is not included** — keeping the axis single-pollutant and interpretable rather than folding pollutants into a composite AQI (a composite is a max-of-sub-indices, still largely PM2.5-driven, and less legible). The PM2.5 surface is a **provide-once** local grid (the research-grade surfaces are portal/Box-hosted with no stable direct URL), resolved via `PM25_RASTER` / `data/raw/pm25/*.{tif,nc}` / `PM25_GRID_URL` — sampled as a GeoTIFF (rasterio) or a NetCDF (xarray; the ACAG North-America `GWRPM25` surface).

### Elevation → Copernicus DEM tiles + coverage guard (2026-06-22)

Replaced the **Open-Meteo Elevation API** with direct sampling of **Copernicus DEM GLO-90** (90 m) 1°×1° Cloud-Optimized GeoTIFF tiles from the AWS Open Data bucket (`s3://copernicus-dem-90m`, public, no auth). Reason: the rate-limited API silently returned only ~4,200 of 31,519 elevations (it never checked HTTP status, so throttled batches yielded empty results that were recorded as nulls). The pipeline now buckets towns by their 1°×1° tile and downloads only the few hundred tiles that actually contain towns (cached under `data/raw/elevation/`; ocean tiles don't exist and are treated as no-data), then samples each tile locally with the standard raster sampler — no API, no full-CONUS mosaic.

A **data-coverage check** was added to the smoke test at the same time: any key column expected to be complete (incl. `elevation_ft`) or any `raw_` column that ran must clear a non-null-fraction threshold (`COVERAGE_MIN_KEY` = 99%, `COVERAGE_MIN_RAW` = 95%, per-column overrides via `COVERAGE_OVERRIDES`). Because `score_` columns are median-imputed, this is what catches a silent upstream gap like the elevation one.

### Politics criterion added (2026-06-22)

Added **`score_politics`**: the Democratic share of the two-party **2024** presidential vote — Dem / (Dem + Rep) — in the **precinct** containing each town centroid, percentile-ranked so more Democratic-voting towns score higher. Precinct-level (not county) was chosen deliberately so a left-leaning town inside a right-leaning county (college towns, state capitals) is captured — county returns would blur exactly the cases of interest. Single most-recent cycle, no multi-year averaging (per the requirement).

Source: the NYT national 2024 presidential precinct **TopoJSON** (`precincts-with-results.topojson.gz`), which carries both precinct geometry and the `votes_dem`/`votes_rep` columns, spatially joined to each centroid. (The sibling `.csv.gz` is results-only — no geometry — so it can't be used.) It's a large (~1 GB) **provide-once** layer, resolved via `POLITICS_PRECINCT_FILE` / `data/raw/politics/` / auto-download from `POLITICS_PRECINCT_URL`; the loader reads geo files including gzipped ones (`.topojson[.gz]`/`.geojson[.gz]`/`.gpkg`/`.shp`, via GDAL's `/vsigzip/`) or a `.csv[.gz]` that contains a WKT geometry column, and auto-detects the Dem/Rep vote columns. The metric measures how an area *votes*, not residents' policy ideology directly (a survey-MRP ideology layer was considered but only covers places ≥25k population). Towns whose centroid lands outside any precinct are left null and flagged by the coverage check.
