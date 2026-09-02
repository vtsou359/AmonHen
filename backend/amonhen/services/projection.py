"""Ensemble fire projection.

A single spread arrow is a lie of precision. It says "this fire goes south-west
at 45 m/min" when the honest statement is "under the assumptions we are forced
to make, it probably goes somewhere in this arc, and here is how wrong we could
be". The three inputs we are least sure of are exactly the three that decide
where a fire goes:

    fuel       we assume one uniform type for a whole country
    wind       a 9 km forecast grid, and it veers
    slope      nothing supplies terrain yet, so every estimate is flat-ground

So instead of one projection, this runs a small ensemble across those
uncertainties and reports the spread of outcomes. That turns "Kifisia in 2.6 h"
into "Kifisia is reached in 6 of 9 projections, earliest 1.9 h" — which is a
statement a duty officer can actually act on, and defend afterwards.

Why a curated scenario set rather than Monte Carlo: nine named, explainable
scenarios can be reasoned about ("the upslope case is the one that reaches the
village") and justified to a mayor. Ten thousand samples produce a prettier
probability surface that nobody can interrogate, from input distributions we
have no evidence for. The honesty is in the naming, not the sample count.

Geometry is the standard elliptical model: a fire from a point source spreads as
an ellipse with the ignition point at the rear focus, elongating with wind.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from amonhen.core.logging import get_logger
from amonhen.domain.entities import ExposedElement, Incident, WeatherObservation
from amonhen.services.gazetteer import bearing_deg, haversine_km
from amonhen.services.fire_weather import initial_spread_index
from amonhen.sources.elevation import Terrain
from amonhen.sources.landcover import LandCover
from amonhen.services.spread import DEFAULT_FUEL, SpreadEstimate, estimate_spread

log = get_logger(__name__)

#: How far ahead we project. Beyond ~6 hours the wind forecast that drives the
#: whole calculation has usually changed, so a longer horizon is decoration.
DEFAULT_HORIZONS_MINUTES: tuple[int, ...] = (60, 180, 360)

KM_PER_DEGREE_LAT = 111.32


@dataclass(frozen=True)
class Scenario:
    """One set of assumptions, named so it can be argued with."""

    id: str
    label: str
    #: What this scenario is testing, in plain words — shown in the UI.
    rationale: str
    #: None means "use the fuel measured at the fire". An explicit value
    #: overrides it, which is how the fuel-uncertainty cases work.
    fuel: str | None
    wind_speed_factor: float = 1.0
    wind_direction_offset_deg: float = 0.0
    #: When True the fire is assumed to follow the hill rather than the wind.
    #: Real behaviour in light wind on steep ground, and it can send a fire in a
    #: direction the forecast alone would never suggest.
    terrain_driven: bool = False
    #: Multiplies the *measured* grade. Terrain is now observed, not guessed, so
    #: this only stresses how much we trust a 90 m DEM over broken ground.
    slope_factor: float = 1.0


#: Spans the plausible space in the variables that actually matter. Deliberately
#: small: every scenario earns its place by testing something an analyst would
#: otherwise have to ask about.
SCENARIOS: tuple[Scenario, ...] = (
    Scenario("expected", "Most likely", "Current wind, measured slope and the vegetation actually here.", None),
    Scenario(
        "veer_left", "Wind shifts left", "Wind turns 30° to the left.",
        None, wind_direction_offset_deg=-30.0,
    ),
    Scenario(
        "veer_right", "Wind shifts right", "Wind turns 30° to the right.",
        None, wind_direction_offset_deg=30.0,
    ),
    Scenario(
        "gusting", "Stronger wind", "Wind 40% stronger than forecast.",
        None, wind_speed_factor=1.4,
    ),
    Scenario(
        "easing", "Weaker wind", "Wind 30% weaker than forecast — the hopeful case.",
        None, wind_speed_factor=0.7,
    ),
    # The two fuel cases stay explicit. Land cover is 100 m data from 2018 with a
    # 25 ha minimum patch, so the vegetation at any given fire may well not be
    # what the map says. These bracket that.
    Scenario(
        "light_fuel", "Drier, lighter scrub", "If the ground is really low scrub, it runs faster.",
        "phrygana",
    ),
    Scenario(
        "heavy_fuel", "Denser forest", "If it is really pine forest: slower front, far more heat.",
        "pine",
    ),
    Scenario(
        "terrain_driven", "Runs up the hill",
        "In light wind a fire follows the slope instead. This one climbs.",
        None, terrain_driven=True,
    ),
    Scenario(
        "worst_case", "Worst case",
        "Stronger wind, lighter fuel and a steeper reading of the ground, together.",
        "phrygana", wind_speed_factor=1.4, slope_factor=1.5,
    ),
)


@dataclass
class ScenarioProjection:
    """One scenario's footprint at each horizon."""

    scenario: Scenario
    spread: SpreadEstimate
    #: The fuel this case actually ran with — the measured one unless the
    #: scenario overrode it. Reported so the dossier can say which is which.
    fuel_used: str = DEFAULT_FUEL
    #: horizon minutes -> GeoJSON polygon
    footprints: dict[int, dict[str, Any]] = field(default_factory=dict)
    areas_ha: dict[int, float] = field(default_factory=dict)


