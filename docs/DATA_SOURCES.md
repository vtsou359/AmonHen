# Data sources

Every source Amon Hen uses or should use, what it actually gives you, and where
it lets you down. All are open — no commercial licences anywhere in the stack.

## Wired in

### NASA FIRMS — active fire detections
`backend/amonhen/sources/firms.py`

Thermal anomaly points from VIIRS and MODIS, reprocessed by NASA and published
in near-real-time.

| | |
|---|---|
| **Resolution** | VIIRS 375 m, MODIS 1 km at nadir (much worse off-nadir) |
| **Revisit** | ~4 useful passes/day over Greece from 3 VIIRS platforms + MODIS |
| **Latency** | ~3 h for Europe (NRT). ~60 s only for US/Canada (URT) |
| **Day range** | **1-5 only** for the area endpoint. Asking for more returns the plain text "Invalid day range" with an HTTP 200 — an error that looks exactly like an empty result |
| **Key** | Free, instant: https://firms.modaps.eosdis.nasa.gov/api/map_key/ |
| **Limit** | Quota is per key; the connector caches for 10 min and de-duplicates |

**What it will not do:** distinguish a wildfire from a steel works — see "Is it actually a fire?" in the README. Or see a fire in its first fifteen minutes. Ignition to
first detection is typically 1–4 hours in Greece, dominated by overpass timing.
Anything claiming faster detection is using ground sensors, cameras or 112 calls
— which is precisely why this should be one input among several, not the system.

**Quirks handled in the connector:** VIIRS and MODIS name the same physical
quantities differently (`bright_ti4` vs `brightness`); confidence is `l/n/h` for
VIIRS but 0–100 for MODIS; quota errors arrive as HTTP 200 with a prose body.

### Open-Meteo — fire weather
`backend/amonhen/sources/open_meteo.py`

ECMWF IFS and DWD ICON at ~9 km, hourly, past and forecast.

Chosen as the default for one reason above all: **no API key**. `docker compose
up` gives you real live weather with zero signup, which is what makes the
zero-setup promise real rather than a demo mode. Free for non-commercial use.

The archive endpoint proxies ERA5, which is what warms up the FWI moisture codes
over 45 days.

### Natural Earth — country boundary
`backend/scripts/build_boundary.py` → `data/boundaries/greece.geojson`

Public domain 10m admin-0 country polygons. Used to clip FIRMS detections to
Greece, because the bounding box FIRMS requires also covers four neighbouring
countries — 92% of detections in a live sample.

**Known gap:** the 10m dataset omits twelve genuine Greek islands, several
inhabited (Kastellorizo, Lipsi, Fournoi, Oinousses, Othonoi). They are added
back as discs in the build script. If you swap in a different boundary source,
re-run the script's validation before trusting it — it checks all 51 gazetteer
places fall inside and eight foreign cities fall outside, and refuses to write
a boundary that fails.

Rebuild with `make boundary`.

### CORINE Land Cover — vegetation, and the fuel it implies
`backend/amonhen/sources/landcover.py`

**CORINE Land Cover 2018**, the European reference dataset, from the EEA. This
replaced the platform's worst assumption: that the whole country was one uniform
shrubland.

Queried through the ArcGIS REST `identify` endpoint rather than WMS. That matters
— `GetFeatureInfo` returns nothing for the EFFIS raster layers, which is why the
fuel map could be *looked at* but not *used*. `identify` returns a real class
code for a real point, keyless, as JSON, with no raster processing and no new
dependency.

**A trap that silently produced plausible nonsense.** ArcGIS computes `identify`
tolerance in *screen pixels*, derived from `mapExtent` and `imageDisplay`. Pass a
degenerate extent — the same point as both corners — and that pixel size is
meaningless, so the service answers with whatever polygon is vaguely nearby.
Parnitha National Park came back as "Continuous urban fabric". With a real
extent it correctly returns coniferous forest. Validate any change to those
parameters against known ground truth before trusting it.

