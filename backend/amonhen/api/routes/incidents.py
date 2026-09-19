"""Incident endpoints — the operational picture and individual fire dossiers."""

from __future__ import annotations

from math import ceil

from fastapi import APIRouter, HTTPException, Query

from amonhen.api.schemas import (
    BurnScarSummary,
    BurnScarUnavailableSummary,
    FuelMoistureSummary,
    FwiSummary,
    IncidentDetail,
    IncidentSummary,
    LandCoverSummary,
    PictureResponse,
    PlausibilitySummary,
    ProjectionSummary,
    ScenarioSummary,
    TerrainSummary,
    SpreadSummary,
    ThreatSummary,
)
from amonhen.services.burn_scar import BurnScar, BurnScarUnavailable
from amonhen.services.operations import (
    DEFAULT_DAY_RANGE,
    IncidentView,
    RefreshTooSoon,
    operations,
)

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("", response_model=PictureResponse, summary="Current operational picture")
async def list_incidents(
    max_age_seconds: int = Query(600, ge=0, le=3600, description="Accept a cached picture this old"),
    status: str | None = Query(None, description="Filter by incident status"),
    min_area_ha: float = Query(0.0, ge=0.0),
    include_suspect: bool = Query(
        True,
        description=(
            "Include detections that behave more like industry than wildfire. "
            "Default True: hiding them by default would silently drop real fires "
            "that happen to look odd."
        ),
    ),
) -> PictureResponse:
    picture = await operations.get_picture(max_age_seconds=max_age_seconds)

    views = picture.incidents
    if status:
        views = [v for v in views if v.incident.status.value == status]
    if min_area_ha:
        views = [v for v in views if v.incident.estimated_area_ha >= min_area_ha]
    suspect_count = sum(1 for v in views if v.plausibility.is_suspect)
    if not include_suspect:
        views = [v for v in views if not v.plausibility.is_suspect]

    return PictureResponse(
        generated_at=picture.generated_at,
        area_of_interest=picture.area_of_interest,
        active_count=picture.active_count,
        total_incidents=len(views),
        total_area_ha=picture.total_area_ha,
        unclustered_detections=len(picture.unclustered),
        dropped_outside_boundary=picture.dropped_outside_boundary,
        suspect_count=suspect_count,
        incidents=[_to_summary(v) for v in views],
        sources=picture.source_status,
    )


@router.get("/{incident_id}", response_model=IncidentDetail, summary="Full incident dossier")
async def get_incident(incident_id: str) -> IncidentDetail:
    view = await _find(incident_id)
    return IncidentDetail(
        incident=view.incident,
        perimeter=view.perimeter,
        weather=view.weather,
        danger=FwiSummary(**vars(view.danger)) if view.danger else None,
        spread=SpreadSummary(**vars(view.spread)) if view.spread else None,
        exposed=view.exposed,
        projection=_to_projection(view),
        burn_scar=_to_burn_scar(view),
        burn_scar_unavailable=_to_burn_scar_unavailable(view),
        plausibility=PlausibilitySummary(
            verdict=view.plausibility.verdict,
            score=view.plausibility.score,
            reasons=view.plausibility.reasons,
            signals=view.plausibility.signals,
        ),
        brief=view.brief,
        detection_count=len(view.detections),
    )


def _to_burn_scar(view: IncidentView) -> BurnScarSummary | None:
    scar = view.burn_scar
    if not isinstance(scar, BurnScar):
        return None
    return BurnScarSummary(
        burned_area_ha=scar.burned_area_ha,
        mean_dnbr=scar.mean_dnbr,
        mean_rdnbr=scar.mean_rdnbr,
        severity_ha=scar.severity_ha,
        dominant_severity=scar.dominant_severity,
        pre_image_date=scar.pre_image_date,
        post_image_date=scar.post_image_date,
        usable_fraction=scar.usable_fraction,
        confidence=scar.confidence,
        is_partial=scar.is_partial,
        note=scar.note,
    )


def _to_burn_scar_unavailable(view: IncidentView) -> BurnScarUnavailableSummary | None:
    """Why there is no measured burn scar.

    Reported rather than left as a null: an operator looking at a thermal-pixel
    estimate needs to know whether the better number is coming in a day or is
    never coming at all.
    """
    reason = view.burn_scar
    if not isinstance(reason, BurnScarUnavailable):
        return None
    return BurnScarUnavailableSummary(
        reason=reason.reason, detail=reason.detail, transient=reason.transient
    )


