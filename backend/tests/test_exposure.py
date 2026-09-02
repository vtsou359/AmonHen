"""Tests for threat assessment — the part that drives evacuation advice."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from amonhen.domain.entities import Incident, WeatherObservation
from amonhen.services.exposure import assess_exposure, summarise, threat_score
from amonhen.services.gazetteer import bearing_deg, compass_point, haversine_km, nearest_place
from amonhen.services.spread import estimate_spread, minutes_to_reach

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def incident_near_marathon() -> Incident:
    return Incident(
        name="Test fire",
        latitude=38.20,
        longitude=23.94,
        first_detected_at=NOW,
        last_detected_at=NOW,
        estimated_area_ha=500.0,
    )


def northerly_wind() -> WeatherObservation:
    """A meltemi: blowing FROM the north, so fire runs south."""
    return WeatherObservation(
        latitude=38.20,
        longitude=23.94,
        observed_at=NOW,
        temperature_c=35.0,
        relative_humidity_pct=20.0,
        wind_speed_kmh=30.0,
        wind_direction_deg=0.0,
        isi=15.0,
    )


def test_wind_direction_is_the_direction_it_comes_from():
    """A northerly pushes fire SOUTH. Getting this backwards is a real-world
    incident-report class of error, so it is pinned by a test."""
    spread = estimate_spread(isi=15.0, wind_direction_deg=0.0, wind_speed_kmh=30.0)
    assert spread.direction_deg == pytest.approx(180.0)
    assert spread.direction_label == "S"


def test_downwind_places_rank_above_closer_upwind_ones():
    """Proximity alone is the wrong ranking — trajectory has to dominate."""
    exposed, spread = assess_exposure(incident_near_marathon(), northerly_wind())
    assert spread is not None

    downwind = [e for e in exposed if e.is_downwind]
    assert downwind, "expected something south of the fire"

    # The top-ranked element should be downwind, even if not the nearest.
    assert exposed[0].is_downwind


def test_no_weather_means_no_invented_eta():
    """Without wind we can report distance but must not fabricate arrival times."""
    exposed, spread = assess_exposure(incident_near_marathon(), weather=None)

    assert spread is None
    assert all(e.minutes_to_impact is None for e in exposed)
    assert all(e.distance_km is not None for e in exposed)


def test_spread_is_fastest_at_the_head_and_slowest_at_the_back():
    spread = estimate_spread(isi=15.0, wind_direction_deg=0.0, wind_speed_kmh=30.0)

    assert spread.head_ros_m_per_min > spread.flank_ros_m_per_min > spread.back_ros_m_per_min
    assert spread.ros_toward(180.0) == pytest.approx(spread.head_ros_m_per_min, rel=0.01)
    assert spread.ros_toward(0.0) < spread.head_ros_m_per_min


def test_upwind_target_takes_longer_than_downwind_at_equal_distance():
    spread = estimate_spread(isi=15.0, wind_direction_deg=0.0, wind_speed_kmh=30.0)

    downwind = minutes_to_reach(spread, distance_km=5.0, bearing_to_target_deg=180.0)
    upwind = minutes_to_reach(spread, distance_km=5.0, bearing_to_target_deg=0.0)

    assert downwind is not None and upwind is not None
    assert upwind > downwind * 3


def test_spread_confidence_is_never_overstated():
    """The model is a triage tool; it must never claim high confidence."""
    for wind in (5.0, 25.0, 60.0):
        for isi in (2.0, 20.0, 50.0):
            spread = estimate_spread(isi=isi, wind_direction_deg=180.0, wind_speed_kmh=wind)
            assert spread.confidence in ("low", "moderate")
            assert spread.caveats


def test_life_safety_outranks_forest_at_equal_exposure():
    from amonhen.domain.entities import ExposedElement
    from amonhen.domain.enums import ExposureKind

    common = dict(latitude=38.1, longitude=23.9, distance_km=5.0, bearing_deg=180.0,
                  minutes_to_impact=60.0, is_downwind=True)
    hospital = ExposedElement(name="H", kind=ExposureKind.HOSPITAL, **common)
    forest = ExposedElement(name="F", kind=ExposureKind.FOREST, **common)

    assert threat_score(hospital) > threat_score(forest)


def test_brief_flags_its_own_uncertainty():
    exposed, spread = assess_exposure(incident_near_marathon(), northerly_wind())
    brief = summarise(incident_near_marathon(), exposed, spread)

    assert "rough estimates" in brief
    assert "not a forecast" in brief


def test_brief_avoids_data_model_vocabulary():
    """The brief is read at 3 a.m. by someone who fights fires, not one who
    studies them. None of the enum values should reach the page."""
    exposed, spread = assess_exposure(incident_near_marathon(), northerly_wind())
    brief = summarise(incident_near_marathon(), exposed, spread)

    for jargon in ("informational", "severity", "head spread", "FRP", "SSW", "exposure"):
        assert jargon not in brief, f"{jargon!r} leaked into the brief"


def test_brief_does_not_claim_flat_ground_once_terrain_is_known():
    """Regression: the brief said 'flat-ground estimate' unconditionally, and
    kept saying it after slope became measured — directly above a panel
    reporting the slope."""
    from amonhen.sources.elevation import Terrain

    terrain = Terrain(38.2, 23.94, 700.0, 28.0, 90.0, 250.0, 25, "test")
    exposed, spread = assess_exposure(incident_near_marathon(), northerly_wind())
    brief = summarise(incident_near_marathon(), exposed, spread, terrain=terrain)

    assert "flat" not in brief
    # Assert against the descriptor itself rather than a hard-coded word, so this
    # does not break every time the band thresholds are retuned.
    assert terrain.descriptor in brief


def test_brief_stays_silent_when_terrain_is_unknown():
    exposed, spread = assess_exposure(incident_near_marathon(), northerly_wind())
    brief = summarise(incident_near_marathon(), exposed, spread, terrain=None)

    assert "ground here is" not in brief


def test_brief_never_lists_upwind_places_as_on_trajectory():
    """Regression: the elliptical model returns a finite time for every bearing,
    so an unfiltered brief announced fires arriving at villages they were moving
    away from."""
    incident = incident_near_marathon()
    exposed, spread = assess_exposure(incident, northerly_wind())
    brief = summarise(incident, exposed, spread)

    if "On current trajectory" in brief:
        listed = brief.split("On current trajectory:")[1].split(".")[0]
        for element in exposed:
            if element.name in listed:
                assert element.is_downwind, f"{element.name} is upwind but listed as on trajectory"


def test_gazetteer_geometry():
    athens_to_thessaloniki = haversine_km(37.9838, 23.7275, 40.6401, 22.9444)
    assert 280 < athens_to_thessaloniki < 320          # ~300 km

    assert compass_point(bearing_deg(37.98, 23.73, 40.64, 22.94)) in ("N", "NNW", "NNE")
    assert compass_point(0) == "N"
    assert compass_point(90) == "E"
    assert compass_point(180) == "S"
    assert compass_point(270) == "W"


def test_fire_in_the_middle_of_the_aegean_gets_no_place_name():
    """Better an honest coordinate than a village 90 km away."""
    assert nearest_place(37.0, 25.5, max_km=25.0) is None
