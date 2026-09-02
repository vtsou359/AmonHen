"""The operational picture.

One module that answers "what is happening right now", by running the whole
chain: detections -> clusters -> incidents -> weather -> danger -> exposure.

Two decisions worth explaining:

**Weather is fetched per incident, not once per country.** An early version
used a single national weather reading, and every fire on the map obediently
reported the same spread rate in the same direction — which is not merely
imprecise, it is actively misleading, because the wind on Rhodes has nothing to
do with the wind over Parnitha. Fires are far enough apart that each needs its
own atmosphere.

**The live picture lives in memory, history lives in PostGIS.** The current
picture is a few hundred objects and is rebuilt from scratch every cycle, so a
database round-trip buys nothing. History is large and worth indexing properly.
This also means the platform runs, and is useful, before Postgres is even up.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from amonhen.core.config import settings
from amonhen.core.logging import get_logger
from amonhen.domain.entities import (
    Detection,
    ExposedElement,
    Incident,
    Perimeter,
    WeatherObservation,
)
from amonhen.services import boundary
from amonhen.services.validation import Plausibility, assess
from amonhen.services.clustering import build_incident, build_perimeter, cluster_detections
from amonhen.services.exposure import assess_exposure, summarise
from amonhen.services.fire_weather import FwiResult, run_series
from amonhen.services.projection import EnsembleProjection, project
from amonhen.services.spread import SpreadEstimate
from amonhen.sources.elevation import ElevationSource, Terrain
from amonhen.sources.firms import FirmsSource
from amonhen.sources.landcover import LandCover, LandCoverSource
from amonhen.sources.open_meteo import OpenMeteoSource

log = get_logger(__name__)

#: Days of weather history used to warm up the FWI moisture codes. The Drought
#: Code has a ~53-day time lag, so anything less than a few weeks is still
#: reporting its arbitrary start value rather than the actual drought.
FWI_WARMUP_DAYS = 45


@dataclass
class IncidentView:
    """An incident plus everything needed to reason about it. One API payload."""

    incident: Incident
    detections: list[Detection]
    perimeter: Perimeter | None
    weather: WeatherObservation | None
    danger: FwiResult | None
    exposed: list[ExposedElement]
    spread: SpreadEstimate | None
    #: Whether this behaves like a wildfire at all, or like industry.
    plausibility: Plausibility
    #: What is growing here, and therefore what burns.
    land_cover: LandCover | None
    #: Local ground shape from the Copernicus DEM. None when unavailable, which
    #: the projection reports rather than silently assuming flat.
    terrain: Terrain | None
    #: The ensemble — where the fire could go under nine sets of assumptions.
    projection: EnsembleProjection | None
    brief: str


@dataclass
class OperationalPicture:
    """Everything the situation display needs, as of `generated_at`."""

    generated_at: datetime
    incidents: list[IncidentView] = field(default_factory=list)
    unclustered: list[Detection] = field(default_factory=list)
    source_status: list[dict] = field(default_factory=list)
    area_of_interest: str = settings.area_of_interest_name
    #: Detections inside the FIRMS bbox but outside the country polygon.
    #: Reported so it is visible that filtering happened, and by how much.
    dropped_outside_boundary: int = 0

    @property
    def active_count(self) -> int:
        return sum(1 for v in self.incidents if v.incident.status.value == "active")

    @property
    def total_area_ha(self) -> float:
        return round(sum(v.incident.estimated_area_ha for v in self.incidents), 1)


class OperationsService:
    """Builds and caches the operational picture."""

    def __init__(self) -> None:
        self.firms = FirmsSource()
        self.weather = OpenMeteoSource()
        self.elevation = ElevationSource()
        self.land_cover = LandCoverSource()
        self._picture: OperationalPicture | None = None
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- public

    async def get_picture(self, max_age_seconds: int = 600) -> OperationalPicture:
        """Return the cached picture, rebuilding it if stale.

        The lock matters: without it, ten simultaneous page loads on a cold
        cache each trigger a full rebuild and hammer NASA's servers ten times
        for the same bytes.
        """
        async with self._lock:
            if self._picture is not None:
                age = (datetime.now(UTC) - self._picture.generated_at).total_seconds()
                if age < max_age_seconds:
                    return self._picture
            self._picture = await self.rebuild()
            return self._picture

    async def rebuild(self, day_range: int = 5, force: bool = False) -> OperationalPicture:
        """Run the full chain from scratch.

        `force` additionally bypasses the source disk caches, so a manual
        refresh really does go back to NASA rather than replaying what we
        already had.
        """
        started = datetime.now(UTC)

        detections = await self.firms.fetch(
            bbox=settings.area_of_interest_bbox, day_range=day_range, force=force
        )
        # FIRMS only accepts a bounding box, and every rectangle around Greece
        # also contains western Turkey, Albania, North Macedonia and southern
        # Bulgaria. Clip to the actual country polygon before clustering, so
        # foreign fires do not crowd out Greek ones in the incident list.
        detections, dropped_outside = boundary.filter_detections(detections)
        clusters = cluster_detections(detections)
        fire_clusters = {label: group for label, group in clusters.items() if label >= 0}

        views = await asyncio.gather(
            *(self._build_view(group) for group in fire_clusters.values())
        )
        views = [v for v in views if v is not None]
        views.sort(key=lambda v: (-_severity_rank(v.incident), -v.incident.estimated_area_ha))

        picture = OperationalPicture(
            generated_at=started,
            incidents=views,
            unclustered=clusters.get(-1, []),
            source_status=[
                self.firms.status(),
                self.weather.status(),
                self.elevation.status(),
                self.land_cover.status(),
            ],
            dropped_outside_boundary=dropped_outside,
        )

        log.info(
            "operations.rebuilt",
            incidents=len(views),
            detections=len(detections),
            seconds=round((datetime.now(UTC) - started).total_seconds(), 2),
        )
        return picture

    # --------------------------------------------------------------- internal

    async def _build_view(self, detections: list[Detection]) -> IncidentView | None:
        try:
            incident = build_incident(detections)
        except ValueError:
            return None

        # Weather and terrain are independent lookups; fetch them together.
        # Terrain is cached for a month — the ground does not move — so after the
        # first pass this costs nothing.
        # Three independent lookups; fetch them together. Terrain and land cover
        # are both cached for a month — neither the ground nor the vegetation
        # survey changes between refreshes — so after the first pass this is free.
        (weather, danger), terrain, cover = await asyncio.gather(
            self._local_fire_weather(incident.latitude, incident.longitude),
            self.elevation.terrain_at(incident.latitude, incident.longitude),
            self.land_cover.cover_at(incident.latitude, incident.longitude),
        )
        if weather is not None and danger is not None:
            weather.ffmc, weather.dmc, weather.dc = danger.ffmc, danger.dmc, danger.dc
            weather.isi, weather.bui, weather.fwi = danger.isi, danger.bui, danger.fwi
            weather.danger_class = danger.danger_class
            incident.fire_weather = weather

        exposed, spread = assess_exposure(incident, weather)
        perimeter = build_perimeter(incident.id, detections)
        if perimeter is not None:
            incident.current_perimeter_id = perimeter.id

        return IncidentView(
            incident=incident,
            detections=detections,
            perimeter=perimeter,
            weather=weather,
            danger=danger,
            exposed=exposed,
            spread=spread,
            plausibility=assess(detections, land_cover=cover),
            terrain=terrain,
            land_cover=cover,
            projection=project(incident, weather, exposed, terrain=terrain, land_cover=cover),
            brief=summarise(incident, exposed, spread, terrain=terrain),
        )

    async def _local_fire_weather(
        self, latitude: float, longitude: float
    ) -> tuple[WeatherObservation | None, FwiResult | None]:
        """Current conditions at the fire, plus FWI warmed up over recent weeks."""
        observations = await self.weather.fetch(
            latitude=latitude,
            longitude=longitude,
            past_days=min(FWI_WARMUP_DAYS, 92),
            forecast_days=2,
        )
        if not observations:
            return None, None

        current = _closest_to_now(observations)
        danger = compute_danger(observations, latitude=latitude)
        return current, danger


# --------------------------------------------------------------------------
# Weather -> fire danger
# --------------------------------------------------------------------------


def compute_danger(
    observations: list[WeatherObservation], latitude: float = 38.0
) -> FwiResult | None:
    """Aggregate hourly weather into daily FWI inputs and march the system forward.

    The FWI system is specified on *noon local standard time* readings — the
    daily peak of fire danger — with rainfall totalled over the preceding 24
    hours. Feeding it hourly values, or daily means, produces numbers that look
    plausible and are wrong, so the aggregation is done explicitly here.
    """
    if not observations:
        return None

    by_day: dict[str, list[WeatherObservation]] = {}
    for observation in observations:
        by_day.setdefault(observation.observed_at.strftime("%Y-%m-%d"), []).append(observation)

    daily: list[dict[str, float]] = []
    for day in sorted(by_day):
        readings = by_day[day]
        # Local noon in Greece is 10:00 UTC in summer (EEST, UTC+3).
        noon = min(readings, key=lambda o: abs(o.observed_at.hour - 10))
        if noon.temperature_c is None or noon.relative_humidity_pct is None:
            continue

        daily.append(
            {
                "temperature_c": noon.temperature_c,
                "humidity_pct": noon.relative_humidity_pct,
                "wind_kmh": noon.wind_speed_kmh or 0.0,
                "rain_mm": sum(r.precipitation_mm or 0.0 for r in readings),
                "month": float(noon.observed_at.month),
            }
        )

    if not daily:
        return None

    series = run_series(daily, latitude=latitude)
    return series[-1]


def _closest_to_now(observations: list[WeatherObservation]) -> WeatherObservation:
    now = datetime.now(UTC)
    return min(observations, key=lambda o: abs((o.observed_at - now).total_seconds()))


def _severity_rank(incident: Incident) -> int:
    order = ["informational", "minor", "moderate", "major", "critical"]
    value = incident.severity.value if hasattr(incident.severity, "value") else incident.severity
    return order.index(value) if value in order else 0


#: Process-wide singleton. FastAPI dependency-injects this.
operations = OperationsService()