def _to_projection(view: IncidentView) -> ProjectionSummary | None:
    """Flatten the ensemble for transport.

    Footprint geometry is deliberately left out: it is large, and the map fetches
    it from /layers/projections where it can be refreshed independently of the
    dossier text.
    """
    projection = view.projection
    if projection is None:
        return None

    return ProjectionSummary(
        incident_id=projection.incident_id,
        generated_at=projection.generated_at,
        horizons_minutes=list(projection.horizons_minutes),
        scenarios=[
            ScenarioSummary(
                id=p.scenario.id,
                label=p.scenario.label,
                rationale=p.scenario.rationale,
                fuel=p.fuel_used,
                live_moisture_pct=p.live_moisture_pct,
                head_ros_m_per_min=p.spread.head_ros_m_per_min,
                direction_deg=p.spread.direction_deg,
                direction_label=p.spread.direction_label,
                areas_ha=p.areas_ha,
            )
            for p in projection.projections
        ],
        envelope_areas_ha=projection.envelope_areas_ha,
        threats=[
            ThreatSummary(
                name=t.name,
                kind=t.kind,
                latitude=t.latitude,
                longitude=t.longitude,
                distance_km=t.distance_km,
                population=t.population,
                hit_count=t.hit_count,
                scenario_count=t.scenario_count,
                likelihood=t.likelihood,
                earliest_minutes=t.earliest_minutes,
                median_minutes=t.median_minutes,
                scenarios_hit=t.scenarios_hit,
            )
            for t in projection.threats
        ],
        land_cover=(
            LandCoverSummary(
                code=projection.land_cover.code,
                label=projection.land_cover.label,
                fuel=projection.land_cover.fuel,
                burnable=projection.land_cover.burnable,
                source=projection.land_cover.source,
            )
            if projection.land_cover is not None and projection.land_cover.code
            else None
        ),
        fuel_moisture=(
            FuelMoistureSummary(
                ndmi=projection.fuel_moisture.ndmi,
                ndvi=projection.fuel_moisture.ndvi,
                live_moisture_pct=projection.fuel_moisture.live_moisture_pct,
                descriptor=projection.fuel_moisture.descriptor,
                greenness=projection.fuel_moisture.greenness,
                spread_factor=projection.fuel_moisture.spread_factor,
                observed_at=projection.fuel_moisture.observed_at,
                sample_pixels=projection.fuel_moisture.sample_pixels,
                source=projection.fuel_moisture.source,
            )
            if projection.fuel_moisture is not None
            else None
        ),
        terrain=(
            TerrainSummary(
                elevation_m=projection.terrain.elevation_m,
                slope_pct=projection.terrain.slope_pct,
                aspect_deg=projection.terrain.aspect_deg,
                relief_m=projection.terrain.relief_m,
                descriptor=projection.terrain.descriptor,
                source=projection.terrain.source,
            )
            if projection.terrain is not None
            else None
        ),
        caveats=projection.caveats,
    )


@router.post("/refresh", response_model=PictureResponse, summary="Force a rebuild")
async def refresh(
    day_range: int = Query(DEFAULT_DAY_RANGE, ge=1, le=5),
) -> PictureResponse:
    """Re-run the whole chain now, ignoring every cache.

    Defaults to the same window the automatic rebuild uses. It used to default
    to 3 while the scheduled rebuild used 5, so pressing Refresh dropped two
    days of detections and roughly half the incidents — the opposite of what the
    button promises.

    Bounded to 1-5 days, which is FIRMS' real limit for the area endpoint —
    not the 1-10 this once claimed. Asking for more returns the plain text
    "Invalid day range. Expects [1..5]." with an HTTP 200, which the connector
    catches and treats as an empty product; the net effect was a silent loss of
    every detection rather than an error.

    Rate-limited, answering HTTP 429 with `Retry-After` when asked again too
    soon: bypassing the caches costs a full round of upstream requests against
    the operator's NASA key, and a deployed instance is reachable by anyone.
    """
    try:
        picture = await operations.force_refresh(day_range=day_range)
    except RefreshTooSoon as exc:
        retry_after = ceil(exc.retry_after_seconds)
        raise HTTPException(
            status_code=429,
            detail=f"A refresh ran moments ago. Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        ) from exc
    return PictureResponse(
        generated_at=picture.generated_at,
        area_of_interest=picture.area_of_interest,
        active_count=picture.active_count,
        total_incidents=len(picture.incidents),
        total_area_ha=picture.total_area_ha,
        unclustered_detections=len(picture.unclustered),
        dropped_outside_boundary=picture.dropped_outside_boundary,
        suspect_count=sum(1 for v in picture.incidents if v.plausibility.is_suspect),
        incidents=[_to_summary(v) for v in picture.incidents],
        sources=picture.source_status,
    )


async def _find(incident_id: str) -> IncidentView:
    picture = await operations.get_picture()
    for view in picture.incidents:
        if view.incident.id == incident_id:
            return view
    raise HTTPException(status_code=404, detail=f"No incident {incident_id} in the current picture")


def _to_summary(view: IncidentView) -> IncidentSummary:
    incident = view.incident
    # Surface the single most urgent downwind threat, so the list view can be
    # scanned for "who needs to move" without opening every dossier.
    downwind = sorted(
        (e for e in view.exposed if e.minutes_to_impact is not None and e.is_downwind),
        key=lambda e: e.minutes_to_impact or 0.0,
    )
    top = downwind[0] if downwind else None

    return IncidentSummary(
        id=incident.id,
        name=incident.name,
        status=incident.status.value,
        severity=incident.severity.value,
        latitude=incident.latitude,
        longitude=incident.longitude,
        estimated_area_ha=incident.estimated_area_ha,
        growth_rate_ha_per_hour=incident.growth_rate_ha_per_hour,
        max_frp_mw=incident.max_frp_mw,
        detection_count=incident.detection_count,
        first_detected_at=incident.first_detected_at,
        last_detected_at=incident.last_detected_at,
        area_source=incident.area_source,
        danger_class=view.danger.danger_class if view.danger else None,
        top_threat=top.name if top else None,
        minutes_to_top_threat=top.minutes_to_impact if top else None,
        verdict=view.plausibility.verdict,
        verdict_score=view.plausibility.score,
    )
