# Weighting & Visualization App — Spec

## Purpose & form factor

A single static web page (`index.html`) that lets you (1) inspect the quality of the
scoring matrix and (2) rank candidate towns by weighted criteria, adjusting weights live to
explore trade-offs.

- **Client-side only.** Plain HTML/CSS/JS with canvas rendering — no framework, no build
  step, no server, no account. It loads its data at runtime with `fetch`, so it is served
  over http(s) (a local static server or a static host); it does not embed the data.
- **Two tabs:** **Explore & Weight** (the default view) and **Data Quality**.
- Keep it fast and legible — an analysis instrument, not a marketing page. No decorative
  animation, no 3D, no splash screens.

## Data inputs

Fetched on load:

- `output/candidate_scores.csv` — one row per town. Identifying columns:
  `place_geoid, name, state, county, county_fips, lat, lon, elevation_ft, population,
  land_area_sqmi`. Then, per criterion, a `raw_<criterion>` and a `score_<criterion>`
  (0–100, higher = better fit). Pressure is two independent criteria,
  `score_pressure_diurnal` and `score_pressure_synoptic`.
- `output/column_metadata.csv` — `column, description, units, source, source_date,
  normalization_method`.
- `assets/basemap.json` + `assets/relief.png` — the map context layers (see Basemap).

**Metadata-driven, not hardcoded.** Criteria are discovered by scanning the CSV header for
`score_*` columns; each criterion's human label/units come from `column_metadata.csv`, and
its `raw_*` columns are the ones preceding it in the header. Any new `score_*` column
therefore becomes a new toggle, slider, map layer, histogram, and detail row automatically,
with no code change. If a referenced file is missing or empty, show a clear message (and
note that the page must be served over http, not opened as a `file://`).

## Explore & Weight (default tab)

**Criteria panel.** One row per discovered criterion, laid out on two lines so nothing is
truncated at narrow widths: line 1 is an on/off toggle and the full criterion name; line 2
is the importance slider (0–10, default 5) with its current value and an optional floor
input. **Select all / Select none** buttons toggle every criterion at once. Disabled
criteria are dimmed and drop out of the math entirely. Hovering a criterion row temporarily
previews that single layer on the map.

**Composite score.** For each town, over the set of enabled criteria E with weights w_i:

```
composite = Σ(w_i · score_i) / Σ(w_i)      for i in E
```

A weighted average on the 0–100 scale, so toggling a criterion or changing weights never
blows up the scale. Recomputed live on every slider move.

**Hard-floor filters (separate from weights).** Each criterion has an optional minimum
score; towns below any active floor are excluded from the ranking and map. Floors gate,
weights rank — kept visually distinct from the importance sliders.

**State filter (global).** A type-to-filter combobox (full state names) in a control bar
above the map. Selecting a state restricts the map, the hover/selection hit-testing, the
ranked list, and the export to that state, and auto-zooms the map to it. A population floor
is also offered as a display filter to thin tiny CDPs.

**Ranked results.** A table sorted by composite: rank, town, state, composite, population
(top N, N configurable). Selecting a town — from the table or the map — populates the
**town detail** column.

**Town detail.** A persistent right-hand column (placeholder until a town is selected; a
close ✕ clears the selection). It shows the town's name, county, population, elevation, and
coordinates, its composite and rank, and a per-criterion breakdown: each criterion's raw
value (with units from metadata), its 0–100 score, and its weighted contribution to the
composite — so the ranking is explainable, not a black box.

**Map.** A US map of all towns drawn to a single canvas, colored by the current composite
and redrawn live as weights change, so good *regions* (clusters) are visible. Supports
scroll-to-zoom and drag-to-pan (with a Reset view button); town markers are circles that
scale up as you zoom; the selected town is highlighted prominently; hovering a town shows
its name and composite. Selecting on the map or in the table cross-highlights the other. A
basemap toggle shows/hides the geographic context layers. A centered legend labels the
score color ramp.

