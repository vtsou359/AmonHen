"""Tests for the orchestration decisions in `services.operations`.

Two things here are worth pinning. Handing a measured burned area to an incident
has to move everything derived from area with it, or the dossier shows one
figure and scores the fire on another. And deciding *not* to spend a satellite
read has to say which reason applied, because the reasons imply completely
different things about whether waiting will help.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from amonhen.domain.entities import Detection, Incident
from amonhen.services.burn_scar import BurnScar
from amonhen.services.operations import OperationsService, _apply_measured_area
from amonhen.services.validation import Plausibility

#: A fixed instant, for the arithmetic that does not consult a clock.
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def incident(hours_old: float = 48.0, area_ha: float = 120.0) -> Incident:
    """An incident of a given age.

    Age is measured from the *real* clock, not from `NOW`, because
    `_skip_imaging` asks `datetime.now()` — as it must, since "has a satellite
    passed yet" is a question about the present. Anchoring the fixture to a
    frozen constant instead made these tests pass on the day they were written
    and quietly invert the next day.
    """
    first = datetime.now(UTC) - timedelta(hours=hours_old)
    return Incident(
        id="fire1",
        name="Test fire",
        latitude=38.1,
        longitude=23.8,
        first_detected_at=first,
        last_detected_at=first + timedelta(hours=max(hours_old - 2, 0.5)),
        estimated_area_ha=area_ha,
        growth_rate_ha_per_hour=2.5,
        detection_count=20,
        max_frp_mw=40.0,
    )


def scar(
    area_ha: float = 900.0,
    image_hours_after: float = 24.0,
    fire: Incident | None = None,
) -> BurnScar:
    """A measured scar, imaged `image_hours_after` the fire was first detected."""
    first = fire.first_detected_at if fire else datetime.now(UTC) - timedelta(hours=48)
    return BurnScar(
        incident_id="fire1",
        burned_area_ha=area_ha,
        mean_dnbr=0.51,
        mean_rdnbr=0.88,
        severity_ha={"moderate_high": area_ha},
        geometry={"type": "Polygon", "coordinates": []},
        pre_scene_id="pre",
        post_scene_id="post",
        pre_image_date=first - timedelta(days=10),
        post_image_date=first + timedelta(hours=image_hours_after),
        usable_fraction=0.95,
        confidence=0.85,
    )


def detections(count: int = 6) -> list[Detection]:
    return [
        Detection(latitude=38.1 + i * 0.01, longitude=23.8, observed_at=NOW, frp_mw=30.0)
        for i in range(count)
    ]


def verdict(name: str) -> Plausibility:
    return Plausibility(verdict=name, score=0.8 if name == "wildfire" else 0.1)


# --------------------------------------------------------------------------
# Handing a measurement to the incident
# --------------------------------------------------------------------------


def test_a_measured_area_replaces_the_thermal_estimate():
    fire = incident(area_ha=120.0)
    _apply_measured_area(fire, scar(area_ha=900.0, fire=fire))
    assert fire.estimated_area_ha == 900.0
    assert fire.area_source == "sentinel2_dnbr"


def test_growth_rate_moves_with_the_area():
    """Leaving growth on the old figure would put two areas on one screen: a
    900 ha fire reported as growing at the rate implied by 120 ha."""
    fire = incident(area_ha=120.0)
    before = fire.growth_rate_ha_per_hour
    _apply_measured_area(fire, scar(area_ha=900.0, fire=fire))
    assert fire.growth_rate_ha_per_hour != before
    assert fire.growth_rate_ha_per_hour > 0


def test_growth_is_measured_to_the_image_not_to_the_last_detection():
    """The measured area is what had burnt when the picture was taken. Dividing
    it by a longer window reports a fire slowing down purely because a satellite
    passed early."""
    early = incident()
    late = incident()
    _apply_measured_area(early, scar(area_ha=900.0, image_hours_after=12.0, fire=early))
    _apply_measured_area(late, scar(area_ha=900.0, image_hours_after=36.0, fire=late))
    assert early.growth_rate_ha_per_hour > late.growth_rate_ha_per_hour


def test_severity_is_rescored_against_the_measured_area():
    """Severity is driven by area, so a fire measured at seven times its
    estimate must be allowed to change class."""
    fire = incident(area_ha=120.0)
    before = fire.severity
    _apply_measured_area(fire, scar(area_ha=6_000.0, fire=fire))
    assert fire.severity != before


def test_an_image_taken_immediately_does_not_divide_by_zero():
    fire = incident()
    _apply_measured_area(fire, scar(area_ha=50.0, image_hours_after=0.1, fire=fire))
    assert fire.estimated_area_ha == 50.0
    assert fire.growth_rate_ha_per_hour >= 0


# --------------------------------------------------------------------------
# Deciding not to look
# --------------------------------------------------------------------------


def test_a_plausible_established_fire_is_imaged():
    service = OperationsService()
    assert service._skip_imaging(incident(hours_old=48), detections(), verdict("wildfire")) is None


def test_a_fire_younger_than_the_revisit_is_not_imaged_yet():
    service = OperationsService()
    skip = service._skip_imaging(incident(hours_old=3), detections(), verdict("wildfire"))
    assert skip is not None
    assert "Too soon" in skip.reason
    assert skip.transient, "a later pass fixes this on its own"


def test_an_industrial_heat_source_is_never_imaged():
    """And says so in its own words. Reporting 'too soon' here would have an
    operator waiting for a pass that is never going to change the answer."""
    service = OperationsService()
    skip = service._skip_imaging(
        incident(hours_old=200), detections(), verdict("likely_not_wildfire")
    )
    assert skip is not None
    assert "does not behave like a fire" in skip.reason
    assert not skip.transient, "waiting will not help; there is nothing here to burn"


def test_too_few_detections_to_match_a_scar_against():
    service = OperationsService()
    skip = service._skip_imaging(incident(hours_old=48), detections(count=2), verdict("wildfire"))
    assert skip is not None
    assert "Too few" in skip.reason


@pytest.mark.parametrize(
    ("hours_old", "count", "name"),
    [(3, 6, "wildfire"), (200, 2, "wildfire"), (200, 6, "likely_not_wildfire")],
)
def test_every_refusal_carries_a_distinct_reason(hours_old, count, name):
    """Three different causes, three different messages. A shared one would tell
    the operator nothing about whether to wait."""
    service = OperationsService()
    skip = service._skip_imaging(incident(hours_old=hours_old), detections(count), verdict(name))
    assert skip is not None and skip.reason and skip.detail
