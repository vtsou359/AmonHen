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
from amonhen.services.burn_scar import (
    BurnScar,
    BurnScarService,
    BurnScarUnavailable,
    to_perimeter,
)
from amonhen.services.clustering import (
    build_incident,
    build_perimeter,
    cluster_detections,
    score_severity,
)
from amonhen.services.exposure import assess_exposure, summarise
from amonhen.services.fire_weather import FwiResult, run_series
from amonhen.services.fuel_moisture import FuelMoisture, FuelMoistureService
from amonhen.services.projection import EnsembleProjection, project
from amonhen.services.spread import DEFAULT_FUEL, SpreadEstimate
from amonhen.sources.elevation import ElevationSource, Terrain
from amonhen.sources.firms import FirmsSource
from amonhen.sources.landcover import LandCover, LandCoverSource
from amonhen.sources.open_meteo import OpenMeteoSource
from amonhen.sources.sentinel2 import Sentinel2Source

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
    #: Burned area measured from Sentinel-2, or the reason there is none. Never
    #: None once the raster extra is installed — "we could not see it, here is
    #: why" is information the operator needs, and silence is not.
    burn_scar: BurnScar | BurnScarUnavailable | None
    #: How wet the living vegetation around the fire is.
    fuel_moisture: FuelMoisture | None
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
        # One imagery connector shared by both derived products, so a scene
        # search made for the burn scar is already cached for fuel moisture.
        self.imagery = Sentinel2Source()
        self.burn_scar = BurnScarService(self.imagery)
        self.fuel_moisture = FuelMoistureService(self.imagery)
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
            *(self._build_view(group, force=force) for group in fire_clusters.values())
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
                self.imagery.status(),
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

    async def _build_view(
        self, detections: list[Detection], force: bool = False
    ) -> IncidentView | None:
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

        plausibility = assess(detections, land_cover=cover)

        # Imagery, once we know what is on the ground. Both reads are gated,
        # cached for hours and bounded by a shared semaphore — see the note on
        # `_should_image` for why not every incident is worth a satellite read.
        burn_scar, fuel_moisture = await asyncio.gather(
            self._measure_burn_scar(incident, detections, plausibility, force=force),
            self._measure_fuel_moisture(incident, detections, cover, plausibility, force=force),
        )

        exposed, spread = assess_exposure(incident, weather)
        perimeter = build_perimeter(incident.id, detections)

        # A measured scar supersedes everything derived from thermal pixels.
        # This is the whole point of the module: the hull was a sketch and the
        # pixel count an explicit lower bound, and both now have a real
        # replacement whenever the sky was clear enough to take one.
        if isinstance(burn_scar, BurnScar):
            measured = to_perimeter(burn_scar)
            if measured is not None:
                perimeter = measured
            _apply_measured_area(incident, burn_scar)

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
            plausibility=plausibility,
            terrain=terrain,
            land_cover=cover,
            burn_scar=burn_scar,
            fuel_moisture=fuel_moisture,
            projection=project(
                incident,
                weather,
                exposed,
                terrain=terrain,
                land_cover=cover,
                fuel_moisture=fuel_moisture,
            ),
            brief=summarise(
                incident, exposed, spread, terrain=terrain, burn_scar=burn_scar
            ),
        )

    # ------------------------------------------------------------- imagery

    def _skip_imaging(
        self, incident: Incident, detections: list[Detection], plausibility: Plausibility
    ) -> BurnScarUnavailable | None:
        """Why this incident is not worth spending a satellite read on, if it is not.

        Each exclusion gets its own words. Reporting "too soon" for a quarry
        would be a lie of convenience: an operator reading it would wait for a
        pass that is never going to change the answer, when what the platform
        actually decided was that there is nothing here to burn.
        """
        if len(detections) < 3:
            return BurnScarUnavailable(
                reason="Too few heat detections to locate a scar",
                detail="A burn scar is matched to the heat that found it, and three "
                "points are the fewest that give an area to match against.",
            )
        if plausibility.verdict == "likely_not_wildfire":
            return BurnScarUnavailable(
                reason="Not imaged — this does not behave like a fire",
                detail="Heat here looks industrial, and the ground it sits on cannot "
                "carry a fire. A satellite image would confirm an absence we already "
                "expect, so it is not requested.",
                transient=False,
            )
        age_hours = (
            datetime.now(UTC) - _as_utc(incident.first_detected_at)
        ).total_seconds() / 3600.0
        if age_hours < 12.0:
            return BurnScarUnavailable(
                reason="Too soon for a satellite image of the burn",
                detail="Sentinel-2 passes every 2-3 days. Burnt area is estimated from "
                "heat detections until one does.",
            )
        return None

    async def _measure_burn_scar(
        self,
        incident: Incident,
        detections: list[Detection],
        plausibility: Plausibility,
        force: bool = False,
    ) -> BurnScar | BurnScarUnavailable | None:
        if not self.burn_scar.available:
            return None
        skip = self._skip_imaging(incident, detections, plausibility)
        return skip or await self.burn_scar.assess(incident, detections, force=force)

    async def _measure_fuel_moisture(
        self,
        incident: Incident,
        detections: list[Detection],
        cover: LandCover | None,
        plausibility: Plausibility,
        force: bool = False,
    ) -> FuelMoisture | None:
        if not self.fuel_moisture.available or plausibility.verdict == "likely_not_wildfire":
            return None
        fuel = (cover.fuel if cover and cover.fuel else None) or DEFAULT_FUEL
        return await self.fuel_moisture.observe(incident, detections, fuel, force=force)

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


def _apply_measured_area(incident: Incident, scar: BurnScar) -> None:
    """Replace the thermal-pixel area estimate with the measured one.

    Everything derived from area has to move with it. Leaving growth rate and
    severity on the old figure would put two different areas on the same screen
    and score the fire against the one that is no longer shown.

    The growth rate is divided by the time to the *image*, not to the last
    detection: the measured area is what had burnt when the picture was taken,
    and dividing it by a longer window would report a fire slowing down purely
    because a satellite passed early.
    """
    incident.estimated_area_ha = scar.burned_area_ha
    incident.area_source = "sentinel2_dnbr"

    elapsed_hours = (
        scar.post_image_date - _as_utc(incident.first_detected_at)
    ).total_seconds() / 3600.0
    if elapsed_hours >= 1.0:
        incident.growth_rate_ha_per_hour = round(scar.burned_area_ha / elapsed_hours, 2)

    incident.severity = score_severity(incident)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _closest_to_now(observations: list[WeatherObservation]) -> WeatherObservation:
    now = datetime.now(UTC)
    return min(observations, key=lambda o: abs((o.observed_at - now).total_seconds()))


def _severity_rank(incident: Incident) -> int:
    order = ["informational", "minor", "moderate", "major", "critical"]
    value = incident.severity.value if hasattr(incident.severity, "value") else incident.severity
    return order.index(value) if value in order else 0


#: Process-wide singleton. FastAPI dependency-injects this.
operations = OperationsService()