@dataclass
class ThreatOutcome:
    """How often, and how soon, one place is reached across the ensemble."""

    name: str
    kind: str
    latitude: float
    longitude: float
    distance_km: float
    population: int | None
    #: Scenarios that reach it inside the longest horizon, out of the total.
    hit_count: int
    scenario_count: int
    earliest_minutes: float | None
    median_minutes: float | None
    scenarios_hit: list[str]

    @property
    def likelihood(self) -> float:
        """Fraction of the ensemble that reaches this place.

        Explicitly *not* called a probability. The scenarios are not weighted by
        evidence and are not independent samples of anything; this is "how many
        of our nine assumptions lead here", which is a different and more
        honest claim.
        """
        return round(self.hit_count / self.scenario_count, 3) if self.scenario_count else 0.0


@dataclass
class EnsembleProjection:
    incident_id: str
    generated_at: datetime
    horizons_minutes: tuple[int, ...]
    projections: list[ScenarioProjection]
    #: horizon -> union of every scenario's footprint. The "could reach" area.
    envelope: dict[int, dict[str, Any]] = field(default_factory=dict)
    envelope_areas_ha: dict[int, float] = field(default_factory=dict)
    #: horizon -> intersection of every footprint. The "will almost certainly
    #: burn" core, and usually much smaller than people expect.
    core: dict[int, dict[str, Any]] = field(default_factory=dict)
    threats: list[ThreatOutcome] = field(default_factory=list)
    terrain: Terrain | None = None
    land_cover: LandCover | None = None
    caveats: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


def _ellipse_footprint(
    latitude: float,
    longitude: float,
    spread: SpreadEstimate,
    minutes: float,
):
    """The elliptical area burned after `minutes`, as a shapely polygon.

    The ignition point sits at the rear of the ellipse, not its centre: a
    wind-driven fire runs forward far more than it backs, so the geometric
    centre drifts downwind as it grows.
    """
    from shapely import affinity
    from shapely.geometry import Point

    forward_km = spread.head_ros_m_per_min * minutes / 1000.0
    backward_km = spread.back_ros_m_per_min * minutes / 1000.0
    length_km = forward_km + backward_km
    if length_km <= 0:
        return None

    breadth_km = length_km / max(spread.length_to_breadth, 1.0)

    # Build in local kilometres, centred on the origin, major axis along +Y.
    unit = Point(0.0, 0.0).buffer(1.0, quad_segs=24)
    ellipse = affinity.scale(unit, xfact=breadth_km / 2.0, yfact=length_km / 2.0)
    # Shift so the ignition point (origin) sits at the rear focus.
    ellipse = affinity.translate(ellipse, yoff=(length_km / 2.0) - backward_km)
    # Rotate from +Y (north) to the spread bearing. Bearings run clockwise,
    # shapely rotates anticlockwise, hence the negation.
    ellipse = affinity.rotate(ellipse, -spread.direction_deg, origin=(0, 0))

    # Local km -> degrees. Longitude shrinks with latitude.
    km_per_degree_lon = KM_PER_DEGREE_LAT * math.cos(math.radians(latitude))
    ellipse = affinity.scale(
        ellipse, xfact=1.0 / km_per_degree_lon, yfact=1.0 / KM_PER_DEGREE_LAT, origin=(0, 0)
    )
    return affinity.translate(ellipse, xoff=longitude, yoff=latitude)


