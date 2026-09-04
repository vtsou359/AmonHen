# Architecture

## The shape of the thing

Amon Hen is a pipeline with a map on the end:

```
  NASA FIRMS ──┐
               ├─► sources/ ──► clustering ──► incidents ──┐
  Open-Meteo ──┘   (fetch,       (DBSCAN in    (named,      │
                    cache,        space AND     scored)     │
                    fallback)     time)                     │
                                                            ▼
                            fire_weather ──► spread ──► exposure ──► brief
                            (FWI codes)     (FBP ROS)   (ranked)    (prose)
                                                            │
                                                            ▼
                                            api/ ──► GeoJSON layers + dossiers
                                                            │
                                                            ▼
                                     Next.js · MapLibre · deck.gl
```

Each arrow is a module boundary, and each module is independently testable with
plain data in and plain data out. There is no dependency-injection framework, no
plugin registry and no event bus — at this size they would be more machinery
than the problem justifies.

## The ontology

Like Palantir's Gotham, the system is organised around a shared vocabulary
rather than around tables or endpoints. `domain/entities.py` defines it:

- **Events** — things that happened at an instant. `Detection` (a satellite
  thermal anomaly), `WeatherObservation`.
- **Entities** — things that exist over time. `Incident`, `Perimeter`, `Asset`,
  `ExposedElement`, `BurnAssessment`.
- **Links** — typed edges. `Incident --threatens--> Settlement`,
  `Asset --assigned_to--> Incident`.

`domain/enums.py` holds the controlled vocabularies and is exported over the API
at `/api/v1/system/ontology`, so the frontend never hardcodes a status string.
When "contained" needs to mean something specific, it means it in one place.

**Everything carries provenance.** Every entity has an optional `Provenance`
recording source, retrieval time and URL. This is not bureaucracy: the first
question anyone asks about a number on an emergency screen is "says who, and how
old is it?"

## Layering

```
core/       Config and logging. The ONLY module that reads the environment.
domain/     Pure vocabulary. Imports nothing but pydantic.
sources/    External feeds. Import domain. Never import services.
services/   The intelligence. Import domain and sources.
api/        HTTP. Imports services. Contains no logic of its own.
ingest/     Scheduling. Calls services.
```

Dependencies point strictly downward. `domain` knows nothing about HTTP or
Postgres, which is what lets the science be tested without either.

## Why the picture lives in memory

The live operational picture is a few hundred objects and is rebuilt from
scratch each cycle, so a database round-trip buys nothing. `OperationsService`
holds it behind an `asyncio.Lock` — without the lock, ten simultaneous page
loads on a cold cache each trigger a full rebuild and hit NASA ten times for the
same bytes.

PostGIS is for *history*, which is large, needs indexing, and is not yet wired
in. It ships in `docker compose --profile persistence` so that work can start
immediately. The consequence worth knowing: **restarting the API loses nothing
but the cache**, and gains nothing either — there is currently no history.

## Why weather is fetched per incident

An early version fetched one national weather reading. Every fire on the map
then reported the same spread rate in the same direction — which is not merely
imprecise, it is actively misleading, because the wind on Rhodes has nothing to
do with the wind over Parnitha. Fires are far enough apart that each needs its
own atmosphere. Requests are concurrent and cached for 30 minutes.

## Why spatio-temporal DBSCAN

Three properties of the problem force the choice:

1. **We do not know how many fires there are** — that is the answer we want, so
   k-means is out.
2. **Fires are not convex blobs.** They are L-shapes and horseshoes carved by
   terrain and wind. DBSCAN grows clusters by density, so shape is free.
3. **Isolated pixels must be rejectable.** Industry, agricultural burns and sun
   glint all produce single detections. DBSCAN's noise class handles this; every
   partitioning method would force them into a fire.

Time must be *inside* the metric. Two detections 2 km apart on one afternoon are
one fire; the same two ten days apart are two.

The first implementation folded time into a third spatial axis and ran plain
Euclidean DBSCAN. That is subtly wrong: it makes the two thresholds trade off
against each other, so a pair 2 km apart with a 10 h gap was rejected under a
3 km / 12 h setting even though each offset was well inside its own limit — and
tuning `eps_km` silently retightened the effective time window.

It is now true **ST-DBSCAN**: two detections are neighbours when they are within
`eps_km` *and* within `eps_hours`, as two independent tests. The spatial pass
builds a sparse neighbour graph with a KD-tree, the time mask is a vectorised
filter over its edges, and DBSCAN runs on the result with `metric="precomputed"`
— still O(n log n).

