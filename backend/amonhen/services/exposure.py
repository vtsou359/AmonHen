"""What is at risk, and how soon.

This module is the point of the whole platform. Everything upstream — pixels,
clusters, weather codes, spread rates — exists so that this can answer one
question: *if nobody does anything, what gets hurt and when?*

The output is deliberately ordered by urgency rather than distance. The nearest
village is not the most threatened one if the wind is blowing the other way.
"""

from __future__ import annotations

from datetime import timedelta

from amonhen.domain.entities import ExposedElement, Incident, WeatherObservation
from amonhen.domain.enums import ExposureKind
from amonhen.services.gazetteer import bearing_deg, compass_point, places_within
from amonhen.services.spread import DEFAULT_FUEL, SpreadEstimate, estimate_spread, minutes_to_reach

#: How much a category matters when everything else is equal. Life safety first,
#: then the things whose loss compounds (a burnt-out hospital costs lives later).
EXPOSURE_WEIGHTS: dict[ExposureKind, float] = {
    ExposureKind.HOSPITAL: 1.0,
    ExposureKind.SETTLEMENT: 0.95,
    ExposureKind.SCHOOL: 0.9,
    ExposureKind.EVACUATION_ROUTE: 0.85,
    ExposureKind.POWER_INFRASTRUCTURE: 0.7,
    ExposureKind.INDUSTRIAL_SITE: 0.65,
    ExposureKind.CULTURAL_HERITAGE: 0.6,
    ExposureKind.NATURA2000: 0.55,
    ExposureKind.FOREST: 0.4,
}

#: Beyond this the estimate is not worth showing — the wind will have changed.
MAX_LEAD_TIME_MINUTES = 12 * 60


def assess_exposure(
    incident: Incident,
    weather: WeatherObservation | None = None,
    search_radius_km: float = 30.0,
    fuel: str = DEFAULT_FUEL,
) -> tuple[list[ExposedElement], SpreadEstimate | None]:
    """Rank everything near `incident` by how urgently it is threatened.

    Without weather we can still report proximity, and we say so by leaving
    `minutes_to_impact` empty rather than inventing a spread rate. A blank field
    is honest; a fabricated ETA is dangerous.
    """
    spread = None
    if weather and weather.isi is not None and weather.wind_direction_deg is not None:
        spread = estimate_spread(
            isi=weather.isi,
            wind_direction_deg=weather.wind_direction_deg,
            wind_speed_kmh=weather.wind_speed_kmh or 0.0,
            fuel=fuel,
        )

    exposed: list[ExposedElement] = []
    for place, distance_km in places_within(incident.latitude, incident.longitude, search_radius_km):
        bearing = bearing_deg(
            incident.latitude, incident.longitude, place.latitude, place.longitude
        )

        minutes: float | None = None
        is_downwind: bool | None = None
        if spread is not None:
            # "Downwind" means within the leading 90-degree arc of the head.
            offset = abs((bearing - spread.direction_deg + 180.0) % 360.0 - 180.0)
            is_downwind = offset <= 45.0
            minutes = minutes_to_reach(spread, distance_km, bearing)
            if minutes is not None and minutes > MAX_LEAD_TIME_MINUTES:
                minutes = None

        exposed.append(
            ExposedElement(
                name=place.name,
                kind=place.kind,
                latitude=place.latitude,
                longitude=place.longitude,
                population=place.population,
                distance_km=round(distance_km, 2),
                bearing_deg=round(bearing, 1),
                minutes_to_impact=minutes,
                is_downwind=is_downwind,
            )
        )

    exposed.sort(key=lambda e: -threat_score(e))
    return exposed, spread