Resolution is 100 m with a 25 ha minimum mapping unit, vintage 2018. This is the
*landscape*, not this season: good enough to tell pine from pasture, not good
enough to know a field was harvested last week. The ensemble's two fuel cases
exist to bracket exactly that uncertainty.

Two Mediterranean-specific calls in the mapping worth knowing about:

* **Olive groves (223) are treated as shrub, not cropland.** They burn readily —
  the understory carries fire between the trees — and filing them under
  agriculture would give them the slowest fuel model in the set.
* **Sclerophyllous vegetation (323) and transitional woodland-shrub (324) both
  map to maquis.** The latter is post-fire regrowth, and some of the most
  dangerous ground in Greece.

It also does double duty for the fire/not-fire test: ground that cannot carry a
fire — quarries, docks, open water — is the only *independent* evidence
available, since every other signal derives from the same satellite pixels.

### Copernicus DEM — terrain
`backend/amonhen/sources/elevation.py`

Ground elevation, via Open-Meteo's elevation endpoint, which serves **Copernicus
DEM GLO-90** (~90 m). Chosen over the Copernicus Data Space or OpenTopography for
one reason that outweighs resolution: **no credentials**, so terrain-aware
projections work on a fresh clone. 90 m is finer than the 375 m satellite pixel
we locate fires with, so it is not the limiting factor.

This closed the largest accuracy gap in the platform. `estimate_spread()` had
always accepted a slope and nothing ever supplied one, so every projection was
computed on flat ground while fire runs uphill roughly exponentially with grade.

What is computed: a least-squares **plane fit** through a 5×5 grid at 400 m
spacing (one request, 25 points). That yields a gradient, from which slope
percentage and aspect (the compass bearing of steepest ascent) are derived. Both
go to the spread model whole: it converts the grade to an equivalent wind speed
and adds it to the real wind as a vector, so the hill helps decide *where* the
fire goes rather than only how fast. See "Slope is a wind, not a multiplier" in
ARCHITECTURE.md for why resolving the grade along a pre-chosen direction — which
is what `Terrain.slope_toward()` does, and what this used to feed — cannot work
once the direction is an output of the model.

A plane fit rather than a central difference so one noisy DEM cell cannot swing
the result. Cached for 30 days, and deliberately exempt from the Refresh
button's cache bypass — the ground does not move.

### Copernicus Sentinel-2 — burned area, severity and live fuel moisture
**Keyless.** Earth Search STAC (`https://earth-search.aws.element84.com/v1`)
over the public `sentinel-cogs` AWS bucket. Needs the optional `[eo]` extra.

Three products from one windowed read, at 20 m:

```
NBR   = (B8A - B12) / (B8A + B12)        dNBR = NBR_before - NBR_after   burn scar
NDMI  = (B8A - B11) / (B8A + B11)                                        live fuel moisture
NDVI  = (B8A - B04) / (B8A + B04)                                        greenness / curing
```

Severity is classified by the Key & Benson dNBR thresholds already in
`domain/enums.py` (`BurnSeverity`). Cloud-Optimized GeoTIFFs mean nothing is
downloaded: GDAL range-requests only the tiles covering the fire, a few megabytes
instead of the ~1 GB granule.

**Why AWS rather than the Copernicus Data Space Ecosystem.** Same ESA pixels,
same processing baseline, no credential — which is the rule every source in this
project follows. CDSE stays the right answer the day we need bulk download or a
product AWS does not mirror; `AMONHEN_CDSE_CLIENT_ID` is still in the config for
that.

**Only `rasterio` is needed.** The `[eo]` extra used to list `xarray`,
`rioxarray`, `netcdf4`, `odc-stac` and `pystac-client` as well. None was ever
imported: the STAC catalogue is plain JSON over HTTP, which the existing httpx
client handles, and windowed COG reads plus polygonisation are rasterio's own
job. What is left resolves to four packages and 165 MB. rasterio still needs
`libexpat1`, which `python:*-slim` does not ship — installed unconditionally in
`backend/Dockerfile` so `uv pip install rasterio` works in a running container.

