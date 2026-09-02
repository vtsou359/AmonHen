"""Tests for area-of-interest filtering.

The stakes here are asymmetric. Letting a Turkish fire through is a nuisance;
dropping a Greek one is a safety failure. These tests are weighted accordingly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from amonhen.core.config import settings
from amonhen.domain.entities import Detection
from amonhen.services import boundary
from amonhen.services.gazetteer import SEED_PLACES

pytestmark = pytest.mark.skipif(
    not boundary.boundary_path().exists(),
    reason="boundary file not built — run scripts/build_boundary.py",
)

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

# Deep inside neighbouring countries — must never be treated as Greek.
FOREIGN = [
    ("Izmir, TR", 38.420, 27.140),
    ("Tirana, AL", 41.330, 19.820),
    ("Skopje, MK", 41.990, 21.430),
    ("Sofia, BG", 42.700, 23.320),
    ("Bursa, TR", 40.190, 29.060),
    ("Denizli, TR", 37.862, 29.372),
]

# Remote Greek territory a naive polygon loses.
REMOTE_GREEK = [
    ("Kastellorizo", 36.146, 29.594),
    ("Gavdos", 34.833, 24.083),
    ("Othonoi", 39.843, 19.396),
    ("Samos", 37.755, 26.977),
    ("Rhodes", 36.434, 28.218),
    ("Soufli/Evros", 41.193, 26.297),
]


def detection(lat: float, lon: float) -> Detection:
    return Detection(latitude=lat, longitude=lon, observed_at=NOW, frp_mw=25.0)


@pytest.mark.parametrize(("name", "lat", "lon"), REMOTE_GREEK)
def test_remote_greek_territory_is_inside(name, lat, lon):
    assert boundary.contains(lat, lon), f"{name} must be inside the area of interest"


@pytest.mark.parametrize(("name", "lat", "lon"), FOREIGN)
def test_foreign_cities_are_outside(name, lat, lon):
    assert not boundary.contains(lat, lon), f"{name} must not be treated as Greek"


def test_every_gazetteer_place_is_inside():
    """The strongest check available: 51 places we already call Greek.

    Coastal town centroids sit marginally seaward of a simplified coastline —
    Mati, Chalkida and Alexandroupoli all failed this before the boundary grew
    its 3 km coastal buffer.
    """
    outside = [p.name for p in SEED_PLACES if not boundary.contains(p.latitude, p.longitude)]
    assert outside == [], f"gazetteer places excluded by the boundary: {outside}"


def test_filter_keeps_greek_and_drops_foreign():
    greek = [detection(lat, lon) for _, lat, lon in REMOTE_GREEK]
    foreign = [detection(lat, lon) for _, lat, lon in FOREIGN]

    kept, dropped = boundary.filter_detections(greek + foreign)

    assert dropped == len(foreign)
    assert len(kept) == len(greek)
    assert all(boundary.contains(d.latitude, d.longitude) for d in kept)


def test_cross_border_margin_is_applied():
    """Fires do not respect borders; a detection just outside must be retained."""
    if settings.boundary_margin_km <= 0:
        pytest.skip("strict mode: no cross-border margin configured")

    # ~2 km north of the Greek border near Doirani — inside the margin.
    assert boundary.contains(41.148, 22.501)
    # Deep inside North Macedonia — outside it.
    assert not boundary.contains(41.700, 21.700)


def test_filtering_is_a_no_op_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "restrict_to_boundary", False)
    foreign = [detection(lat, lon) for _, lat, lon in FOREIGN]

    kept, dropped = boundary.filter_detections(foreign)

    assert dropped == 0
    assert len(kept) == len(foreign)


def test_empty_input_is_handled():
    kept, dropped = boundary.filter_detections([])
    assert kept == [] and dropped == 0
