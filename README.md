<div align="center">
  <img src="Logos/amon-hen-logo-primary.jpg" alt="Amon Hen" width="150">
  <h1>Amon Hen</h1>
  <p><em>Monitor active wildfires. Project where they go next.</em></p>
</div>

---

Amon Hen turns open-source Earth observation into decisions. It ingests
satellite active-fire detections and fire weather, clusters them into incidents a
human can reason about, and then projects where each one could go — as an
ensemble of scenarios rather than a single arrow, because the honest answer to
"where will this fire be in six hours" is a range.

**Current focus:** Greece.

```bash
docker compose up
```

Then open **http://localhost:3000**. No API keys, no accounts, no setup — the
platform ships with a realistic demo dataset and tells you plainly that it is
using it. Add a free NASA key and the same screens become live.

---

## What it does today

| | |
|---|---|
| **Ingests** | NASA FIRMS active fire detections (VIIRS 375 m, MODIS 1 km) and Open-Meteo fire weather |
| **Measures fuel** | Vegetation per fire from CORINE Land Cover, so a pine stand and a ploughed field no longer spread identically |
| **Measures terrain** | Slope and aspect from the Copernicus DEM, so projections run over the real hillside instead of imaginary flat ground |
| **Measures the burn** | Burned area and severity from Sentinel-2 dNBR at 20 m, replacing a 375 m pixel count that was explicitly a lower bound — validated at 9,910 ha against the 2024 Varnavas fire's reported ~10,000 ha |
| **Measures dryness** | Live fuel moisture from Sentinel-2 NDMI, sampled in a ring *around* the fire so it reads the fuel ahead of the front rather than the scar behind it |
| **Screens** | Flags heat detections that behave like factories or flares rather than fires — weak, night-only, never moving |
| **Clips** | Detections are cut to an actual Greece polygon, not the bounding box FIRMS forces you to ask with — in live data that box was 92% foreign fires |
| **Clusters** | ST-DBSCAN turns scattered pixels into named incidents that survive the ~24 h gaps between satellite passes |
| **Scores** | Canadian Fire Weather Index (FFMC/DMC/DC/ISI/BUI/FWI), the system Copernicus EFFIS runs on, warmed up over 45 days of real weather |
| **Projects** | Rate of spread, direction, and length-to-breadth from the FBP equations, with an explicit confidence band |
| **Ranks** | What is downwind, how far, how soon — weighted by life safety first |
| **Overlays** | Eleven map layers from Copernicus EFFIS and Esri — official EU fire danger, seasonal anomaly, spread speed, vegetation, terrain relief, settlements — each with its own opacity |
| **Projects** | A nine-case ensemble around the measured fuel, wind and terrain, drawn as 1 h / 3 h / 6 h envelopes |
| **Explains** | Every incident gets a plain-language brief that states its own uncertainty |

## Screens

The platform does one thing: watch the fires that exist and project where they
could go. There is no phase navigation, because there are no other phases to
navigate to.

- **Incident list** — sortable by severity, area, growth rate or recency, with
  the single most urgent downwind threat surfaced per row.
- **Map** — MapLibre + deck.gl. Detections coloured by Fire Radiative Power and
  faded by age, approximate perimeters, incident markers with one-hour spread
  vectors, and exposure points. Dark or satellite basemap.
- **Projections** — the ensemble, drawn as nested envelopes. Three views, and
  the distinction is the point: **could reach** (union of all nine scenarios —
  what you evacuate against), **all agree** (intersection — usually far smaller
  than people expect), and **each** (every scenario separately, to see which
  assumption drives the shape).
- **Dossier** — the brief, key figures, live fire weather, the full FWI
  breakdown, and the ensemble reported as a *range* of forward rates rather than
  one number. What is at risk is expressed as scenario agreement — "Megara, 1 of
  9 scenarios, earliest 6 h" — not a single deterministic ETA.