> **⚠ The offset trap. Applying the offset the metadata declares is the bug.**
>
> Sentinel-2 baseline 04.00 (January 2022) added a −1000 shift to the digital
> numbers, so for ESA's own products reflectance is `DN * 0.0001 - 0.1`. Every
> asset here duly carries `raster:bands: [{scale: 0.0001, offset: -0.1}]`, and
> the STAC convention reads that as an instruction to apply it.
>
> Earth Search has **already applied it** when building the COGs, and says so in
> a different field: `earthsearch:boa_offset_applied: true`. The `raster:bands`
> offset describes the convention the product came from, not a correction still
> outstanding.
>
> Applying it twice drives dark pixels negative — physically impossible, and
> arithmetically silent. Over Parnitha, red reflectance came out at −0.034 for
> 67% of pixels and NDVI, which is mathematically confined to [−1, 1], returned
> **1.457**.
>
> Two images of the same ground either side of the baseline change settle it
> without reference to any documentation:
>
> | | red | NIR |
> |---|---|---|
> | 2021, baseline 03.01, offset genuinely zero | 0.0775 | 0.2683 |
> | 2026, baseline 05.12, scale only | 0.0662 | 0.2909 |
> | 2026, baseline 05.12, offset applied | **−0.0338** | 0.1909 |
>
> The first two agree; the third is impossible. So `boa_offset_applied` wins,
> and `_normalised_difference` floors reflectance at zero as a standing guard.

Two more things this gets wrong if you are not careful:

* **Cloud shadow reads as a burn scar.** A shadow darkens near-infrared exactly
  the way char does. Every pixel is screened through the Scene Classification
  Layer (SCL) before it counts; without that, an afternoon of cumulus maps as a
  severe burn.
* **The post-fire search window must be bounded.** Left open to "now", scene
  selection prefers the clearest image and so picks up last week's for a fire
  from two summers ago. The scar is still faintly visible, so the measurement
  *succeeds* and reports two years of regrowth as a light burn. On Varnavas that
  dragged mean dNBR from 0.43 to 0.30 and moved the dominant severity class.

**Validation.** Against the Varnavas/Penteli fire of 11–13 August 2024, reported
at roughly 10,000 ha, this measures **9,910 ha**, mean dNBR 0.507, dominant class
moderate-high.

### Esri World Hillshade — terrain, drawn
Keyless raster tiles. Now that slope drives the projections, being able to *see*
the hills explains why a fire is heading where it is.

## Computed, not fetched

### Canadian Fire Weather Index System
`backend/amonhen/services/fire_weather.py`

Implemented from Van Wagner (1987) Forestry Technical Report 35 — the same
system Copernicus EFFIS runs operationally across Europe. Six components:

```
FFMC  fine fuel moisture   will a spark catch?           hours to respond
DMC   duff moisture        will it reach the litter?     days
DC    drought code         how deep is the drought?      ~53-day time lag
  ISI = f(FFMC, wind)      how fast will it run?
  BUI = f(DMC, DC)         how much fuel is available?
    FWI = f(ISI, BUI)      overall potential intensity
```

**The critical property:** the three moisture codes are a *memory*. Today
depends on yesterday. A single hot afternoon does not make a dangerous forest;
six dry weeks do. You therefore cannot compute a meaningful FWI from one weather
reading — you must march it forward over a warm-up period. Amon Hen uses 45
days. Below about a week the answer mostly reflects the arbitrary start state,
which is why the API enforces a floor.

**Latitude adjustment:** the published day-length tables are derived for ~46°N.
Greece sits near 38°N. `_day_length` interpolates between the 46°N and 20°N
tables rather than pretending Greece is Canada.

### Fire Behaviour Prediction rate of spread
`backend/amonhen/services/spread.py`

FBP rate-of-spread equations driven by ISI, wrapped in the standard elliptical
growth model. **The weakest link in the platform, and knowingly so:** FBP fuel
types were derived for Canadian boreal forest. The Mediterranean mappings
(phrygana, maquis, Aleppo pine, mixed broadleaf, agricultural) are the closest
published analogues, not calibrated Greek models. Every estimate carries its
caveats through the API and into the UI.