def _area_ha(geometry) -> float:
    """Approximate area of a small lat/lon polygon, in hectares."""
    if geometry is None or geometry.is_empty:
        return 0.0
    latitude = geometry.centroid.y
    km2 = (
        geometry.area
        * KM_PER_DEGREE_LAT
        * KM_PER_DEGREE_LAT
        * math.cos(math.radians(latitude))
    )
    return round(max(km2, 0.0) * 100.0, 1)


# --------------------------------------------------------------------------
# The ensemble
# --------------------------------------------------------------------------


def project(
    incident: Incident,
    weather: WeatherObservation | None,
    exposed: list[ExposedElement] | None = None,
    horizons_minutes: tuple[int, ...] = DEFAULT_HORIZONS_MINUTES,
    terrain: Terrain | None = None,
    land_cover: LandCover | None = None,
) -> EnsembleProjection | None:
    """Run every scenario and summarise where they agree and disagree.

    Returns None without wind: a projection with no wind direction is not a
    cautious projection, it is a made-up one.
    """
    if weather is None or weather.isi is None or weather.wind_direction_deg is None:
        return None

    from shapely.geometry import mapping
    from shapely.ops import unary_union

    # The fuel actually on the ground, when we know it. Scenarios that name a
    # fuel explicitly override this; the rest inherit it.
    measured_fuel = (land_cover.fuel if land_cover and land_cover.fuel else None) or DEFAULT_FUEL

    base_wind_speed = weather.wind_speed_kmh or 0.0
    base_direction = weather.wind_direction_deg
    projections: list[ScenarioProjection] = []

    for scenario in SCENARIOS:
        scenario_wind = base_wind_speed * scenario.wind_speed_factor

        # Direction first: a terrain-driven fire climbs the hill rather than
        # running with the wind, which can point it somewhere the forecast never
        # would. Everything downstream depends on getting this order right.
        if scenario.terrain_driven and terrain is not None and terrain.slope_pct >= 5.0:
            spread_bearing = terrain.aspect_deg
            wind_direction_for_model = (terrain.aspect_deg + 180.0) % 360.0
        else:
            wind_direction_for_model = (
                base_direction + scenario.wind_direction_offset_deg
            ) % 360.0
            spread_bearing = (wind_direction_for_model + 180.0) % 360.0

        # Slope is now *measured*, and only the component along the direction of
        # travel counts: a 40% hill does nothing for a fire running across it.
        if terrain is not None:
            scenario_slope = terrain.slope_toward(spread_bearing) * scenario.slope_factor
        else:
            scenario_slope = 0.0

        # ISI must be recomputed, not carried over. In the FBP system wind is an
        # *input to* ISI, and head rate of spread is a function of ISI alone —
        # so perturbing wind while reusing the original ISI changes only the
        # ellipse's elongation. The first version did exactly that, which made
        # the "easing" scenario cover more ground than "gusting": lower wind gave
        # a rounder ellipse at an unchanged forward speed. Feeding wind through
        # ISI is what makes a windier scenario actually run faster.
        if weather.ffmc is not None and scenario.wind_speed_factor != 1.0:
            scenario_isi = initial_spread_index(weather.ffmc, scenario_wind)
        else:
            scenario_isi = weather.isi

        spread = estimate_spread(
            isi=scenario_isi,
            wind_direction_deg=wind_direction_for_model,
            wind_speed_kmh=scenario_wind,
            fuel=scenario.fuel or measured_fuel,
            slope_pct=scenario_slope,
        )
        projection = ScenarioProjection(
            scenario=scenario, spread=spread, fuel_used=scenario.fuel or measured_fuel
        )
        for minutes in horizons_minutes:
            footprint = _ellipse_footprint(
                incident.latitude, incident.longitude, spread, float(minutes)
            )
            if footprint is None or footprint.is_empty:
                continue
            projection.footprints[minutes] = mapping(footprint)
            projection.areas_ha[minutes] = _area_ha(footprint)
        projections.append(projection)

    # Envelope and core, per horizon.
    envelope: dict[int, dict[str, Any]] = {}
    envelope_areas: dict[int, float] = {}
    core: dict[int, dict[str, Any]] = {}

    from shapely.geometry import shape

    for minutes in horizons_minutes:
        shapes = [
            shape(p.footprints[minutes]) for p in projections if minutes in p.footprints
        ]
        if not shapes:
            continue
        union = unary_union(shapes)
        envelope[minutes] = mapping(union)
        envelope_areas[minutes] = _area_ha(union)

        intersection = shapes[0]
        for geometry in shapes[1:]:
            intersection = intersection.intersection(geometry)
        if not intersection.is_empty:
            core[minutes] = mapping(intersection)

    threats = _score_threats(incident, projections, exposed or [], max(horizons_minutes))

    result = EnsembleProjection(
        incident_id=incident.id,
        generated_at=datetime.now(UTC),
        horizons_minutes=horizons_minutes,
        projections=projections,
        envelope=envelope,
        envelope_areas_ha=envelope_areas,
        core=core,
        threats=threats,
        terrain=terrain,
        land_cover=land_cover,
        caveats=_caveats(terrain, land_cover),
    )
    log.info(
        "projection.complete",
        incident=incident.id,
        scenarios=len(projections),
        threats=len(threats),
    )
    return result