- **Copernicus overlays** — EFFIS layers stacked over the basemap with per-layer
  opacity, behind a collapsed **Layers** dropdown so the control costs no map
  space when you are not using it. Its contents are ordered the way the map is
  stacked: overlays on top, then our fire data, then the basemap choice
  underneath. Keyless and served straight to the browser, so they cost the
  backend nothing.
- **Legends** — every enabled overlay contributes its own EFFIS colour key to a
  panel on the map, showing the real class breaks. They sit outside the dropdown
  on purpose: you need a legend while reading the map, which is exactly when the
  dropdown is shut.

## Language

The interface is written for someone who fights fires, not someone who studies
them. Fire danger is "Fire danger", not "FWI"; the FFMC is "Surface dryness"; an
`informational` incident is one to "Watch". The precise term is kept in the
tooltip for anyone who wants to look it up, and the domain model underneath is
unchanged — translation happens once, at the display boundary, in
`frontend/lib/labels.ts`.

## How fresh the data is

| Source | Updates | Amon Hen refreshes |
|---|---|---|
| NASA FIRMS detections | ~3 h (Europe, NRT) | every 15 min |
| Open-Meteo fire weather | hourly | every 30 min |
| Copernicus EFFIS overlays | daily | date decided server-side, tiles cached 1 h |
| Copernicus DEM terrain | never | cached 30 days |
| CORINE land cover | ~6 years | cached 30 days |

The screen polls every 60 s, so a background update appears without a reload.
**Refresh** forces the whole chain immediately *and bypasses the source caches* —
a button labelled "Refresh" that returns a ten-minute-old cached response is
lying to the person pressing it. Terrain is deliberately exempt: the ground does
not move.

## Getting live data

Everything works without this. To upgrade from demo data to live satellites:

1. Get a free FIRMS key (issued instantly): https://firms.modaps.eosdis.nasa.gov/api/map_key/
2. `cp .env.example .env` and set `AMONHEN_FIRMS_MAP_KEY=...`
3. `docker compose restart api`

The amber banner across the top disappears when every feed is live. Weather is
already live out of the box — Open-Meteo needs no key, which is exactly why it
is the default.

### Turning on satellite imagery

Burned-area mapping and live fuel moisture read Sentinel-2 imagery, which needs
`rasterio` — the one genuinely heavy dependency here, because it carries GDAL.
It is off by default and needs **no key**:

```bash
make build-eo
```

That rebuilds the API image with the `[eo]` extra (165 MB, a couple of minutes)
and restarts it. Everything else works without it; fires simply report "estimated"
area and say why there is no measurement. `make status` shows a note against the
`sentinel2` source when it is not installed.

## Layout

```
backend/          Python — FastAPI, the science, the ingest
  amonhen/
    core/         Config and logging. Nothing else reads os.environ.
    domain/       The ontology: entities, events, links, controlled vocabularies
    sources/      One module per external feed, each with a fixture fallback
    services/     The intelligence: FWI, clustering, spread, exposure, operations
    api/          Routes and wire schemas
    ingest/       Scheduled refresh
  tests/          55 tests, including FWI validated against Van Wagner (1987)
frontend/         Next.js 16, React 19, Tailwind 4, MapLibre 6, deck.gl 9
data/fixtures/    The bundled demo scenario
docs/             Architecture and data source notes
```

## Common tasks

```bash
make up          # start everything
make status      # service health + which feeds are live vs fixtures
make test        # backend test suite
make logs        # tail everything
make build-eo    # rebuild with Sentinel-2 support (burned area + fuel moisture)
make fixtures    # regenerate the demo dataset
make boundary    # rebuild the Greece polygon from Natural Earth
make down        # stop
```

## Design commitments

These are the rules the code is written to, and the reasons behind them.

**Never require setup to see the product.** Every data source falls back to a
bundled fixture. A platform you cannot evaluate without three accounts and an
afternoon is a platform that does not get evaluated.