Defaults: **3 km, 30 hours, 2 detections minimum**. The 30 hours is measured,
not guessed. Gaps between consecutive detections of the same burning ground in
live Greek FIRMS data pile up at ~24 h (the daily polar-orbit revisit); the
original 12 h window split 10.5% of them, fragmenting one fire into roughly one
incident per day. On a live sample this took the picture from 62 incidents to
45, merging duplicates like "38.677N 29.211E · 26 Aug" and "38.675N 29.215E ·
27 Aug" — the same fire, 400 m apart, listed twice.

## The area of interest is a polygon, not a box

FIRMS is queried with a bounding box, and no rectangle around Greece exists that
does not also contain western Turkey, Albania, North Macedonia and southern
Bulgaria. In live data that was **92% of all detections** — foreign fires the
gazetteer could not even name, crowding genuine Greek incidents out of the list.

So the bbox stays (FIRMS needs one) and detections are clipped afterwards
against a real country polygon, built by `scripts/build_boundary.py` from
Natural Earth 10m. Two corrections are applied, both found by validating rather
than assuming:

* **A 3 km coastal buffer.** Town centroids sit marginally seaward of a
  simplified coastline: Mati, Chalkida and Alexandroupoli all fell *outside* a
  strict polygon by 0.01–0.34 km. Calibration showed 1 km fixes it and 10 km
  starts admitting Edirne, Turkey.
* **Twelve missing islands.** Natural Earth 10m omits several inhabited Greek
  islands; Kastellorizo is 152 km from the nearest landmass it does contain.
  They are unioned back in as discs.

The build script refuses to write a boundary that fails its checks — every one
of the 51 gazetteer places must be inside, and eight foreign cities outside.

`services/boundary.py` fails **permissive**: if the boundary file is missing or
unreadable it logs loudly and accepts everything. Over-reporting foreign fires
is a nuisance; silently dropping a Greek one is a safety failure. The number
dropped is reported at `/api/v1/incidents` so the filtering is visible.

## Area estimation

Two regimes, and the platform is explicit about which one a number came from.

**Before a satellite sees the scar**, area is derived from thermal detections.
Three approaches, one chosen:

| Approach | Problem |
|---|---|
| Sum pixel footprints | Double-counts — consecutive passes re-detect the same ground |
| Convex hull | Over-counts badly on L- and horseshoe-shaped burns, often 2× |
| **Union of footprints on a grid** | **Chosen.** Snap each detection to a cell at sensor resolution, count distinct cells |

The result is an honest lower bound: fire that burns between overpasses is
invisible to it.

**Once Sentinel-2 gets a clear look**, `services/burn_scar.py` replaces that with
a measurement — dNBR at 20 m, delineated and classified by severity. The
measured figure supersedes the estimate, and `Incident.area_source` records
which one is on screen. That flag exists because the two are not the same kind of
number at different precisions: one is a floor, the other is the ground.

Everything derived from area moves with it. `_apply_measured_area` rescores
severity and recomputes the growth rate — dividing by the time to the *image*,
not to the last detection, so a fire does not appear to slow down merely because
a satellite passed early.

The perimeter follows the same rule. A `detection_hull` is a convex sketch drawn
faint and thin; a `sentinel2_dnbr` scar is drawn solid. The map must not present
a 375 m guess and a 20 m measurement with identical styling.

## Live fuel moisture, and where it does not go

The FWI moisture codes (FFMC, DMC, DC) describe **dead** fuel moisture and are
computed from weather. Living vegetation is absent from the model entirely, and
in Greece it is most of the fuel.

`services/fuel_moisture.py` measures it from Sentinel-2 NDMI and applies it as a
bounded multiplier on rate of spread — *not* by writing into the moisture codes.
That distinction is the whole design: live and dead fuel moisture are different
physical quantities, so this is filling a gap in the model rather than
overwriting one of its inferences.

Two constraints worth carrying forward:

**The sample is a ring around the fire, not the fire.** Averaging NDMI over the
burn measures the scar — charred ground has no leaf water — and then feeds that
back as a prediction that the fire will spread fast. Sampling the unburnt
annulus measures the fuel the fire is running *into*, which is the quantity
actually wanted.

**The multiplier is clamped to ±35%, and the clamp is an argument.** A satellite
is watching this fire burn. Whatever an uncalibrated index says, the fuel is
evidently dry enough to carry fire, so the index gets to adjust the rate and
never to overrule the observation.

