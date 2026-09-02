"""Tests for wildfire plausibility scoring.

The asymmetry matters: wrongly flagging industry is a nuisance, wrongly
dismissing a real fire is a safety failure. So the positive controls here carry
more weight than the negative ones, and the bundled demo scenario — which is
built to look like real Greek fires — must never be rejected.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from amonhen.domain.entities import Detection
from amonhen.domain.enums import Satellite
from amonhen.services.validation import assess

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def detection(
    lat: float, lon: float, hours_ago: float, frp: float, daytime: bool, sat=Satellite.VIIRS_NOAA20
) -> Detection:
    return Detection(
        latitude=lat,
        longitude=lon,
        observed_at=NOW - timedelta(hours=hours_ago),
        frp_mw=frp,
        is_daytime=daytime,
        satellite=sat,
        scan_km=0.375,
    )


def test_industrial_signature_is_rejected():
    """Weak, night-only, never moving, night after night — a flare or a works.

    This is the exact pattern found over the Thessaloniki industrial area: six
    detections inside 300 m, 0.3-1.5 MW, all between midnight and 02:00, across
    four consecutive nights, seen by three different satellites.
    """
    cluster = [
        detection(40.700, 22.952, hours_ago=h, frp=frp, daytime=False)
        for h, frp in [(11, 1.2), (11.5, 0.9), (12, 0.9), (35, 0.3), (36, 0.8), (36.5, 1.0)]
    ]
    result = assess(cluster, now=NOW)

    assert result.verdict == "likely_not_wildfire"
    assert result.is_suspect
    assert any("weak heat" in r.lower() for r in result.reasons)
    assert any("night" in r.lower() for r in result.reasons)


def test_a_real_fire_front_is_accepted():
    """Hot, daytime, spread over kilometres — unambiguous."""
    cluster = [
        detection(38.20 + i * 0.01, 23.94 + i * 0.01, hours_ago=6 - i * 0.5, frp=60 + i * 20, daytime=True)
        for i in range(8)
    ]
    result = assess(cluster, now=NOW)

    assert result.verdict == "wildfire"
    assert result.score >= 0.65
    assert not result.is_suspect


def test_the_bundled_demo_fires_are_all_accepted():
    """Positive control against the shipped scenario.

    If a change to the scoring starts rejecting these, it would reject real
    Greek summer fires too — the fixture is built to imitate them.
    """
    from pathlib import Path

    from amonhen.core.config import settings
    from amonhen.services.clustering import cluster_detections
    from amonhen.sources.firms import FirmsSource

    path = settings.fixtures_dir / "firms_detections.json"
    if not path.exists():
        pytest.skip("demo fixture not present")

    detections = FirmsSource()._load_fixture()
    clusters = cluster_detections(detections)
    fires = [group for label, group in clusters.items() if label >= 0]
    assert fires, "fixture should produce clusters"

    for group in fires:
        result = assess(group)
        assert not result.is_suspect, (
            f"a demo fire was rejected: {result.verdict} {result.score} {result.reasons}"
        )


def test_weak_but_daytime_and_moving_is_not_dismissed():
    """A small real fire must survive.

    Low power alone is not disqualifying — a genuine fire that is small, or seen
    at the edge of a pass, can read weak. It takes weak *and* the behavioural
    signals to reject something.
    """
    cluster = [
        detection(38.20 + i * 0.02, 23.94, hours_ago=5 - i, frp=1.5, daytime=True)
        for i in range(4)
    ]
    result = assess(cluster, now=NOW)
    assert not result.is_suspect


def test_reasons_are_always_given():
    """Nothing may be dismissed without an explanation an operator can argue with."""
    cluster = [detection(40.70, 22.95, hours_ago=h, frp=1.0, daytime=False) for h in (10, 11, 34)]
    result = assess(cluster, now=NOW)
    assert result.reasons
    assert all(r.strip() for r in result.reasons)


def test_signals_are_reported_for_audit():
    cluster = [detection(38.2, 23.9, hours_ago=3, frp=40, daytime=True)]
    result = assess(cluster, now=NOW)
    for key in ("peak_frp_mw", "night_fraction", "distinct_days", "spread_km"):
        assert key in result.signals


def test_empty_cluster_is_handled():
    assert assess([]).verdict == "questionable"


def test_score_stays_in_range():
    for cluster in (
        [detection(40.7, 22.95, h, 0.3, False) for h in (10, 34, 58, 82)],
        [detection(38.2 + i * 0.03, 23.9, 5 - i, 300, True) for i in range(10)],
    ):
        result = assess(cluster, now=NOW)
        assert 0.0 <= result.score <= 1.0
