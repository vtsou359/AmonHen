# Working on Amon Hen

The other two documents cover *why* the platform is shaped the way it is
([ARCHITECTURE.md](ARCHITECTURE.md)) and *what* every feed is
([DATA_SOURCES.md](DATA_SOURCES.md)). This one is about finding your way around
and changing things.

## Where to start reading

**`backend/amonhen/services/operations.py`.** It is the spine: it runs the whole
chain from raw satellite detections to a finished operational picture, calling
everything else in order. Read `rebuild()` and then `_build_view()` top to
bottom and you will have met every service in the codebase and seen how they
fit together. Nothing else gives you the shape of the system as quickly.

After that, pick the direction you care about:

| If you want to understand… | Read |
|---|---|
| how pixels become named fires | `services/clustering.py` |
| whether a "fire" is really a fire | `services/validation.py` |
| the fire danger numbers | `services/fire_weather.py` |
| how fast and which way a fire moves | `services/spread.py` |
| the nine-case ensemble on the map | `services/projection.py` |
| how burned area is measured | `services/burn_scar.py` |
| adding a data feed | `sources/base.py` |
| what the frontend draws | `frontend/components/MapCanvas.tsx` |

Every package has a docstring in its `__init__.py` listing what lives there.
Every module has one explaining what it is for and, more usefully, what it is
*not* for.

## Running it

Step-by-step setup for macOS, Windows and Linux — installing Docker, changing
ports, adding keys, and what to do when something fails — is in
[RUNNING.md](RUNNING.md). The short version:

```bash
make up          # start everything: UI on :3000, API on :8000/docs
make status      # service health, and which feeds are live vs fixtures
make test        # the backend suite, in Docker
make logs        # tail both services
make down        # stop
```

Satellite imagery (burned area, live fuel moisture) needs the optional raster
stack, which is off by default because it pulls GDAL:

```bash
make build-eo    # rebuild the API image with the [eo] extra, ~165 MB
```

To iterate on the science without Docker:

```bash
cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests -q
```

> **Note.** `backend/amonhen/core/config.py` reads a `.env` at the repository
> root, and the Docker containers do not. If a test passes in Docker and fails
> locally, an override in your `.env` is the first thing to check.

## Adding a data source

1. Subclass `DataSource[T]` in `backend/amonhen/sources/`.
2. Set `name`, `attribution`, `homepage`, `cache_ttl_seconds`,
   `requires_credentials`.
3. Implement `_fetch_live` and `_load_fixture`. The docstrings on the abstract
   methods in `sources/base.py` are the contract — read them; they cover
   caching, the `force` flag and what a fixture should return.
4. Register it in `services/operations.py` and add it to the source list in
   `api/routes/system.py`, so the UI can report its health.
5. Document it in `docs/DATA_SOURCES.md`, including anything about the upstream
   API that surprised you.

`sources/landcover.py` is a short worked example. `sources/sentinel2.py` is a
longer one that also overrides `is_live` and `status()` for an optional
dependency.

## Conventions worth knowing before you change anything

**Never invent a measurement.** If a source cannot answer, it says so — an
empty list, an explicit "unavailable" record, a named reason. A fabricated
value does not stay local: it propagates silently into every number computed
from it and there is no way to tell afterwards which figures were real. This is
why `elevation.py` returns "flat, and we know it" rather than a plausible
slope, and why `burn_scar.py` returns a *reason* rather than nothing at all.

**Comments record what was measured, not what the API promises.** Several
comments in this codebase contradict the documentation of the library they
describe, because the documented behaviour was tested and found false. They
name the measurement and the number. Please read them before simplifying past
them — each one is there because it cost a bug. The densest examples:

- `frontend/components/MapCanvas.tsx` — `isStyleLoaded()` reads false on a map
  that is rendering perfectly; `idle` never fires at all.
- `sources/sentinel2.py` — the STAC metadata says to apply a reflectance offset
  that has already been applied; doing as told breaks every index.
- `sources/landcover.py` — an ArcGIS `identify` with a degenerate extent returns
  confident nonsense.
- `services/spread.py` — slope is a wind, not a multiplier.

**Plain language happens at one boundary.** The domain vocabulary stays precise
(`informational`, `ffmc`, `envelope`); translation into words a duty officer can
read at 3 a.m. happens once, in `frontend/lib/labels.ts`. Do not spell things
differently in the backend to make them read better — the enums are exported to
the frontend at `/api/v1/system/ontology` precisely so the two sides cannot
drift apart.

**State the limits where they will be read.** Projections carry `caveats`,
perimeters carry a `method` and a `confidence`, areas carry an `area_source`.
These are not decoration: the UI styles a measurement differently from an
estimate, and removing one makes the map assert a precision it does not have.

## Testing

The suite is fast (a couple of seconds) and runs with no network. Tests are
named as sentences describing the behaviour, and the docstring usually explains
the failure that motivated them — including the real numbers. That is
deliberate: a test called `test_slope_never_speeds_a_fire_up_in_the_direction_it_is_not_climbing`
tells you more when it fails than one called `test_slope_factor`.

Anything touching imagery must pass **with and without** the `[eo]` extra
installed. Installing an optional dependency must never silently change a number
on a fire it cannot see.