def _caveats(terrain: Terrain | None, land_cover: LandCover | None = None) -> list[str]:
    """Plain-language limits, stated where the reader will see them."""
    lines = [
        "Shows where fire could spread, not where it will. Roads, rivers, "
        "firebreaks and firefighting are not included.",
        "Fires can throw embers ahead of the front. That can outrun every "
        "scenario shown here.",
        "The shaded area is 'could reach'. The smaller core is the part every "
        "scenario agrees on.",
    ]
    if land_cover is not None and land_cover.fuel:
        lines.insert(
            0,
            f"Vegetation here is mapped as {land_cover.descriptor}, from the European "
            f"land cover survey (100 m, 2018) — so it may not match this season exactly.",
        )
    else:
        lines.insert(
            0,
            "No vegetation data for this spot, so a Mediterranean shrubland is assumed. "
            "Pine forest would burn hotter and farmland far less.",
        )

    if terrain is None or terrain.sample_count == 0:
        lines.insert(
            0,
            "No ground-height data for this fire, so it is treated as flat. "
            "On a hillside a real fire would move faster uphill than shown.",
        )
    else:
        lines.insert(
            0,
            f"Ground here is {terrain.descriptor} "
            f"({terrain.slope_pct:.0f}% slope, {terrain.relief_m:.0f} m of height change "
            f"nearby), measured from Copernicus satellite elevation data.",
        )
    return lines


def _score_threats(
    incident: Incident,
    projections: list[ScenarioProjection],
    exposed: list[ExposedElement],
    max_minutes: int,
) -> list[ThreatOutcome]:
    """For each exposed place, count how many scenarios reach it and how soon."""
    outcomes: list[ThreatOutcome] = []

    for element in exposed:
        distance_km = element.distance_km
        if distance_km is None:
            distance_km = haversine_km(
                incident.latitude, incident.longitude, element.latitude, element.longitude
            )
        bearing = element.bearing_deg
        if bearing is None:
            bearing = bearing_deg(
                incident.latitude, incident.longitude, element.latitude, element.longitude
            )

        arrivals: list[tuple[str, float]] = []
        for projection in projections:
            ros = projection.spread.ros_toward(bearing)
            if ros < 0.5:
                continue
            minutes = distance_km * 1000.0 / ros
            if minutes <= max_minutes:
                arrivals.append((projection.scenario.id, minutes))

        if not arrivals:
            continue

        times = sorted(t for _, t in arrivals)
        outcomes.append(
            ThreatOutcome(
                name=element.name,
                kind=element.kind.value if hasattr(element.kind, "value") else str(element.kind),
                latitude=element.latitude,
                longitude=element.longitude,
                distance_km=round(distance_km, 2),
                population=element.population,
                hit_count=len(arrivals),
                scenario_count=len(projections),
                earliest_minutes=round(times[0], 1),
                median_minutes=round(times[len(times) // 2], 1),
                scenarios_hit=[s for s, _ in sorted(arrivals, key=lambda a: a[1])],
            )
        )

    # Most-reached first, then soonest. A place every scenario reaches in four
    # hours outranks one a single scenario reaches in one.
    outcomes.sort(key=lambda o: (-o.hit_count, o.earliest_minutes or 1e9))
    return outcomes
