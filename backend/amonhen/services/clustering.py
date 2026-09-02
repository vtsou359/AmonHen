"""Turning satellite pixels into incidents.

This is the step that makes the platform useful. FIRMS gives you a scatter of
thermal anomalies; a commander needs "the Varnavas fire, started 14:20, 340 ha,
growing 40 ha/h, heading south-west toward Marathon". Getting from one to the
other is a clustering problem in space *and* time.

Why not plain spatial clustering: two fires 2 km apart on the same afternoon are
one incident; the same two points ten days apart are two. Time has to be in the
distance metric.

Why DBSCAN and not k-means: we do not know how many fires there are (that is the
answer we want), fires are not convex blobs, and we need isolated one-off pixels
to fall out as noise rather than being forced into a cluster. DBSCAN gives us
all three.

Implementation: this is ST-DBSCAN — two detections are neighbours when they
are within `eps_km` of each other *and* within `eps_hours`, as two independent
tests rather than one combined distance. The neighbour graph is built with a
KD-tree and the time mask applied to its sparse edges, so it stays O(n log n).
A bad fire week in Greece is tens of thousands of detections; this keeps that
comfortably interactive.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import numpy as np
from scipy import sparse
from sklearn.cluster import DBSCAN
from sklearn.neighbors import radius_neighbors_graph, sort_graph_by_row_values

from amonhen.core.config import settings
from amonhen.core.logging import get_logger
from amonhen.domain.entities import Detection, Incident, Perimeter
from amonhen.domain.enums import IncidentSeverity, IncidentStatus

log = get_logger(__name__)

EARTH_RADIUS_KM = 6371.0088

#: A fire with no fresh detections for this long is presumed out. Two full days
#: covers a night plus a cloudy day, which is the usual reason a real fire goes
#: quiet on satellite without actually being out.
QUIET_HOURS_UNTIL_OUT = 48.0
QUIET_HOURS_UNTIL_CONTAINED = 12.0


def cluster_detections(
    detections: list[Detection],
    eps_km: float | None = None,
    eps_hours: float | None = None,
    min_samples: int | None = None,
) -> dict[int, list[Detection]]:
    """Group detections into spatio-temporal clusters.

    Returns a mapping of cluster label -> detections. Label -1 is DBSCAN's noise
    class: isolated detections we are not willing to call a fire yet. They are
    returned rather than dropped, because a single high-confidence VIIRS pixel in
    the wrong place is exactly what you want an analyst to look at.
    """
    eps_km = eps_km if eps_km is not None else settings.cluster_eps_km
    eps_hours = eps_hours if eps_hours is not None else settings.cluster_eps_hours
    min_samples = min_samples if min_samples is not None else settings.cluster_min_samples

    if not detections:
        return {}

    positions_km, hours = _project(detections)
    graph = _spatiotemporal_graph(positions_km, hours, eps_km=eps_km, eps_hours=eps_hours)
    labels = DBSCAN(eps=eps_km, min_samples=min_samples, metric="precomputed").fit_predict(graph)

    clusters: dict[int, list[Detection]] = defaultdict(list)
    for detection, label in zip(detections, labels, strict=True):
        clusters[int(label)].append(detection)

    log.info(
        "clustering.complete",
        detections=len(detections),
        clusters=len([k for k in clusters if k >= 0]),
        noise=len(clusters.get(-1, [])),
        eps_km=eps_km,
        eps_hours=eps_hours,
    )
    return dict(clusters)


def _project(detections: list[Detection]) -> tuple[np.ndarray, np.ndarray]:
    """Project (lat, lon) into local kilometres and time into hours.

    Longitude degrees shrink with latitude, so we scale them by cos(lat) about
    the centroid — an equirectangular approximation. Over a country-sized area
    the error is well under a percent, far below the 375 m pixel we are
    clustering, and it buys us a KD-tree instead of a distance matrix.
    """
    latitudes = np.array([d.latitude for d in detections], dtype=np.float64)
    longitudes = np.array([d.longitude for d in detections], dtype=np.float64)
    timestamps = np.array(
        [_as_utc(d.observed_at).timestamp() for d in detections], dtype=np.float64
    )

    reference_latitude = float(np.mean(latitudes))
    km_per_degree_lat = math.pi * EARTH_RADIUS_KM / 180.0
    km_per_degree_lon = km_per_degree_lat * math.cos(math.radians(reference_latitude))

    x = (longitudes - float(np.mean(longitudes))) * km_per_degree_lon
    y = (latitudes - reference_latitude) * km_per_degree_lat
    hours = (timestamps - timestamps.min()) / 3600.0

    return np.column_stack([x, y]), hours


def _spatiotemporal_graph(
    positions_km: np.ndarray, hours: np.ndarray, eps_km: float, eps_hours: float
) -> sparse.csr_matrix:
    """Build the neighbour graph: within `eps_km` AND within `eps_hours`.

    The earlier implementation folded time into a third spatial axis and ran
    plain Euclidean DBSCAN. That is subtly wrong, and wrong in a way that bites:
    it makes the two thresholds trade off against each other. Two detections
    2 km apart with a 10 h gap were rejected even though each offset was well
    inside its own limit, because the *combined* distance exceeded eps. It also
    means tuning eps_km silently retightens the effective time window.

    Building the graph explicitly gives each parameter its plain meaning, and
    still runs in O(n log n): the spatial pass uses a KD-tree, and the temporal
    mask is a vectorised filter over the resulting sparse edges.
    """
    graph = radius_neighbors_graph(
        positions_km, radius=eps_km, mode="distance", include_self=True
    ).tocoo()

    within_time = np.abs(hours[graph.row] - hours[graph.col]) <= eps_hours

    # Distances of exactly zero (co-located detections, and every self-edge)
    # would be dropped as structural zeros by the sparse format, which would
    # silently remove real neighbours. Floor them to a negligible positive value.
    data = np.maximum(graph.data[within_time], 1e-9)

    n = positions_km.shape[0]
    masked = sparse.csr_matrix(
        (data, (graph.row[within_time], graph.col[within_time])), shape=(n, n)
    )
    # sklearn wants precomputed sparse input sorted by row value, and warns loudly
    # on every call if it is not.
    return sort_graph_by_row_values(masked, warn_when_not_sorted=False)


# --------------------------------------------------------------------------
# Building incidents
# --------------------------------------------------------------------------


def stable_incident_id(first: Detection) -> str:
    """A deterministic id for the fire this detection started.

    Incidents are *derived* objects — rebuilt from scratch every ingest cycle —
    so a random UUID gives them a new identity every 15 minutes. That quietly
    breaks anything holding a reference: the UI would 404 on the dossier of the
    incident the user had open, permanently, as soon as the picture refreshed.

    Deriving the id from the fire's origin instead keeps it stable across
    rebuilds. The earliest detection is the right anchor because, unlike the
    centroid or the perimeter, it does not move as the fire grows.

    Caveat: FIRMS occasionally backfills detections earlier than ones already
    seen, which would shift the anchor and mint a new id. Rare, and far better
    than re-minting every id on every cycle.
    """
    key = f"{first.latitude:.3f}:{first.longitude:.3f}:{_as_utc(first.observed_at).isoformat()}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def build_incident(
    detections: list[Detection],
    name: str | None = None,
    now: datetime | None = None,
) -> Incident:
    """Summarise a cluster of detections into an Incident."""
    if not detections:
        raise ValueError("cannot build an incident from zero detections")

    now = now or datetime.now(UTC)
    ordered = sorted(detections, key=lambda d: _as_utc(d.observed_at))
    first_seen = _as_utc(ordered[0].observed_at)
    last_seen = _as_utc(ordered[-1].observed_at)

    latitude, longitude = _frp_weighted_centroid(ordered)
    area_ha = estimate_area_ha(ordered)
    frps = [d.frp_mw for d in ordered if d.frp_mw is not None]

    duration_hours = max((last_seen - first_seen).total_seconds() / 3600.0, 1.0)
    growth_rate = area_ha / duration_hours

    incident = Incident(
        id=stable_incident_id(ordered[0]),
        name=name or _provisional_name(latitude, longitude, first_seen),
        status=derive_status(last_seen, now),
        latitude=latitude,
        longitude=longitude,
        first_detected_at=first_seen,
        last_detected_at=last_seen,
        detection_count=len(ordered),
        total_frp_mw=round(sum(frps), 1),
        max_frp_mw=round(max(frps), 1) if frps else 0.0,
        estimated_area_ha=round(area_ha, 1),
        growth_rate_ha_per_hour=round(growth_rate, 2),
    )
    incident.severity = score_severity(incident)
    return incident


def _frp_weighted_centroid(detections: list[Detection]) -> tuple[float, float]:
    """Where the fire *is*, weighted by how hard each pixel is burning.

    A plain mean drags the marker toward the long cold tail of a fire's scar.
    Weighting by Fire Radiative Power puts the marker on the active front, which
    is the part anyone is making decisions about.
    """
    weights = np.array([max(d.frp_mw or 0.0, 0.1) for d in detections], dtype=np.float64)
    latitudes = np.array([d.latitude for d in detections], dtype=np.float64)
    longitudes = np.array([d.longitude for d in detections], dtype=np.float64)
    total = weights.sum()
    return (
        round(float(np.dot(latitudes, weights) / total), 6),
        round(float(np.dot(longitudes, weights) / total), 6),
    )


def estimate_area_ha(detections: list[Detection]) -> float:
    """Estimate burned area by unioning pixel footprints on a grid.

    Two tempting approaches are worse:

    * *Summing pixel areas* double-counts, because consecutive satellite passes
      re-detect the same ground.
    * *Convex hull* over-counts badly for the L- and horseshoe-shaped burns that
      terrain and wind actually produce — often by a factor of two or more.

    So we snap every detection to a grid at the sensor's own resolution and count
    distinct occupied cells. That is a lower bound (fire between passes is
    invisible) and we say so rather than pretending to a precision we do not have.
    """
    if not detections:
        return 0.0

    occupied: set[tuple[int, int]] = set()
    total_cell_area_km2 = 0.0

    for detection in detections:
        # VIIRS is 375 m at nadir, MODIS 1 km; FIRMS reports the actual, larger
        # off-nadir footprint per pixel, so prefer it when present.
        cell_km = max(detection.scan_km or 0.375, 0.375)
        degrees_lat = cell_km / 111.32
        degrees_lon = degrees_lat / max(math.cos(math.radians(detection.latitude)), 0.01)

        cell = (int(detection.latitude / degrees_lat), int(detection.longitude / degrees_lon))
        if cell not in occupied:
            occupied.add(cell)
            total_cell_area_km2 += cell_km * cell_km

    return total_cell_area_km2 * 100.0  # km² -> hectares


def build_perimeter(incident_id: str, detections: list[Detection]) -> Perimeter | None:
    """A convex hull around the detections, buffered by half a pixel.

    Explicitly labelled `detection_hull` and given a low confidence: this is a
    *bounding sketch*, not a surveyed perimeter. Real perimeters come from
    Sentinel-2 burn-scar delineation in the recovery module. Showing the two with
    the same styling would be a lie the map tells the user.
    """
    from shapely.geometry import MultiPoint, mapping

    if len(detections) < 3:
        return None

    points = MultiPoint([(d.longitude, d.latitude) for d in detections])
    buffer_degrees = 0.375 / 2 / 111.32
    hull = points.convex_hull.buffer(buffer_degrees)
    if hull.is_empty:
        return None

    return Perimeter(
        incident_id=incident_id,
        observed_at=max(_as_utc(d.observed_at) for d in detections),
        geometry=mapping(hull),
        area_ha=round(estimate_area_ha(detections), 1),
        method="detection_hull",
        confidence=0.45,
    )


def derive_status(last_detected_at: datetime, now: datetime | None = None) -> IncidentStatus:
    """Infer status from detection recency.

    This is deliberately conservative: satellites go quiet for cloud, for
    smoke, and simply for lack of an overpass. We downgrade slowly and never
    claim a fire is out on the basis of one missed pass.
    """
    now = now or datetime.now(UTC)
    quiet_hours = (now - _as_utc(last_detected_at)).total_seconds() / 3600.0

    if quiet_hours >= QUIET_HOURS_UNTIL_OUT:
        return IncidentStatus.OUT
    if quiet_hours >= QUIET_HOURS_UNTIL_CONTAINED:
        return IncidentStatus.CONTAINED
    return IncidentStatus.ACTIVE


def score_severity(incident: Incident) -> IncidentSeverity:
    """Blend size, intensity and growth into one operational severity class.

    Deliberately simple and inspectable rather than a learned model: an analyst
    can look at this and tell you exactly why a fire was flagged CRITICAL, which
    matters more than a couple of points of accuracy when someone has to justify
    an evacuation order.

    Exposure (people and assets in the path) is folded in separately by
    `services.exposure`, which can escalate but never de-escalate this score.
    """
    # Scales are calibrated to Mediterranean fire sizes: a 500 ha fire is a bad
    # day in Greece, not a catastrophe. Anchoring "full marks" at 500 ha (as the
    # Canadian defaults would) saturates the score and makes every summer fire
    # read CRITICAL, which is the same as having no severity scale at all.
    area_points = min(incident.estimated_area_ha / 5_000.0, 1.0) * 40
    intensity_points = min(incident.max_frp_mw / 1_000.0, 1.0) * 30
    growth_points = min(incident.growth_rate_ha_per_hour / 120.0, 1.0) * 30
    score = area_points + intensity_points + growth_points

    if score >= 70:
        return IncidentSeverity.CRITICAL
    if score >= 50:
        return IncidentSeverity.MAJOR
    if score >= 30:
        return IncidentSeverity.MODERATE
    if score >= 12:
        return IncidentSeverity.MINOR
    return IncidentSeverity.INFORMATIONAL


def _provisional_name(latitude: float, longitude: float, started: datetime) -> str:
    """Name a fire after the nearest known place, the way responders talk.

    Falls back to a grid reference when we have nothing closer, which is honest:
    a made-up place name is worse than a coordinate.
    """
    from amonhen.services.gazetteer import nearest_place

    place = nearest_place(latitude, longitude)
    if place is not None:
        return f"{place.name} · {started.strftime('%d %b')}"
    return f"Unnamed {latitude:.3f}N {longitude:.3f}E · {started.strftime('%d %b')}"


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