**Never let the UI lie about its own confidence.** Approximate perimeters are
labelled and styled as approximate. Spread estimates carry their caveats into
the interface, not just the docstring. A fire that satellites have not seen for
ten hours is "contained", never "out". Demo data announces itself permanently.

**Never crash because an upstream feed is down.** A failed source logs loudly
and returns nothing; the map still renders, just staler.

**Keep the science inspectable.** Severity is transparent arithmetic, not a
learned model — when this drives an evacuation recommendation, somebody has to
explain it to a mayor, and "the model said so" is not an explanation.

## Is it actually a fire?

Satellites detect **heat, not fire**. A thermal anomaly is equally consistent
with a burning forest, a steel works, a gas flare or a landfill. FIRMS does not
try to tell them apart, so an unfiltered list quietly mixes wildfires with
industry — in a typical Greek picture, more than half the "incidents".

The tempting fix is to cross-check against Copernicus EFFIS. **It does not
work**: EFFIS active-fire hotspots come from the same MODIS and VIIRS sensors we
already ingest, so agreement proves only that we both read the same pixel.

Burnt-area products *are* independent evidence — vegetation is either gone or it
is not — but they are weakest exactly where the noise is. EFFIS maps burnt areas
above roughly 30 ha, hours to days later. A false positive leaves no scar; so
does a real 5 ha fire that started this morning. Absence cannot distinguish them.
Burnt area confirms large fires; it cannot filter small ones.

What does work, from data already in hand, is **behaviour**:

| A wildfire | An industrial heat source |
|---|---|
| Burns hottest mid-afternoon | Often only detected at night, against a cold background |
| Tens to hundreds of MW | A few MW at most |
| Moves and grows | Never moves |
| Burns out within days | Is there again next week |

A worked example from the live feed: six detections within 300 m of the
Thessaloniki port, 0.3–1.5 MW, every one between 00:18 and 01:37, across four
consecutive nights, seen by three different satellites. Nothing about that is a
wildfire.

Scoring is transparent arithmetic with named reasons, never a classifier —
when this contradicts an operator, they can see exactly why and overrule it.
**Suspect incidents are flagged and dimmed, never hidden by default.** Hiding
them unasked would eventually hide a real fire that happened to look odd, and
nobody would know to go looking.

## Known limits

Stated plainly, because knowing where a tool stops is part of using it:

- **Spread is a triage estimate, not a simulation.** Flat ground, uniform fuel,
  steady wind. Real terrain routinely doubles uphill spread; spotting and crown
  fire can outrun the model entirely. It is for ranking fifteen simultaneous
  fires, not for planning a burn.
- **Fuel models are borrowed.** Which model applies is now measured from CORINE,
  but the coefficients inside each one were still derived for Canadian boreal
  forest. The Mediterranean mappings in `services/spread.py` are the
  closest published analogues and are the largest single source of error.
  Calibrating them against Greek fire records is the highest-value next step.
- **No fuel continuity.** The model will happily carry a fire across a strait,
  a motorway or a ploughed field. Treat cross-water ETAs as nonsense.
- **Burned area is a lower bound *until a satellite sees the scar*.** Fire
  between overpasses is invisible to a thermal pixel count. Once Sentinel-2
  gets a clear look the figure becomes a 20 m measurement and the dossier says
  "measured" instead of "estimated" — but that takes 2-3 days, longer under
  cloud, and needs the optional `[eo]` extra installed. A fire that started this
  morning has no scar to measure.
- **Live fuel moisture is uncalibrated.** The NDMI ends of the mapping in
  `services/fuel_moisture.py` were measured over eight Greek sites across a
  season, but the live-fuel-moisture values they map onto are literature figures
  for Mediterranean vegetation, not Greek field measurements. The resulting
  spread correction is clamped to ±35% precisely because it is first-order.
- **The gazetteer is a seed**, not a national dataset — about fifty places.
  Import the real thing from OpenStreetMap before relying on exposure counts.