### Copernicus EFFIS / GWIS — map overlays
`backend/amonhen/sources/effis.py` · `https://maps.effis.emergency.copernicus.eu/gwis`

The European operational fire service, part of the Copernicus Emergency
Management Service. Its WMS is public, keyless, sends `Access-Control-Allow-Origin: *`
and caches for an hour, so the browser consumes it directly — no proxy, no
storage, no backend load.

266 layers advertised; ten are wired in as toggleable overlays:

| Layer | What it answers |
|---|---|
| `ecmwf.fwi` | The European reference FWI — cross-check against ours |
| `ecmwf.anomaly` | Is this danger *unusual for the date*? FWI 45 in August vs in May |
| `ecmwf.isi` | Initial Spread Index, mapped |
| `ecmwf.mark5.ros` | McArthur Mk5 rate of spread — a second opinion on our FBP number |
| `ecmwf.dc` | Drought Code — the slow variable, for pre-positioning |
| `fuel_map` | GWIS global fuel classification |
| `landcover.mcd12.2024` | MODIS land cover |
| `ghsl` | Built-up surface — exposure at far finer resolution than our gazetteer |
| `wdpa.poly_0_03` | Protected areas |
| `viirs.hs` | EFFIS's own hotspots — cross-check on our ingest |

**Two traps, both cost real time to find:**

* **Time-aware layers must be given a date.** EFFIS defaults them to
  `2019-01-01`, which returns a valid, empty, 1 KB PNG. The failure looks
  exactly like a broken layer. Every time-aware entry carries `{time}` in its
  URL template and the client substitutes today.
* **Burnt-area polygons (`nrt.ba.*`) are deliberately not listed.** They render
  empty for every date tried, including over the 2023 Evros mega-fire, so they
  need parameters we have not worked out. A layer that silently shows nothing is
  worse than no layer.

**GetFeatureInfo does not work on the raster layers** ("Search returned no
results"), so the fuel map cannot currently be queried programmatically — only
looked at. Fixing that needs the underlying raster, not the WMS.

## Available, not yet wired in

### ~~Copernicus EFFIS / GWIS~~ — now wired in
See "Wired in" above.

### ~~Copernicus Sentinel-2~~ — now wired in
See "Wired in" above.

### Sentinel-3 SLSTR — active fire
300–500 m, includes a dedicated fire radiometer. Complements VIIRS with
different overpass times, which is exactly what you want to shorten the worst
detection gap.

### ERA5-Land — reanalysis
Copernicus CDS. 9 km hourly back to 1950. Needed for *climatological* baselines
— "is this FWI unusual for late August in Attica?" — which is a much more useful
question than the raw number. Requires a CDS account and is slow to query, hence
Open-Meteo as the operational default.

### Copernicus DEM — terrain
30 m global. Slope and aspect. `estimate_spread` already accepts `slope_pct` and
nothing supplies it, so this is a drop-in accuracy improvement.

### OpenStreetMap — the real gazetteer
`services/gazetteer.py` ships ~50 curated Greek places so exposure analysis works
on first run. Replace it with OSM `place=*`, `amenity=hospital|school`, plus EEA
Natura 2000 boundaries. Until then, treat exposure counts as illustrative.

### Greek national sources
- **Fire Service (Πυροσβεστικό Σώμα)** — authoritative incident reports and
  deployments. No open API; would need scraping or a formal data agreement.
- **meteo.gr (National Observatory of Athens)** — dense national AWS network,
  far better than 9 km model output for local wind. Worth pursuing.
- **Forest Service fuel maps** — the missing input for calibrating the fuel
  models, which is the highest-value improvement available to this platform.

## Choosing a source

| Question | Source |
|---|---|
| Where is it burning now? | FIRMS |
| How dangerous are conditions? | Open-Meteo → FWI |
| Where will it go in the next hour? | FBP spread (triage only) |
| How big was the burn, exactly? | Sentinel-2 dNBR, or EFFIS perimeters |
| Is this unusual for the season? | ERA5-Land climatology |
| Who is in the way? | OSM gazetteer + Natura 2000 |
