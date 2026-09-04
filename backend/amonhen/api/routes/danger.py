"""Fire danger endpoints — the preparedness side of the cycle.

Where `/incidents` answers "what is burning", this answers "where is it about to
burn", which is the question that actually lets you pre-position resources.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from amonhen.api.schemas import FwiSummary, GeoJsonFeature, GeoJsonFeatureCollection
from amonhen.core.config import settings
from amonhen.services.operations import compute_danger
from amonhen.services.spread import FUEL_MODELS, estimate_spread
from amonhen.sources.open_meteo import OpenMeteoSource

router = APIRouter(prefix="/danger", tags=["danger"])
_weather = OpenMeteoSource()


@router.get("/point", response_model=FwiSummary | None)
async def danger_at_point(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    warmup_days: int = Query(45, ge=7, le=92, description="Days of history to warm up the codes"),
) -> FwiSummary | None:
    """FWI at one location.

    `warmup_days` has a floor of 7 for a reason: the Drought Code has a ~53-day
    time lag, so a short warm-up returns a number that mostly reflects the
    arbitrary starting state rather than the weather. Seven days is the point
    below which the answer stops meaning anything at all.
    """
    observations = await _weather.fetch(
        latitude=latitude, longitude=longitude, past_days=warmup_days, forecast_days=1
    )
    result = compute_danger(observations, latitude=latitude)
    return FwiSummary(**vars(result)) if result else None


@router.get("/grid", response_model=GeoJsonFeatureCollection)
async def danger_grid(
    resolution_deg: float = Query(0.5, ge=0.25, le=2.0, description="Grid spacing in degrees"),
    warmup_days: int = Query(30, ge=7, le=92),
) -> GeoJsonFeatureCollection:
    """A coarse fire-danger grid over the area of interest.

    Bounded deliberately: at 0.25° over Greece this is already ~250 points and
    ~250 upstream weather requests. It is a preparedness overview, not a
    high-resolution danger raster — for that, ingest the EFFIS FWI product
    directly rather than recomputing it point by point.
    """
    import asyncio

    west, south, east, north = settings.area_of_interest_bbox
    points: list[tuple[float, float]] = []
    latitude = south
    while latitude <= north:
        longitude = west
        while longitude <= east:
            points.append((round(latitude, 3), round(longitude, 3)))
            longitude += resolution_deg
        latitude += resolution_deg

    async def one(lat: float, lon: float) -> GeoJsonFeature | None:
        observations = await _weather.fetch(
            latitude=lat, longitude=lon, past_days=warmup_days, forecast_days=1
        )
        result = compute_danger(observations, latitude=lat)
        if result is None:
            return None
        return GeoJsonFeature(
            geometry={"type": "Point", "coordinates": [lon, lat]},
            properties={
                "fwi": result.fwi,
                "isi": result.isi,
                "bui": result.bui,
                "ffmc": result.ffmc,
                "dc": result.dc,
                "danger_class": result.danger_class.value,
            },
        )

    # Bounded concurrency: Open-Meteo is free and we are not going to abuse it.
    semaphore = asyncio.Semaphore(8)

    async def guarded(lat: float, lon: float) -> GeoJsonFeature | None:
        async with semaphore:
            return await one(lat, lon)

    results = await asyncio.gather(*(guarded(lat, lon) for lat, lon in points))
    return GeoJsonFeatureCollection(
        features=[f for f in results if f is not None],
        attribution="Open-Meteo · FWI computed by Amon Hen",
    )


@router.get("/fuel-models")
async def fuel_models() -> dict[str, dict[str, object]]:
    """The fuel models available to the spread estimator, with their caveats."""
    return {
        key: {"label": m.label, "note": m.note, "coefficients": {"a": m.a, "b": m.b, "c": m.c}}
        for key, m in FUEL_MODELS.items()
    }


@router.get("/spread")
async def spread_preview(
    isi: float = Query(..., ge=0, le=200),
    wind_direction_deg: float = Query(..., ge=0, lt=360),
    wind_speed_kmh: float = Query(..., ge=0, le=200),
    fuel: str = Query("maquis"),
    slope_pct: float = Query(0.0, ge=0, le=100, description="Steepest grade, unsigned"),
    slope_aspect_deg: float | None = Query(
        None,
        ge=0,
        lt=360,
        description=(
            "Compass bearing of steepest ascent. Omit and the hill is assumed to rise "
            "in the direction the fire is already heading, i.e. the pure upslope run."
        ),
    ),
) -> dict[str, object]:
    """What-if spread calculator. Backs the scenario panel in the UI.

    An aspect is required for slope to do anything, because slope is now added
    to wind as a vector rather than multiplying the rate of spread. Callers that
    only supply a grade get the straight-uphill case, which is what a bare
    `slope_pct` meant before the change.
    """
    if slope_aspect_deg is None and slope_pct > 0:
        slope_aspect_deg = (wind_direction_deg + 180.0) % 360.0

    estimate = estimate_spread(
        isi=isi,
        wind_direction_deg=wind_direction_deg,
        wind_speed_kmh=wind_speed_kmh,
        fuel=fuel,
        slope_pct=slope_pct,
        slope_aspect_deg=slope_aspect_deg,
    )
    return vars(estimate)
