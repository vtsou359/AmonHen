"""HTTP response contracts.

Separate from `domain.entities` on purpose: the wire format is allowed to change
for the frontend's convenience without dragging the domain model along with it,
and the domain is allowed to hold things we do not expose.

Everything spatial is offered as GeoJSON, because that is what MapLibre and
deck.gl consume natively — no conversion layer in the browser.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from amonhen.domain.entities import ExposedElement, Incident, Perimeter, WeatherObservation
from amonhen.domain.enums import DangerClass


class FwiSummary(BaseModel):
    ffmc: float
    dmc: float
    dc: float
    isi: float
    bui: float
    fwi: float
    danger_class: DangerClass


class SpreadSummary(BaseModel):
    head_ros_m_per_min: float
    flank_ros_m_per_min: float
    back_ros_m_per_min: float
    length_to_breadth: float
    direction_deg: float
    direction_label: str
    fuel_model: str
    confidence: str
    caveats: list[str]


class PlausibilitySummary(BaseModel):
    """Whether a cluster of heat detections behaves like a wildfire."""

    verdict: str
    score: float
    #: Plain-language, shown in the UI — every point that moved the score.
    reasons: list[str] = Field(default_factory=list)
    signals: dict[str, float] = Field(default_factory=dict)


class IncidentDetail(BaseModel):
    """Everything about one fire. Backs the incident dossier view."""

    incident: Incident
    perimeter: Perimeter | None = None
    weather: WeatherObservation | None = None
    danger: FwiSummary | None = None
    spread: SpreadSummary | None = None
    exposed: list[ExposedElement] = Field(default_factory=list)
    projection: "ProjectionSummary | None" = None
    plausibility: PlausibilitySummary | None = None
    brief: str = ""
    detection_count: int = 0


class IncidentSummary(BaseModel):
    """The list-view row. Deliberately small — the incident list can be long."""

    id: str
    name: str
    status: str
    severity: str
    latitude: float
    longitude: float
    estimated_area_ha: float
    growth_rate_ha_per_hour: float
    max_frp_mw: float
    detection_count: int
    first_detected_at: datetime
    last_detected_at: datetime
    danger_class: DangerClass | None = None
    top_threat: str | None = None
    minutes_to_top_threat: float | None = None
    #: "wildfire" | "probable" | "questionable" | "likely_not_wildfire"
    verdict: str = "probable"
    verdict_score: float = 0.5


class PictureResponse(BaseModel):
    """The whole situation, as of one instant."""

    generated_at: datetime
    area_of_interest: str
    active_count: int
    total_incidents: int
    total_area_ha: float
    unclustered_detections: int
    #: Incidents whose behaviour suggests industry rather than fire.
    suspect_count: int = 0
    #: Detections that were inside the FIRMS bounding box but outside the
    #: country polygon — i.e. fires in Turkey, Albania, North Macedonia or
    #: Bulgaria. Reported so the filtering is visible rather than silent.
    dropped_outside_boundary: int = 0
    incidents: list[IncidentSummary]
    sources: list[dict[str, Any]]


class GeoJsonFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any]
    properties: dict[str, Any]


class GeoJsonFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[GeoJsonFeature]

    #: Non-standard but harmless, and it saves the frontend a second request
    #: just to find out how fresh the layer is.
    generated_at: datetime | None = None
    attribution: str | None = None


class SourceStatus(BaseModel):
    name: str
    mode: Literal["live", "fixture"]
    attribution: str
    homepage: str
    requires_credentials: bool


class SystemStatus(BaseModel):
    status: Literal["ok", "degraded"]
    environment: str
    area_of_interest: str
    bbox: tuple[float, float, float, float]
    live_sources: bool
    sources: list[SourceStatus]
    picture_generated_at: datetime | None = None
    #: Whether country-polygon filtering is active. False means every detection
    #: in the bounding box is being shown, including foreign fires.
    boundary_filter_active: bool = False
    boundary_name: str | None = None


class OverlayCatalogue(BaseModel):
    """Raster overlays the map can draw on top of its basemap.

    The shape is intentionally loose (`overlays` is a list of dicts): these are
    passed through to the map layer config largely untouched, and pinning a
    strict schema here would mean a backend change every time a layer gains a
    property.
    """

    attribution: str
    wms_endpoint: str
    #: The date the client should request for time-aware layers. Decided here so
    #: "most recent available" is one decision in one place, rather than every
    #: client inventing its own idea of today.
    time: str
    overlays: list[dict[str, Any]]


class ScenarioSummary(BaseModel):
    """One named set of assumptions and what it implies."""

    id: str
    label: str
    rationale: str
    fuel: str
    head_ros_m_per_min: float
    direction_deg: float
    direction_label: str
    areas_ha: dict[int, float]


class ThreatSummary(BaseModel):
    name: str
    kind: str
    latitude: float
    longitude: float
    distance_km: float
    population: int | None = None
    hit_count: int
    scenario_count: int
    #: Fraction of the ensemble reaching this place. Not a probability — see
    #: services/projection.py on why that distinction is kept.
    likelihood: float
    earliest_minutes: float | None = None
    median_minutes: float | None = None
    scenarios_hit: list[str] = Field(default_factory=list)


class TerrainSummary(BaseModel):
    """Local ground shape, from the Copernicus DEM."""

    elevation_m: float
    slope_pct: float
    aspect_deg: float
    relief_m: float
    #: Plain words — "hilly", "steep" — because a percentage grade means little
    #: to most readers.
    descriptor: str
    source: str


class ProjectionSummary(BaseModel):
    incident_id: str
    generated_at: datetime
    horizons_minutes: list[int]
    scenarios: list[ScenarioSummary]
    envelope_areas_ha: dict[int, float]
    threats: list[ThreatSummary]
    terrain: TerrainSummary | None = None
    caveats: list[str]