**Export.** "Export shortlist" → CSV of the top N towns (respecting the active state
filter) with their composite, per-criterion scores, and the weight/floor profile used.

**Weight profiles.** Save named weight + floor profiles (persisted in `localStorage`) and
reload or delete them. A compare view picks two profiles and shows how their top 10 differ.

## Data Quality tab

1. **Per-layer maps (projection check).** Small-multiple US maps, one per `score_*` column,
   each town plotted at its lat/lon and colored by that score. The eyeball test: `score_sunlight`
   should show the bright-Southwest / dark-Pacific-Northwest gradient; a layer that looks
   like random static means a raster was sampled in the wrong projection. Canvas-rendered.
2. **Per-criterion histograms.** Distribution of each `score_*` column, with min/median/max
   and null count, flagging any layer that is degenerate, heavily clustered, or null-heavy.
3. **Anchor-town panel.** Smoke-test anchors checked against expected bands (HIGH = top
   quartile / ≥ p75; LOW = bottom quartile / ≤ p25), each marked pass/✗ with its value and
   the cutoffs on hover:
   - Olympia, WA — LOW: sunlight, dryness, pressure-synoptic
   - San Luis Obispo, CA — HIGH: temperature comfort, pressure-diurnal, pressure-synoptic
   - Santa Fe, NM — HIGH: sunlight, dryness, Lyme; LOW: pressure-diurnal
   - Phoenix, AZ — HIGH: sunlight, dryness; LOW: temperature comfort
   - International Falls, MN — LOW: temperature comfort, sunlight
   - Hartford, CT — LOW: Lyme, dryness

   A handful of these are honest consequences of the chosen metrics rather than bugs (e.g.
   Olympia's synoptic pressure ranks mid because maritime air moderates swing *amplitude*;
   International Falls' temperature comfort sits below average but not bottom-quartile under
   the 2× heat penalty; Santa Fe's Lyme is very low but not the exactly-zero top mass;
   Hartford's dryness is mid-pack on the national wetness composite). The panel reports the
   real result; it does not fudge the data to force a pass.
4. **Data-health summary.** Row count, criteria count, total nulls, and per-criterion
   min/median/max with quality flags.

## Basemap

The map context is generated offline by `tools/build_basemap.py` and fetched as two files:

- `assets/basemap.json` — vector layers in lon/lat: coastline (GSHHS intermediate, so bays
  and sounds appear), state borders (TIGER counties dissolved per state, clipped to land),
  major rivers, border-defining lakes (Great Lakes, etc.), and Interstate highways.
- `assets/relief.png` — a shaded-relief raster reprojected to the map's Albers extent, with
  land shaded and ocean/bays tinted as water.

All layers are drawn subtly so the town scores stay the most prominent thing; the basemap
toggle hides them entirely. Both files are baked to the same projection/extent the app uses
so they register with the town dots at every zoom level.

## Projection & performance

~31,500 Census places. The weighted-average recompute per slider tick is sub-millisecond.
The map renders all points to a single canvas (Albers USA-style conic projection), redrawn
on change — never one DOM element per town. Histograms bin first, then draw. Zoom/pan apply
a single transform over the same canvas. Vector basemap layers are pre-simplified so a full
redraw during a slider drag or pan stays smooth.

## Scope — deliberately out

No routing/drive-time calculation in the app (that belongs in a pipeline column), no
listing data, no account/login, no server. Everything runs client-side off the fetched CSVs
and basemap assets.

## Acceptance checks

- Every `score_*` column in the CSV produces a working toggle + slider with the correct
  label/units from metadata; adding a new `score_*` column makes a new control, map layer,
  histogram, and detail row appear with no code edit.
- The anchor panel reports each expected-band check truthfully; documented metric-driven
  divergences are surfaced as such rather than hidden.
- Under an all-equal weighting the ranking is sensible (no single broken layer dominating);
  disabling and re-enabling a criterion returns the same ranking.
- The composite map redraws smoothly while dragging a slider, panning, and zooming with the
  full row set loaded.
- The page loads its data over http and shows a clear message if a data file is missing.
