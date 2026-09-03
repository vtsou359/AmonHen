"""Map layers, as GeoJSON.

One endpoint per visual layer, because that is how the map consumes them and it
lets the client refresh a fast-moving layer (detections) without re-fetching a
slow one (perimeters).
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from amonhen.api.schemas import GeoJsonFeature, GeoJsonFeatureCollection, OverlayCatalogue
from amonhen.sources import overlays as overlay_catalogue
from amonhen.services.gazetteer import SEED_PLACES
from amonhen.services.operations import operations

router = APIRouter(prefix="/layers", tags=["layers"])

FIRMS_ATTRIBUTION = "NASA FIRMS (LANCE/EOSDIS) · Open-Meteo · Amon Hen"


@router.get("/detections", response_model=GeoJsonFeatureCollection)
async def detections_layer(
    include_noise: bool = Query(True, description="Include unclustered detections"),
) -> GeoJsonFeatureCollection:
    """Every thermal anomaly, coloured by FRP and aged by acquisition time."""
    picture = await operations.get_picture()
    features: list[GeoJsonFeature] = []

    for view in picture.incidents:
        for detection in view.detections:
            features.append(_detection_feature(detection, view.incident.id, view.incident.name))

    if include_noise:
        for detection in picture.unclustered:
            features.append(_detection_feature(detection, None, None))

    return GeoJsonFeatureCollection(
        features=features,
        generated_at=picture.generated_at,
        attribution=FIRMS_ATTRIBUTION,
    )


@router.get("/perimeters", response_model=GeoJsonFeatureCollection)
async def perimeters_layer() -> GeoJsonFeatureCollection:
    """Fire footprints, of two quite different kinds.

    `method` says which, and the map must style them differently:

    * `detection_hull` — a convex hull around 375 m thermal pixels. A bounding
      sketch, drawn dashed and faint, and the dossier says so in words.
    * `sentinel2_dnbr` — a burn scar measured from 20 m imagery. A real boundary.

    Presenting the two with the same styling would be a lie the map tells on its
    own, without anyone having written it down.
    """
    picture = await operations.get_picture()
    features = [
        GeoJsonFeature(
            geometry=view.perimeter.geometry,
            properties={
                "incident_id": view.incident.id,
                "name": view.incident.name,
                "status": view.incident.status.value,
                "severity": view.incident.severity.value,
                "area_ha": view.perimeter.area_ha,
                "method": view.perimeter.method,
                "is_measured": view.perimeter.method == "sentinel2_dnbr",
                "confidence": view.perimeter.confidence,
                "observed_at": view.perimeter.observed_at.isoformat(),
            },
        )
        for view in picture.incidents
        if view.perimeter is not None
    ]
    return GeoJsonFeatureCollection(
        features=features, generated_at=picture.generated_at, attribution=FIRMS_ATTRIBUTION
    )


@router.get("/incidents", response_model=GeoJsonFeatureCollection)
async def incidents_layer() -> GeoJsonFeatureCollection:
    """Incident markers at the FRP-weighted centroid, with a spread vector."""
    picture = await operations.get_picture()
    features: list[GeoJsonFeature] = []

    for view in picture.incidents:
        incident = view.incident
        features.append(
            GeoJsonFeature(
                geometry={"type": "Point", "coordinates": [incident.longitude, incident.latitude]},
                properties={
                    "id": incident.id,
                    "name": incident.name,
                    "status": incident.status.value,
                    "severity": incident.severity.value,
                    "area_ha": incident.estimated_area_ha,
                    "area_source": incident.area_source,
                    "growth_rate_ha_per_hour": incident.growth_rate_ha_per_hour,
                    "max_frp_mw": incident.max_frp_mw,
                    "detection_count": incident.detection_count,
                    "first_detected_at": incident.first_detected_at.isoformat(),
                    "last_detected_at": incident.last_detected_at.isoformat(),
                    "danger_class": view.danger.danger_class.value if view.danger else None,
                    "fwi": view.danger.fwi if view.danger else None,
                    "spread_direction_deg": view.spread.direction_deg if view.spread else None,
                    "spread_direction_label": view.spread.direction_label if view.spread else None,
                    "head_ros_m_per_min": view.spread.head_ros_m_per_min if view.spread else None,
                    "brief": view.brief,
                },
            )
        )
    return GeoJsonFeatureCollection(
        features=features, generated_at=picture.generated_at, attribution=FIRMS_ATTRIBUTION
    )


@router.get("/exposure", response_model=GeoJsonFeatureCollection)
async def exposure_layer(
    incident_id: str | None = Query(None, description="Restrict to one incident's threats"),
) -> GeoJsonFeatureCollection:
    """Places at risk, carrying their distance, bearing and ETA."""
    picture = await operations.get_picture()
    features: list[GeoJsonFeature] = []

    for view in picture.incidents:
        if incident_id and view.incident.id != incident_id:
            continue
        for element in view.exposed:
            if element.minutes_to_impact is None and not element.is_downwind:
                continue  # not meaningfully threatened; keep the layer readable
            features.append(
                GeoJsonFeature(
                    geometry={
                        "type": "Point",
                        "coordinates": [element.longitude, element.latitude],
                    },
                    properties={
                        "incident_id": view.incident.id,
                        "name": element.name,
                        "kind": element.kind.value,
                        "population": element.population,
                        "distance_km": element.distance_km,
                        "bearing_deg": element.bearing_deg,
                        "minutes_to_impact": element.minutes_to_impact,
                        "is_downwind": element.is_downwind,
                    },
                )
            )
    return GeoJsonFeatureCollection(
        features=features, generated_at=picture.generated_at, attribution=FIRMS_ATTRIBUTION
    )


@router.get("/places", response_model=GeoJsonFeatureCollection)
async def places_layer() -> GeoJsonFeatureCollection:
    """The full gazetteer, for context and for search."""
    return GeoJsonFeatureCollection(
        features=[
            GeoJsonFeature(
                geometry={"type": "Point", "coordinates": [p.longitude, p.latitude]},
                properties={
                    "name": p.name,
                    "name_el": p.name_el,
                    "kind": p.kind.value,
                    "population": p.population,
                    "region": p.region,
                },
            )
            for p in SEED_PLACES
        ],
        attribution="Amon Hen seed gazetteer",
    )


def _detection_feature(detection, incident_id: str | None, incident_name: str | None) -> GeoJsonFeature:
    return GeoJsonFeature(
        geometry={"type": "Point", "coordinates": [detection.longitude, detection.latitude]},
        properties={
            "id": detection.id,
            "incident_id": incident_id,
            "incident_name": incident_name,
            "observed_at": detection.observed_at.isoformat(),
            "satellite": detection.satellite.value,
            "frp_mw": detection.frp_mw,
            "brightness_k": detection.brightness_k,
            "confidence": detection.confidence.value,
            "is_daytime": detection.is_daytime,
            "is_noise": incident_id is None,
        },
    )


@router.get("/overlays", response_model=OverlayCatalogue, summary="Copernicus raster overlays")
async def overlays() -> OverlayCatalogue:
    """The catalogue of EFFIS raster layers available as map overlays.

    Served from the backend rather than hardcoded in the frontend so layers can
    be added, retired or re-described without a frontend release. Each entry
    carries a `why` — the decision the layer supports — because a map with ten
    unexplained toggles is a map nobody turns anything on in.

    Tiles are fetched by the browser straight from EFFIS: it is public, keyless,
    CORS-open and cached for an hour, so proxying would add latency and a cache
    to maintain for no benefit.
    """
    return OverlayCatalogue(
        attribution=overlay_catalogue.ATTRIBUTION,
        wms_endpoint=overlay_catalogue.EFFIS_WMS,
        time=overlay_catalogue.current_layer_date(),
        overlays=overlay_catalogue.catalogue(),
    )


@router.get("/projections", response_model=GeoJsonFeatureCollection)
async def projections_layer(
    incident_id: str | None = Query(None, description="Restrict to one incident"),
    kind: str = Query("envelope", pattern="^(envelope|core|scenarios)$"),
) -> GeoJsonFeatureCollection:
    """Projected fire footprints.

    Three views of the same ensemble, and the distinction matters operationally:

    * `envelope` — the union of every scenario. "Could reach." What you evacuate
      against.
    * `core` — the intersection. "Every scenario agrees this burns." Usually far
      smaller than people expect, which is the point.
    * `scenarios` — each scenario separately, for inspecting *why* the envelope
      has the shape it does.
    """
    picture = await operations.get_picture()
    features: list[GeoJsonFeature] = []

    for view in picture.incidents:
        if incident_id and view.incident.id != incident_id:
            continue
        projection = view.projection
        if projection is None:
            continue

        common = {
            "incident_id": view.incident.id,
            "incident_name": view.incident.name,
            "severity": view.incident.severity.value,
        }

        if kind == "scenarios":
            for scenario_projection in projection.projections:
                for minutes, geometry in scenario_projection.footprints.items():
                    features.append(
                        GeoJsonFeature(
                            geometry=geometry,
                            properties={
                                **common,
                                "kind": "scenario",
                                "scenario": scenario_projection.scenario.id,
                                "scenario_label": scenario_projection.scenario.label,
                                "rationale": scenario_projection.scenario.rationale,
                                "minutes": minutes,
                                "area_ha": scenario_projection.areas_ha.get(minutes),
                            },
                        )
                    )
            continue

        source = projection.envelope if kind == "envelope" else projection.core
        for minutes, geometry in source.items():
            features.append(
                GeoJsonFeature(
                    geometry=geometry,
                    properties={
                        **common,
                        "kind": kind,
                        "minutes": minutes,
                        "area_ha": projection.envelope_areas_ha.get(minutes),
                        "scenario_count": len(projection.projections),
                    },
                )
            )

    # Largest first, so the 6 h band does not paint over the 1 h band.
    features.sort(key=lambda f: -(f.properties.get("minutes") or 0))
    return GeoJsonFeatureCollection(
        features=features, generated_at=picture.generated_at, attribution=FIRMS_ATTRIBUTION
    )
