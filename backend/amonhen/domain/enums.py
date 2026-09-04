"""Controlled vocabularies.

Fire intelligence is only useful if everyone means the same thing by "contained"
or "severe". These enums are the single source of truth for that shared meaning,
and they are exported to the frontend so both sides speak the same language.
"""

from __future__ import annotations

from enum import StrEnum


class Satellite(StrEnum):
    """Which instrument saw a detection.

    Pixel size varies enormously between them — VIIRS is 375 m, MODIS ~1 km at
    nadir and considerably worse off-nadir — which is why `clustering.estimate_area_ha`
    reads the footprint FIRMS reports per detection rather than assuming one.
    Two satellites agreeing also counts as corroboration in the fire/not-fire test.
    """

    VIIRS_SNPP = "viirs_snpp"
    VIIRS_NOAA20 = "viirs_noaa20"
    VIIRS_NOAA21 = "viirs_noaa21"
    MODIS_TERRA = "modis_terra"
    MODIS_AQUA = "modis_aqua"
    SENTINEL3_SLSTR = "sentinel3_slstr"
    UNKNOWN = "unknown"


class DetectionConfidence(StrEnum):
    """FIRMS reports confidence as low/nominal/high (VIIRS) or 0-100 (MODIS).
    We normalise both onto this scale."""

    LOW = "low"
    NOMINAL = "nominal"
    HIGH = "high"


class IncidentStatus(StrEnum):
    """Where a fire is in its life, inferred from detection recency.

    Inferred, never reported: satellites go quiet for cloud, for smoke and
    simply for lack of an overpass, so `clustering.derive_status` downgrades
    slowly and never calls a fire out on one missed pass. CONTROLLED is the
    exception — it only comes from a responder saying so.
    """

    ACTIVE = "active"          # still producing new detections
    CONTAINED = "contained"    # perimeter stable, no fresh detections at the edge
    CONTROLLED = "controlled"  # declared under control by responders
    OUT = "out"                # no detections for the quiet-period threshold
    ARCHIVED = "archived"


class IncidentSeverity(StrEnum):
    """Derived from area, growth rate, FRP and exposure — see services.severity."""

    INFORMATIONAL = "informational"
    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"
    CRITICAL = "critical"


class AssetKind(StrEnum):
    """A response resource, for the asset-tracking work that is not built yet.

    Defined here so the vocabulary is settled and exported to the frontend
    before anything depends on it. Nothing currently populates `Asset`.
    """

    AERIAL_TANKER = "aerial_tanker"
    HELICOPTER = "helicopter"
    ENGINE = "engine"
    GROUND_CREW = "ground_crew"
    BULLDOZER = "bulldozer"
    DRONE = "drone"
    WATER_SOURCE = "water_source"


class AssetStatus(StrEnum):
    """What a response resource is currently doing. See `AssetKind`."""

    AVAILABLE = "available"
    ASSIGNED = "assigned"
    EN_ROUTE = "en_route"
    ON_SCENE = "on_scene"
    OUT_OF_SERVICE = "out_of_service"


class ExposureKind(StrEnum):
    """Things we care about protecting. Drives the 'what is at risk' panel."""

    SETTLEMENT = "settlement"
    HOSPITAL = "hospital"
    SCHOOL = "school"
    POWER_INFRASTRUCTURE = "power_infrastructure"
    INDUSTRIAL_SITE = "industrial_site"
    NATURA2000 = "natura2000"
    FOREST = "forest"
    CULTURAL_HERITAGE = "cultural_heritage"
    EVACUATION_ROUTE = "evacuation_route"


class DangerClass(StrEnum):
    """EFFIS fire danger classes, driven by the Fire Weather Index."""

    VERY_LOW = "very_low"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"
    EXTREME = "extreme"


class BurnSeverity(StrEnum):
    """USGS/Key & Benson dNBR classes, used by the recovery modules."""

    UNBURNED = "unburned"
    LOW = "low"
    MODERATE_LOW = "moderate_low"
    MODERATE_HIGH = "moderate_high"
    HIGH = "high"


class LinkKind(StrEnum):
    """Edges in the fire graph. This is what makes the Graph view meaningful."""

    DETECTED_BY = "detected_by"
    PART_OF = "part_of"
    THREATENS = "threatens"
    ASSIGNED_TO = "assigned_to"
    ADJACENT_TO = "adjacent_to"
    PRECEDED_BY = "preceded_by"
    OBSERVED_AT = "observed_at"