- **The boundary is simplified** to ~220 m and carries a 3 km coastal buffer, so
  a detection within a few kilometres of the coastline or a land border may be
  counted as Greek when it is not, and vice versa. `AMONHEN_BOUNDARY_MARGIN_KM`
  deliberately widens this to 5 km, because fires cross borders.
- **The fire/not-fire test can be wrong both ways.** A small real fire seen only
  at night will be flagged; a large industrial flare will not. It is a triage
  aid, not a filter — which is why nothing is hidden unless you ask.
- **Detection latency is hours, not minutes.** FIRMS NRT publishes ~3 h after
  the pass for Europe. This sees a fire run; it does not see it start.

## Where it goes next — accuracy, in priority order

The framing that has held up: **more spectral bands help size a lot and
direction barely at all.** Size is an observation problem, and better observation
fixed it — dNBR now measures burned area to within about 1% on a fire with a
known answer. Direction is a physics problem driven by wind, terrain and fuel,
none of which is a band, and it remains the least certain thing here.

The one band-derived input that does touch spread is live fuel moisture, and it
earns a bounded correction rather than a starring role.

**~~1. Terrain.~~ Done.** Copernicus DEM slope and aspect now feed every
scenario, in each scenario's own direction of travel.

**~~1. Fuel type per location.~~ Done.** CORINE Land Cover 2018 now supplies the
vegetation at each fire, via the EEA's ArcGIS `identify` endpoint — WMS
`GetFeatureInfo` returns nothing for raster layers, which is why the EFFIS fuel
map could be seen but not used.

**1. Calibrate the fuel coefficients against Greek fire records.** The mapping
from vegetation to spread behaviour still uses FBP coefficients derived for
Canadian boreal forest; only the *choice* of model is now measured, not the
model itself. This is the highest-value modelling work remaining, and it needs
historical Greek fire perimeters to fit against.

**~~2. Burned area from Sentinel-2 dNBR.~~ Done.** Burn scars are delineated at
20 m from pre/post NBR differencing and classified by the Key & Benson severity
thresholds. Measured area supersedes the thermal-pixel count, and the measured
scar supersedes the detection hull the UI used to hedge about — the map now
draws the two differently and the dossier labels every area figure "measured" or
"estimated". Validated at 9,910 ha against the 2024 Varnavas fire's reported
~10,000 ha.

**~~3. Live fuel moisture.~~ Done.** Sentinel-2 NDMI, sampled in a ring around
the fire rather than over it, so it measures the fuel the fire is running *into*
instead of the scar it has already left. It feeds the spread model as a bounded
multiplier rather than into the FWI moisture codes, because those describe *dead*
fuel moisture and live moisture is a different physical quantity the model omits
entirely — adding it is filling a gap, not overwriting an inference.

**2. Calibrate live fuel moisture against Greek field measurements.** The NDMI
ends of the mapping are measured; the moisture values they map onto are
literature ranges. This is the same shape of problem as the fuel coefficients
above, and the same fix: field data.

**3. Persistence.** PostGIS is in `docker compose --profile persistence` but not
wired in. History unlocks growth curves, replay and after-action review — and
lets the spread model be scored against what actually happened, which is the
only way any of the above gets validated rather than merely improved.

## Attribution

Active fire data: **NASA FIRMS** (LANCE/EOSDIS). Weather: **Open-Meteo**
(ECMWF IFS / DWD ICON). Terrain: **Copernicus DEM GLO-90**. Vegetation: **CORINE Land Cover 2018** (Copernicus / EEA). Fire danger, fuel and
behaviour overlays: **Copernicus EMS — EFFIS / GWIS**. Relief: **Esri World
Hillshade**. Boundaries: **Natural Earth** (public domain). Basemaps: **CARTO**
and **Esri World Imagery**.
Fire danger: the **Canadian Forest Fire Weather Index System**, Van Wagner (1987).
