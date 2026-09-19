"""Tests for the orchestration decisions in `services.operations`.

Two things here are worth pinning. Handing a measured burned area to an incident
has to move everything derived from area with it, or the dossier shows one
figure and scores the fire on another. And deciding *not* to spend a satellite
read has to say which reason applied, because the reasons imply completely
different things about whether waiting will help.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from amonhen.domain.entities import Detection, Incident
from amonhen.services.burn_scar import BurnScar
from amonhen.services.operations import (
    DEFAULT_DAY_RANGE,
    OperationalPicture,
    OperationsService,
    RefreshTooSoon,
    _apply_measured_area,
)
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


# --------------------------------------------------------------------------
# The Refresh button must not quietly ask for less than the automatic rebuild
# --------------------------------------------------------------------------


def test_refresh_asks_for_the_same_window_as_the_automatic_rebuild():
    """These two defaults live in different files and must agree.

    When they did not — rebuild 5 days, refresh 3 — pressing Refresh dropped two
    days of detections and took the live picture from 20 incidents to 11. Fires
    vanished from the map because the operator asked for fresher data, which is
    the exact opposite of what the button promises.
    """
    import inspect

    from amonhen.api.routes.incidents import refresh

    rebuild_default = inspect.signature(OperationsService.rebuild).parameters["day_range"].default
    refresh_default = inspect.signature(refresh).parameters["day_range"].default.default

    assert rebuild_default == refresh_default == DEFAULT_DAY_RANGE


def test_the_day_range_stays_inside_what_firms_accepts():
    """FIRMS caps the area endpoint at 5 days and answers a larger request with
    the plain text "Invalid day range" under an HTTP 200 — which the connector
    reads as an empty product, silently losing every detection."""
    assert 1 <= DEFAULT_DAY_RANGE <= 5


# --------------------------------------------------------------------------
# The Refresh button is a lever anyone on the internet can pull
# --------------------------------------------------------------------------


def empty_picture() -> OperationalPicture:
    return OperationalPicture(generated_at=datetime.now(UTC))


async def stub_rebuild(**_) -> OperationalPicture:
    """Stands in for the real chain, which would go to NASA."""
    return empty_picture()


def test_the_first_forced_refresh_is_allowed():
    assert OperationsService().seconds_until_refresh_allowed() == 0.0


async def test_a_second_forced_refresh_is_refused_and_says_how_long_to_wait(monkeypatch):
    """Each forced refresh bypasses every source cache, so it costs a full round
    of upstream requests against the operator's NASA key."""
    service = OperationsService()
    monkeypatch.setattr(service, "rebuild", stub_rebuild)

    await service.force_refresh()

    with pytest.raises(RefreshTooSoon) as caught:
        await service.force_refresh()
    assert 0 < caught.value.retry_after_seconds <= 60


async def test_the_cooldown_starts_before_the_rebuild_not_after(monkeypatch):
    """A rebuild takes seconds to tens of seconds. Stamping the time afterwards
    would let everything that arrives meanwhile through at once — which is the
    stampede the limit exists to stop, not a detail of where a line goes."""
    service = OperationsService()
    rebuilds = 0

    async def slow_rebuild(**_) -> OperationalPicture:
        nonlocal rebuilds
        rebuilds += 1
        await asyncio.sleep(0.05)
        return empty_picture()

    monkeypatch.setattr(service, "rebuild", slow_rebuild)

    outcomes = await asyncio.gather(
        service.force_refresh(), service.force_refresh(), return_exceptions=True
    )

    assert rebuilds == 1, "the second request started a second rebuild"
    assert sum(isinstance(o, RefreshTooSoon) for o in outcomes) == 1


async def test_the_limit_can_be_turned_off(monkeypatch):
    """Zero means no cooldown — for an operator running this on their own
    machine, where the only person pressing the button is them."""
    from amonhen.core.config import settings

    monkeypatch.setattr(settings, "refresh_min_interval_seconds", 0)
    service = OperationsService()
    monkeypatch.setattr(service, "rebuild", stub_rebuild)

    await service.force_refresh()
    await service.force_refresh()  # must not raise


async def test_the_scheduled_rebuild_is_never_throttled(monkeypatch):
    """The limit protects the quota from visitors, not from our own ingest. If
    the 15-minute cycle went through the cooldown, a visitor pressing Refresh
    could stop the platform ingesting."""
    from amonhen.ingest import scheduler
    from amonhen.services.operations import operations

    monkeypatch.setattr(operations, "_picture", None)
    monkeypatch.setattr(operations, "_last_forced_refresh", None)
    monkeypatch.setattr(operations, "rebuild", stub_rebuild)

    await scheduler.refresh_picture()
    await scheduler.refresh_picture()

    assert operations.seconds_until_refresh_allowed() == 0.0


async def test_the_endpoint_answers_429_with_a_retry_after_header(monkeypatch):
    """What a client actually sees. 429 and Retry-After are the standard way to
    say "later"; the interface turns them into one plain sentence."""
    from fastapi import HTTPException

    from amonhen.api.routes.incidents import refresh
    from amonhen.services.operations import operations

    monkeypatch.setattr(operations, "_picture", None)
    monkeypatch.setattr(operations, "_last_forced_refresh", None)
    monkeypatch.setattr(operations, "rebuild", stub_rebuild)

    first = await refresh(day_range=DEFAULT_DAY_RANGE)
    assert first.total_incidents == 0

    with pytest.raises(HTTPException) as caught:
        await refresh(day_range=DEFAULT_DAY_RANGE)
    assert caught.value.status_code == 429
    assert int(caught.value.headers["Retry-After"]) >= 1