def threat_score(element: ExposedElement) -> float:
    """A 0-1 urgency score combining category, proximity, trajectory and people.

    Kept as transparent arithmetic on purpose. When this drives an evacuation
    recommendation, somebody will have to explain it to a mayor, and "the model
    said so" is not an explanation.
    """
    category = EXPOSURE_WEIGHTS.get(element.kind, 0.3)

    # Proximity: full weight inside 2 km, decaying to nothing at 30 km.
    distance = element.distance_km if element.distance_km is not None else 30.0
    proximity = max(0.0, min(1.0, (30.0 - distance) / 28.0))

    # Trajectory: an ETA dominates when we have one.
    if element.minutes_to_impact is not None:
        trajectory = max(0.0, 1.0 - element.minutes_to_impact / MAX_LEAD_TIME_MINUTES)
    elif element.is_downwind:
        trajectory = 0.5
    else:
        trajectory = 0.15

    # Population, compressed: 10 people matter, 100k do not matter 10,000x more.
    if element.population:
        from math import log10

        people = min(1.0, log10(element.population + 1) / 5.0)
    else:
        people = 0.25

    score = 0.30 * category + 0.25 * proximity + 0.30 * trajectory + 0.15 * people
    return round(min(1.0, score), 4)


#: Plain words for the UI. The enum values are precise and meaningless to most
#: readers; this is the display boundary on the backend side.
_STATUS_WORDS = {
    "active": "burning",
    "contained": "held",
    "controlled": "under control",
    "out": "out",
    "archived": "archived",
}

_SEVERITY_WORDS = {
    "informational": "low concern",
    "minor": "minor",
    "moderate": "moderate",
    "major": "serious",
    "critical": "critical",
}

_COMPASS_WORDS = {
    "N": "north", "NNE": "north-north-east", "NE": "north-east", "ENE": "east-north-east",
    "E": "east", "ESE": "east-south-east", "SE": "south-east", "SSE": "south-south-east",
    "S": "south", "SSW": "south-south-west", "SW": "south-west", "WSW": "west-south-west",
    "W": "west", "WNW": "west-north-west", "NW": "north-west", "NNW": "north-north-west",
}


def summarise(
    incident: Incident,
    exposed: list[ExposedElement],
    spread: SpreadEstimate | None,
    terrain: object | None = None,
) -> str:
    """A short plain-language brief.

    This is the text a duty officer reads at 3 a.m., so it avoids the vocabulary
    of the data model entirely: no "severity informational", no "head rate of
    spread", no compass abbreviations. It states what is known, and it refuses to
    imply certainty it does not have.

    `terrain` is accepted so the closing caveat can tell the truth. It used to
    say "flat-ground estimate" unconditionally, which became a lie the moment
    slope was measured — and sat directly above a panel reporting the slope.
    """
    status = _STATUS_WORDS.get(_value(incident.status), _value(incident.status))
    severity = _SEVERITY_WORDS.get(_value(incident.severity), _value(incident.severity))

    parts = [f"{incident.estimated_area_ha:,.0f} hectares burnt, {status}, {severity}."]

    if incident.growth_rate_ha_per_hour > 1:
        parts.append(f"Growing by about {incident.growth_rate_ha_per_hour:,.0f} hectares an hour.")

    if spread is not None:
        heading = _COMPASS_WORDS.get(spread.direction_label, spread.direction_label)
        parts.append(
            f"The front is moving {heading} at roughly "
            f"{spread.head_ros_m_per_min:.0f} metres a minute."
        )

    downwind = sorted(
        (e for e in exposed if e.minutes_to_impact is not None and e.is_downwind),
        key=lambda e: e.minutes_to_impact or 0.0,
    )[:3]
    if downwind:
        threats = "; ".join(
            f"{e.name} in about {_humanise_minutes(e.minutes_to_impact)}"
            for e in downwind
            if e.minutes_to_impact is not None
        )
        parts.append(f"If it keeps going this way: {threats}.")
    elif exposed:
        nearest = exposed[0]
        where = _COMPASS_WORDS.get(compass_point(nearest.bearing_deg or 0), "nearby")
        parts.append(
            f"Nearest place is {nearest.name}, {nearest.distance_km:.0f} km to the {where}."
        )

    ground = getattr(terrain, "descriptor", None)
    if ground:
        parts.append(f"The ground here is {ground}.")
    parts.append("Speeds are rough estimates, not a forecast.")

    return " ".join(parts)


def _value(field: object) -> str:
    return field.value if hasattr(field, "value") else str(field)


def _humanise_minutes(minutes: float) -> str:
    if minutes < 90:
        return f"{minutes:.0f} min"
    return f"{minutes / 60:.1f} h"
