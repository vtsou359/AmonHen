"""Tests for burned-area mapping from Sentinel-2 dNBR.

The severity thresholds and the confidence rules are what an operator ends up
reading, so they are pinned here rather than left to drift. The raster path
itself is exercised against live imagery, not mocked: a fake COG would test the
mock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from amonhen.domain.enums import BurnSeverity
from amonhen.services.burn_scar import (
    BURNED_DNBR,
    SEVERITY_BREAKS,
    BurnScar,
    BurnScarUnavailable,
    _confidence,
    _from_cache,
    _note,
    _to_cache,
    _upper_bound,
    to_perimeter,
)
from amonhen.sources.sentinel2 import Scene


def scar(**overrides) -> BurnScar:
    defaults = dict(
        incident_id="abc123",
        burned_area_ha=412.5,
        mean_dnbr=0.48,
        mean_rdnbr=0.71,
        severity_ha={"low": 90.0, "moderate_high": 250.0, "high": 72.5},
        geometry={"type": "Polygon", "coordinates": [[[23.7, 38.0], [23.8, 38.0], [23.8, 38.1], [23.7, 38.0]]]},
        pre_scene_id="S2A_pre",
        post_scene_id="S2B_post",
        pre_image_date=datetime(2025, 8, 12, tzinfo=UTC),
        post_image_date=datetime(2025, 8, 27, tzinfo=UTC),
        usable_fraction=0.92,
        confidence=0.83,
        note="Measured from a satellite image taken on 27 Aug 2025.",
    )
    defaults.update(overrides)
    return BurnScar(**defaults)  # type: ignore[arg-type]


def image(days_ago: int, cloud: float = 5.0) -> Scene:
    return Scene(
        id=f"S2_{days_ago}",
        observed_at=datetime(2025, 8, 27, tzinfo=UTC) - timedelta(days=days_ago),
        cloud_cover_pct=cloud,
        grid_code="MGRS-34SGH",
    )


# --------------------------------------------------------------------------
# Severity classification
# --------------------------------------------------------------------------


def test_severity_breaks_are_ordered_high_to_low():
    """`_upper_bound` walks this table, so the ordering is load-bearing."""
    bounds = [bound for bound, _ in SEVERITY_BREAKS]
    assert bounds == sorted(bounds, reverse=True)


def test_every_break_maps_to_a_real_burn_severity_class():
    for _, severity in SEVERITY_BREAKS:
        assert isinstance(severity, BurnSeverity)


def test_the_classes_tile_the_range_without_gap_or_overlap():
    """A dNBR falling in two classes would be double-counted in the hectare
    breakdown; one falling in none would silently vanish from it."""
    for value in (0.10, 0.2, 0.27, 0.4, 0.44, 0.6, 0.66, 1.2, 3.0):
        matched = [
            severity
            for lower, severity in SEVERITY_BREAKS
            if lower <= value < _upper_bound(lower)
        ]
        assert len(matched) == 1, f"dNBR {value} matched {matched}"


def test_the_top_class_is_unbounded():
    assert _upper_bound(0.66) == float("inf")


def test_the_burn_threshold_is_the_bottom_of_the_lowest_class():
    """If these drift apart, pixels count as burned for the area but fall out of
    the severity breakdown, and the two figures stop adding up."""
    assert BURNED_DNBR == min(bound for bound, _ in SEVERITY_BREAKS)


def test_dominant_severity_is_the_class_with_the_most_ground():
    assert scar().dominant_severity == "moderate_high"


def test_dominant_severity_of_an_empty_breakdown_is_unburned():
    assert scar(severity_ha={}).dominant_severity == BurnSeverity.UNBURNED.value


# --------------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------------


def test_cloud_in_the_window_lowers_confidence():
    clear = _confidence(0.98, image(10), image(0), still_burning=False)
    murky = _confidence(0.55, image(10), image(0), still_burning=False)
    assert clear > murky


def test_a_stale_before_image_lowers_confidence():
    """The longer the gap, the more of the measured change is seasonal drying
    rather than fire."""
    recent = _confidence(0.95, image(10), image(0), still_burning=False)
    stale = _confidence(0.95, image(60), image(0), still_burning=False)
    assert recent > stale


def test_a_fire_still_burning_lowers_confidence():
    final = _confidence(0.95, image(10), image(0), still_burning=False)
    partial = _confidence(0.95, image(10), image(0), still_burning=True)
    assert final > partial


def test_confidence_stays_inside_zero_to_one():
    for usable in (0.0, 0.4, 1.0):
        value = _confidence(usable, image(90), image(0), still_burning=True)
        assert 0.0 < value <= 1.0


# --------------------------------------------------------------------------
# What the operator is told
# --------------------------------------------------------------------------


def test_a_partial_measurement_says_so():
    """The area of a fire that was still burning is not its final size, and a
    figure presented as final would be read as one."""
    note = _note(image(0), still_burning=True)
    assert note.startswith("Still burning")
    assert scar(note=note).is_partial


def test_a_finished_measurement_names_the_image_date():
    note = _note(image(0), still_burning=False)
    assert "27 Aug 2025" in note
    assert not scar(note=note).is_partial


# --------------------------------------------------------------------------
# Handing the result to the rest of the platform
# --------------------------------------------------------------------------


def test_a_measured_scar_becomes_a_perimeter_the_map_can_tell_apart():
    """`method` is what stops the map drawing a 20 m measurement with the same
    styling as a 375 m sketch."""
    perimeter = to_perimeter(scar())
    assert perimeter is not None
    assert perimeter.method == "sentinel2_dnbr"
    assert perimeter.area_ha == 412.5
    assert perimeter.confidence == 0.83


def test_a_scar_with_no_geometry_yields_no_perimeter():
    assert to_perimeter(scar(geometry=None)) is None


def test_a_measured_perimeter_outranks_a_detection_hull():
    """The hull is pinned at 0.45 in `clustering.build_perimeter`. Any measured
    scar must come back more trustworthy than that, or the UI would rank a
    sketch above a measurement."""
    assert to_perimeter(scar()).confidence > 0.45  # type: ignore[union-attr]


# --------------------------------------------------------------------------
# Caching
# --------------------------------------------------------------------------


def test_a_scar_survives_a_cache_round_trip():
    """Dates go through JSON as strings and come back needing parsing; getting
    that wrong would crash the dossier rather than degrade it."""
    restored = _from_cache(_to_cache(scar()))
    assert isinstance(restored, BurnScar)
    assert restored.burned_area_ha == 412.5
    assert restored.post_image_date == datetime(2025, 8, 27, tzinfo=UTC)
    assert restored.dominant_severity == "moderate_high"


def test_an_unavailable_reason_survives_a_cache_round_trip():
    """The reason is cached too. Re-running a doomed satellite search every
    fifteen minutes to rediscover that it is cloudy helps nobody."""
    restored = _from_cache(
        _to_cache(BurnScarUnavailable(reason="Too much cloud", detail="41% visible"))
    )
    assert isinstance(restored, BurnScarUnavailable)
    assert restored.reason == "Too much cloud"
    assert restored.transient is True


@pytest.mark.parametrize("transient", [True, False])
def test_unavailability_records_whether_waiting_will_help(transient):
    """"The satellite has not passed yet" and "this is switched off" look
    identical as an absence, and an operator needs to tell them apart."""
    reason = BurnScarUnavailable(reason="x", transient=transient)
    assert _from_cache(_to_cache(reason)).transient is transient  # type: ignore[union-attr]