## Optional dependencies

`rasterio` is the one heavy dependency in the project, and it is optional. Both
imagery products check `EO_AVAILABLE` and degrade to "not measured" — the whole
active-response path (detections, clustering, weather, spread, projection) runs
untouched without it. Installing it must never silently move numbers on fires it
cannot see, which is why the moisture multiplier defaults to exactly 1.0 and is
covered by a test that says so.

## Ensemble projection

A single spread arrow is a lie of precision. It says "south-west at 45 m/min"
when the honest statement is "under assumptions we are forced to make, probably
somewhere in this arc". The three inputs we are least sure of are exactly the
three that decide where a fire goes: **fuel** (one uniform type assumed for a
whole country), **wind** (a 9 km forecast grid, and it veers), and **slope**
(nothing supplies terrain, so every estimate is flat-ground). All three have
since become measured; what "uncertain" means for each has changed, but not
that it is uncertain.

So `services/projection.py` runs nine named scenarios across those uncertainties
and reports the spread of outcomes:

    expected · veer left · veer right · gusting · easing
    lighter fuel · heavier fuel · upslope · worst case

Each produces an elliptical footprint at 1 h, 3 h and 6 h (ignition point at the
rear focus, since a wind-driven fire runs forward far more than it backs). Those
combine into an **envelope** (union — "could reach") and a **core**
(intersection — "every scenario agrees this burns"). The core is usually far
smaller than people expect, which is the most useful thing the ensemble says.

**Why nine named scenarios rather than Monte Carlo.** Ten thousand samples
produce a prettier probability surface, drawn from input distributions we have no
evidence for, that nobody can interrogate. Nine named cases can be reasoned about
— "the upslope case is the one that reaches the village" — and defended to a
mayor afterwards. The honesty is in the naming, not the sample count. For the
same reason the output is called a *likelihood* only in the narrow sense of "how
many of our assumptions lead here", never a probability.

**Fuel is measured, not assumed.** Until CORINE was wired in, every projection
in the country used one fuel model — "maquis" — with coefficients derived for
Canadian boreal forest. An Aleppo pine stand and a ploughed field produced
identical spread. Now each fire's scenarios inherit the vegetation actually
mapped at its location, and the two explicit fuel cases (`light_fuel`,
`heavy_fuel`) become perturbations *around* that measurement rather than guesses
in place of one.

The land cover is also the only independent input to the fire/not-fire test.
Everything else in that scoring — heat output, time of day, persistence — is a
property of the same satellite pixels that raised the alarm. Whether the ground
can burn at all comes from somewhere else entirely, and it is what exposed the
Parnitha and Thessaloniki detections as a quarry and an industrial site.

**Terrain is measured, not assumed.** An earlier version of the ensemble carried
a scenario called "upslope" that applied a guessed 25% grade. That is now
replaced by the real thing: every scenario uses the Copernicus DEM slope at the
fire, and the scenario that remains — "runs up the hill" — tests something a
guess cannot, namely that the 9 km wind forecast may be too strong for a Greek
valley, in which case the slope takes over and the fire heads somewhere the
forecast never would.

**Slope is a wind, not a multiplier.** This is the correction that matters most
in this module, and it is worth stating why the obvious approach fails.

The first implementation multiplied the head rate of spread by Van Wagner's
slope factor. But the ellipse's elongation comes from wind alone: at 2 km/h the
length-to-breadth ratio is 1.02, so the fire is a circle and the head, flank and
back rates are all nearly equal. Multiplying "the head rate" by 5 for a 52%
grade therefore multiplied *every* direction by 5 — including straight downhill.
On a live incident whose measured burn scar was 18 ha, that produced a 40 km
circle covering 125,000 ha, and a six-hour envelope of 267,000 ha. For scale,
the largest fire in EU history burned 93,000 ha over two weeks.

A hill rises one way. So slope is now handled the way the FBP System itself
handles it: converted to the wind speed that would produce the same increase in
spread, then **added to the real wind as a vector**. The conversion is exact
rather than fitted — wind reaches rate of spread only through ISI, as
`exp(0.05039 · W)` — so a slope factor SF is worth `ln(SF) / 0.05039` km/h. A
52% grade comes out at 32 km/h, which is a claim about a hillside that a fire
officer can argue with.

| | before | after |
|---|---|---|
| head rate | 66.9 m/min | 69.2 m/min |
| back rate | 45.3 m/min | **1.2 m/min** |
| length : breadth | 1.02 | **3.83** |
| direction | S (downwind) | **N (uphill)** |
| 6 h envelope | 267,000 ha | **29,200 ha** |

