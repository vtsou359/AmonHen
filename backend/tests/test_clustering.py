"""Tests for detection clustering and incident construction.

These encode the behaviours that actually matter operationally: two fires must
not be merged, one fire must not be split across satellite passes, and area
estimates must not be inflated by repeated detection of the same ground.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from amonhen.domain.entities import Detection
from amonhen.domain.enums import IncidentStatus
from amonhen.services.clustering import (
    build_incident,
    stable_incident_id,
    build_perimeter,
    cluster_detections,
    derive_status,
    estimate_area_ha,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def detection(lat: float, lon: float, hours_ago: float = 0.0, frp: float = 50.0) -> Detection:
    return Detection(
        latitude=lat,
        longitude=lon,
        observed_at=NOW - timedelta(hours=hours_ago),
        frp_mw=frp,
        scan_km=0.375,
    )


def grid(lat: float, lon: float, n: int, hours_ago: float = 0.0) -> list[Detection]:
    """n detections occupying n *distinct* ~375 m sensor cells.

    Two details matter, and both bit an earlier version of this helper:

    * A degree of longitude is shorter than a degree of latitude away from the
      equator, so the east-west step must be divided by cos(lat). Without that,
      16 "cells" only covered 14 real ones and the area test failed against
      perfectly correct code.
    * Points are placed at cell *centres* rather than on boundaries, so float
      rounding cannot quietly merge two of them.
    """
    d_lat = 0.375 / 111.32
    d_lon = d_lat / math.cos(math.radians(lat))
    base_lat, base_lon = int(lat / d_lat), int(lon / d_lon)
    return [
        detection(
            (base_lat + (i % 4) + 0.5) * d_lat,
            (base_lon + (i // 4) + 0.5) * d_lon,
            hours_ago,
        )
        for i in range(n)
    ]


def test_two_distant_fires_stay_separate():
    """Attica and Rhodes on the same afternoon are two incidents, not one."""
    attica = grid(38.20, 23.94, 8)
    rhodes = grid(36.25, 27.93, 8)

    clusters = cluster_detections(attica + rhodes, min_samples=2)
    fires = [c for label, c in clusters.items() if label >= 0]

    assert len(fires) == 2
    assert {len(c) for c in fires} == {8}


def test_one_fire_across_satellite_passes_stays_one_incident():
    """Detections 10 hours apart on the same ground are the same fire.

    This is the case that plain spatial-only or same-day clustering gets wrong:
    VIIRS revisits every ~6-12 h, so a real fire arrives as separated bursts.
    """
    first_pass = grid(38.20, 23.94, 6, hours_ago=22)
    second_pass = grid(38.21, 23.95, 6, hours_ago=12)

    clusters = cluster_detections(first_pass + second_pass, eps_km=3.0, eps_hours=12.0)
    fires = [c for label, c in clusters.items() if label >= 0]

    assert len(fires) == 1
    assert len(fires[0]) == 12


def test_detections_far_apart_in_time_are_different_incidents():
    """Same ground, ten days apart, is last week's fire and this week's fire."""
    old = grid(38.20, 23.94, 6, hours_ago=240)
    new = grid(38.20, 23.94, 6, hours_ago=1)

    clusters = cluster_detections(old + new, eps_km=3.0, eps_hours=12.0)
    fires = [c for label, c in clusters.items() if label >= 0]

    assert len(fires) == 2


def test_isolated_detection_is_noise_not_a_fire():
    """A single stray pixel must not become an incident with a name and a status."""
    clusters = cluster_detections([*grid(38.2, 23.9, 6), detection(40.5, 21.0)], min_samples=2)

    assert len(clusters.get(-1, [])) == 1
    assert clusters[-1][0].latitude == pytest.approx(40.5)


def test_repeated_detection_of_same_pixel_does_not_inflate_area():
    """Three passes over one burning cell is one cell burnt, not three.

    This is the bug that makes naive area estimates read several times high:
    summing pixel footprints counts the same ground once per overpass.
    """
    cell = [detection(38.20, 23.94, hours_ago=h) for h in (0, 6, 12)]
    single = [detection(38.20, 23.94)]

    assert estimate_area_ha(cell) == pytest.approx(estimate_area_ha(single))


def test_area_scales_with_distinct_cells():
    one = estimate_area_ha(grid(38.2, 23.9, 1))
    sixteen = estimate_area_ha(grid(38.2, 23.9, 16))
    assert sixteen == pytest.approx(one * 16, rel=0.01)


def test_incident_centroid_is_pulled_toward_the_hot_front():
    """FRP weighting must put the marker on the active front, not the cold scar."""
    cold = [detection(38.10, 23.90, frp=2.0) for _ in range(9)]
    hot = [detection(38.30, 23.90, frp=500.0)]

    incident = build_incident(cold + hot, now=NOW)

    plain_mean = (38.10 * 9 + 38.30) / 10
    assert incident.latitude > plain_mean


def test_status_degrades_conservatively_with_satellite_silence():
    assert derive_status(NOW - timedelta(hours=1), NOW) == IncidentStatus.ACTIVE
    assert derive_status(NOW - timedelta(hours=20), NOW) == IncidentStatus.CONTAINED
    assert derive_status(NOW - timedelta(hours=60), NOW) == IncidentStatus.OUT


def test_perimeter_is_labelled_low_confidence():
    """A detection hull must never present itself as a surveyed perimeter."""
    perimeter = build_perimeter("incident-1", grid(38.2, 23.9, 12))

    assert perimeter is not None
    assert perimeter.method == "detection_hull"
    assert perimeter.confidence is not None and perimeter.confidence < 0.6
    assert perimeter.geometry["type"] in ("Polygon", "MultiPolygon")


def test_perimeter_needs_three_points():
    assert build_perimeter("incident-1", grid(38.2, 23.9, 2)) is None


def test_build_incident_rejects_empty_cluster():
    with pytest.raises(ValueError):
        build_incident([])


def test_empty_input_clusters_to_nothing():
    assert cluster_detections([]) == {}


def test_fire_survives_a_full_daily_overpass_gap():
    """Regression: 24 h gaps were fragmenting one fire into one incident per day.

    Measured against live FIRMS data over Greece, consecutive detections of the
    same burning ground cluster at ~24 h — the daily polar-orbit revisit — and
    10.5% of gaps exceeded the old 12 h window. The result was duplicate
    incidents like "38.677N 29.211E · 26 Aug" and "38.675N 29.215E · 27 Aug":
    the same fire, 400 m apart, listed twice.
    """
    day_one = grid(38.20, 23.94, 6, hours_ago=30)
    day_two = grid(38.20, 23.94, 6, hours_ago=6)  # 24 h later, same ground

    clusters = cluster_detections(day_one + day_two)  # settings defaults
    fires = [c for label, c in clusters.items() if label >= 0]

    assert len(fires) == 1, "a 24 h overpass gap must not split one fire"
    assert len(fires[0]) == 12


def test_the_two_thresholds_are_independent():
    """eps_km and eps_hours must not trade off against each other.

    The original implementation folded time into a third spatial axis and used
    Euclidean distance, so a pair 2 km apart with a 10 h gap was rejected under
    a 3 km / 12 h setting — even though each offset was comfortably inside its
    own limit. Tuning one threshold silently retightened the other.
    """
    step = 2.0 / 111.32  # ~2 km apart, well inside eps_km=3
    a = detection(38.20, 23.94, hours_ago=10.0)  # 10 h apart, inside eps_hours=12
    b = detection(38.20 + step, 23.94, hours_ago=0.0)

    clusters = cluster_detections([a, b], eps_km=3.0, eps_hours=12.0, min_samples=2)
    fires = [c for label, c in clusters.items() if label >= 0]

    assert len(fires) == 1, "within 3 km AND within 12 h must mean exactly that"
    assert len(fires[0]) == 2


def test_separate_thresholds_still_reject_out_of_range_pairs():
    """The looser metric must not become a merge-everything metric."""
    too_far = [detection(38.20, 23.94), detection(38.30, 23.94)]  # ~11 km
    too_old = [detection(38.20, 23.94, hours_ago=0), detection(38.20, 23.94, hours_ago=40)]

    for pair in (too_far, too_old):
        clusters = cluster_detections(pair, eps_km=3.0, eps_hours=12.0, min_samples=2)
        assert [c for label, c in clusters.items() if label >= 0] == []


def test_incident_id_is_stable_across_rebuilds():
    """Regression: random ids meant the UI 404'd on whatever fire you had open.

    Incidents are derived objects, rebuilt from scratch every ingest cycle. With
    a random UUID they got a new identity every 15 minutes, so any held
    reference — the open dossier, a bookmark, a link in a report — broke on the
    next refresh and kept 404ing forever.
    """
    detections = grid(38.20, 23.94, 8, hours_ago=6)

    first = build_incident(detections, now=NOW)
    # Same fire, observed again next cycle with two more detections.
    second = build_incident(detections + grid(38.21, 23.95, 2, hours_ago=1), now=NOW)

    assert first.id == second.id
    assert second.detection_count > first.detection_count


def test_different_fires_get_different_ids():
    attica = build_incident(grid(38.20, 23.94, 6), now=NOW)
    rhodes = build_incident(grid(36.25, 27.93, 6), now=NOW)
    assert attica.id != rhodes.id


def test_incident_id_is_derived_from_the_earliest_detection():
    """The origin is the anchor: unlike the centroid, it does not move as the
    fire grows."""
    origin = detection(38.20, 23.94, hours_ago=20)
    later = grid(38.25, 23.99, 5, hours_ago=2)

    assert build_incident([origin, *later], now=NOW).id == stable_incident_id(origin)
