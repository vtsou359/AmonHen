"""The Amon Hen ontology.

These are plain Pydantic models with no database or HTTP concerns — the pure
vocabulary of fire intelligence. Storage (`db.tables`) and transport
(`api.schemas`) both derive from these, never the other way round.

The shape is deliberately Gotham-like:

    Entity      a thing that exists          (Incident, Asset, ExposedElement)
    Event       something that happened      (Detection, StatusChange)
    Link        a typed edge between things  (Incident --threatens--> Settlement)

Everything carries `source` and `observed_at` so any figure on screen can be
traced back to the satellite pass or feed that produced it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from amonhen.domain.enums import (
    AssetKind,
    AssetStatus,
    BurnSeverity,
    DangerClass,
    DetectionConfidence,
    ExposureKind,
    IncidentSeverity,
    IncidentStatus,
    LinkKind,
    Satellite,
)

Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


def _new_id() -> str:
    return str(uuid.uuid4())


class Provenance(BaseModel):
    """Where a fact came from. Attached to everything the platform asserts.

    This is not bureaucracy: in an emergency the first question a commander asks
    about a number on a screen is "says who, and how old is it?"
    """

    source: str                       # e.g. "nasa_firms", "open_meteo", "operator"
    source_id: str | None = None      # the upstream record id, when there is one
    retrieved_at: datetime
    url: str | None = None
    note: str | None = None


class BaseEntity(BaseModel):
    model_config = ConfigDict(use_enum_values=False, populate_by_name=True)

    id: str = Field(default_factory=_new_id)
    created_at: datetime = Field(default_factory=lambda: datetime.now())
    updated_at: datetime | None = None
    provenance: Provenance | None = None


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


class Detection(BaseEntity):
    """A single thermal anomaly pixel from a satellite pass.

    This is the atom of active-fire intelligence. One fire produces hundreds of
    these; `services.clustering` groups them into Incidents.
    """

    latitude: Latitude
    longitude: Longitude
    observed_at: datetime
    satellite: Satellite = Satellite.UNKNOWN

    # Fire Radiative Power in megawatts — the closest thing to an intensity
    # measurement we get from orbit, and the main input to severity scoring.
    frp_mw: float | None = None
    brightness_k: float | None = None       # channel-4/I-4 brightness temperature
    brightness_k_secondary: float | None = None
    confidence: DetectionConfidence = DetectionConfidence.NOMINAL
    confidence_raw: str | None = None       # what the provider actually said

    # Nominal pixel footprint. VIIRS is ~375 m, MODIS ~1 km at nadir but
    # degrades badly off-nadir, which matters when you estimate area.
    scan_km: float | None = None
    track_km: float | None = None
    is_daytime: bool | None = None

    incident_id: str | None = None          # set once clustered


class WeatherObservation(BaseEntity):
    """Fire-relevant weather at a point in time, plus derived FWI indices."""

    latitude: Latitude
    longitude: Longitude
    observed_at: datetime
    is_forecast: bool = False

    temperature_c: float | None = None
    relative_humidity_pct: float | None = None
    wind_speed_kmh: float | None = None
    wind_direction_deg: float | None = None
    wind_gust_kmh: float | None = None
    precipitation_mm: float | None = None

    # Canadian Forest Fire Weather Index system — the system EFFIS runs on.
    ffmc: float | None = None   # Fine Fuel Moisture Code   (ignition ease)
    dmc: float | None = None    # Duff Moisture Code        (upper soil dryness)
    dc: float | None = None     # Drought Code              (deep drought)
    isi: float | None = None    # Initial Spread Index      (expected spread)
    bui: float | None = None    # Buildup Index             (available fuel)
    fwi: float | None = None    # Fire Weather Index        (overall intensity)
    danger_class: DangerClass | None = None


# --------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------


class Perimeter(BaseEntity):
    """A fire's footprint at one moment. A sequence of these is the fire's history."""

    incident_id: str
    observed_at: datetime
    geometry: dict[str, Any]           # GeoJSON Polygon / MultiPolygon
    area_ha: float
    method: str                        # "detection_hull" | "sentinel2_dnbr" | "manual"
    confidence: float | None = None    # 0-1, how much to trust this footprint


class Incident(BaseEntity):
    """A fire, as a human would talk about it. The platform's central object.

    An Incident is *derived*, not observed: it is what you get when you cluster
    detections in space and time and give the result a name, a status and a
    history. Everything else in the response workflow hangs off it.
    """

    name: str
    status: IncidentStatus = IncidentStatus.ACTIVE
    severity: IncidentSeverity = IncidentSeverity.INFORMATIONAL

    # Representative point — the FRP-weighted centroid of its detections.
    latitude: Latitude
    longitude: Longitude

    first_detected_at: datetime
    last_detected_at: datetime
    contained_at: datetime | None = None

    detection_count: int = 0
    total_frp_mw: float = 0.0
    max_frp_mw: float = 0.0
    estimated_area_ha: float = 0.0
    growth_rate_ha_per_hour: float = 0.0
    #: How `estimated_area_ha` was arrived at. "thermal_pixels" counts distinct
    #: 375 m satellite footprints and is an explicit lower bound;
    #: "sentinel2_dnbr" is a 20 m burn-scar measurement. The two differ by
    #: enough that the UI must never present them as the same kind of number.
    area_source: str = "thermal_pixels"

    # Administrative context, filled in by reverse geocoding.
    region: str | None = None
    municipality: str | None = None

    current_perimeter_id: str | None = None
    fire_weather: WeatherObservation | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None


class Asset(BaseEntity):
    """A response resource: aircraft, engine, crew, or a water source."""

    callsign: str
    kind: AssetKind
    status: AssetStatus = AssetStatus.AVAILABLE
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    home_base: str | None = None
    assigned_incident_id: str | None = None
    capacity_litres: float | None = None
    crew_size: int | None = None
    last_seen_at: datetime | None = None


class ExposedElement(BaseEntity):
    """Something worth protecting, and how badly this fire threatens it.

    This is the layer that turns a map of hotspots into a decision: not
    "where is it burning" but "what happens if it keeps burning".
    """

    name: str
    kind: ExposureKind
    latitude: Latitude
    longitude: Longitude
    geometry: dict[str, Any] | None = None
    population: int | None = None

    # Populated relative to a specific incident by services.exposure
    distance_km: float | None = None
    bearing_deg: float | None = None
    minutes_to_impact: float | None = None
    is_downwind: bool | None = None


class BurnAssessment(BaseEntity):
    """Post-fire severity, from Sentinel-2 dNBR. Feeds recovery and restoration."""

    incident_id: str
    assessed_at: datetime
    pre_fire_image_date: datetime | None = None
    post_fire_image_date: datetime | None = None

    burned_area_ha: float
    mean_dnbr: float | None = None
    severity_breakdown_ha: dict[BurnSeverity, float] = Field(default_factory=dict)
    geometry: dict[str, Any] | None = None

    # Restoration planning outputs
    erosion_risk_score: float | None = None      # 0-1
    priority_replanting_ha: float | None = None


class Link(BaseModel):
    """A typed edge in the fire graph."""

    id: str = Field(default_factory=_new_id)
    kind: LinkKind
    source_type: str
    source_id: str
    target_type: str
    target_id: str
    weight: float | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