Three things follow, all of them right. The boost points somewhere instead of
everywhere. It elongates the ellipse rather than inflating a circle, because it
enters through the same term wind does. And in light wind on steep ground the
resultant points uphill — which is what fires do, and which the model previously
needed a hand-written special case to express.

The special case for descending ground disappeared with it. There is no downhill
branch any more: the slope vector always points uphill, and a fire being blown
downhill simply has the two vectors partly cancel. That is the physics rather
than a rule about it, and it can now legitimately point a fire *back up* a slope
against a light breeze. The safety margin that the old "treat downhill as flat"
rule provided lives where it belongs — in the `gusting` and `worst_case` members
of the ensemble.

**One correctness trap worth recording.** In the FBP system wind is an *input to*
ISI, and head rate of spread is a function of ISI alone. The first implementation
scaled `wind_speed_kmh` per scenario while reusing the incident's original ISI —
so all three wind scenarios had identical forward speed and differed only in
ellipse elongation, which made the *easing* case cover more ground than the
*gusting* one. Wind has to be fed back through `initial_spread_index()`. There is
a regression test pinning `easing < expected < gusting`.

## The frontend

Next.js 16 App Router, React 19, Tailwind 4 (CSS-first tokens in
`app/globals.css`), MapLibre GL 6 for the basemap, deck.gl 9 for data layers.

- **SWR** polls the API. Cadences match how fast the data can actually change —
  60 s for the picture, not because satellites are faster but to pick up an
  operator's manual refresh.
- **Zustand** holds only what the user has clicked (selection, layer toggles,
  basemap). Data never goes in it.
- **deck.gl over MapLibre** via `MapboxOverlay` rather than `react-map-gl` — one
  fewer version coupling, and the pattern is documented by deck.gl.

### Plain language at the display boundary

The domain vocabulary is precise — `informational`, `ffmc`, `envelope` all mean
something specific, and the backend, tests and ontology depend on them. But
precision in the data model is no reason to make a duty officer learn forestry
jargon at three in the morning.

So translation happens in one place, `lib/labels.ts`, at the display boundary:
`informational` renders as "Watch", `ffmc` as "Surface dryness", the Fire Weather
Index as "Fire danger". Where the technical name is genuinely useful to someone
who wants to look it up, it goes in the tooltip rather than the label. The same
rule governs `sources/overlays.py`: the title says what a layer *tells you*, and
`technical_name` carries the literature term.

### The design system

The brand ramp is sampled from the logo: `#141776` at the apex to `#0F6251` at
the base. The **UI ground is deliberately not on that ramp.** It was originally a
deep indigo continuing it, which is defensible while the only colour on screen is
our own fire palette — a blue ground makes warm colours sing. It stopped being
defensible once the map started carrying third-party raster overlays: EFFIS fire
danger, fuel classes and land cover each arrive with their own colour scale, and
a blue-cast canvas shifts how every one of them reads. A legend that does not
match perceived map colour is the one thing a legend must never be. So the ground
is near-neutral graphite and the brand lives in the logo and the teal accent.

Operational palettes are deliberately **not** brand colours. Severity and fire
danger use the conventional green-to-red hazard ramp with luminance steps that
survive greyscale and common colour-vision deficiencies, because they have to be
unambiguous at 3 a.m. on a bad monitor.

### One gotcha worth knowing

MapLibre GL 6 loads its web worker as a separate ES module resolved from
`import.meta.url`. Turbopack does not emit that chunk, so the request 404s and
the map renders its controls and attribution but **not a single tile**, with no
error. `frontend/scripts/copy-maplibre-worker.mjs` stages the worker into
`public/maplibre/` and `MapCanvas.tsx` points MapLibre at it with
`setWorkerUrl`. It runs automatically before `dev` and `build`.

## Testing

35 tests, weighted toward the claims that would be dangerous if wrong:

- **FWI validated against Van Wagner (1987)** to two decimal places.
- **Clustering** — two fires stay separate; one fire across satellite passes
  stays one; the same ground ten days apart is two incidents; a stray pixel is
  noise, not a named fire.
- **Area** — repeated detection of one cell does not inflate it.
- **Exposure** — a northerly pushes fire *south* (getting this backwards is a
  real class of operational error, so it is pinned); no weather means no
  invented ETA; the brief never lists upwind places as "on current trajectory".
- **Confidence** — spread estimates can never report better than "moderate".
