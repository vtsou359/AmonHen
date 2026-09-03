"""Tests for the ensemble projection.

The ensemble exists to express uncertainty honestly, so most of these check that
the *relationships between scenarios* are physically sensible — a set of
projections that disagree in the wrong direction is worse than one number.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from amonhen.domain.entities import ExposedElement, Incident, WeatherObservation
from amonhen.domain.enums import ExposureKind
from amonhen.services.fuel_moisture import FuelMoisture, live_moisture_from_ndmi
from amonhen.services.projection import SCENARIOS, project
from amonhen.sources.elevation import Terrain
from amonhen.sources.landcover import LandCover

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def incident() -> Incident:
    return Incident(
        name="Test fire",
        latitude=38.10,
        longitude=23.80,
        first_detected_at=NOW,
        last_detected_at=NOW,
        estimated_area_ha=200.0,
    )


def weather(wind_from_deg: float = 0.0) -> WeatherObservation:
    """A northerly on a hot dry day, with FFMC present so ISI can be recomputed."""
    return WeatherObservation(
        latitude=38.10,
        longitude=23.80,
        observed_at=NOW,
        temperature_c=35.0,
        relative_humidity_pct=20.0,
        wind_speed_kmh=25.0,
        wind_direction_deg=wind_from_deg,
        ffmc=92.0,
        isi=14.0,
    )


def by_id(result, scenario_id):
    return next(p for p in result.projections if p.scenario.id == scenario_id)


def test_no_weather_means_no_projection():
    """A projection without wind is not cautious, it is invented."""
    assert project(incident(), None) is None


def test_wind_perturbation_flows_through_isi():
    """Regression: gusting must actually spread faster than easing.

    In the FBP system wind is an input to ISI, and head rate of spread depends on
    ISI alone. The first implementation scaled wind but reused the original ISI,
    so all three wind scenarios had identical forward speed and differed only in
    ellipse shape — which made "easing" cover *more* ground than "gusting".
    """
    result = project(incident(), weather())
    assert result is not None

    easing = by_id(result, "easing").spread.head_ros_m_per_min
    expected = by_id(result, "expected").spread.head_ros_m_per_min
    gusting = by_id(result, "gusting").spread.head_ros_m_per_min

    assert easing < expected < gusting


def test_fuel_order_is_sensible():
    result = project(incident(), weather())
    expected = by_id(result, "expected").spread.head_ros_m_per_min

    assert by_id(result, "heavy_fuel").spread.head_ros_m_per_min < expected
    assert by_id(result, "light_fuel").spread.head_ros_m_per_min > expected
    # The worst case has to actually be the worst.
    assert by_id(result, "worst_case").spread.head_ros_m_per_min >= max(
        p.spread.head_ros_m_per_min for p in result.projections if p.scenario.id != "worst_case"
    )


def hillside(slope_pct: float = 30.0, aspect_deg: float = 0.0) -> Terrain:
    """Terrain rising toward `aspect_deg`."""
    return Terrain(
        latitude=38.10,
        longitude=23.80,
        elevation_m=600.0,
        slope_pct=slope_pct,
        aspect_deg=aspect_deg,
        relief_m=300.0,
        sample_count=25,
        source="test",
    )


def test_slope_only_counts_along_the_direction_of_travel():
    """A 30% hill does nothing for a fire running across it."""
    terrain = hillside(slope_pct=30.0, aspect_deg=0.0)  # rises to the north

    assert terrain.slope_toward(0.0) == pytest.approx(30.0)     # straight uphill
    assert terrain.slope_toward(180.0) == pytest.approx(-30.0)  # straight downhill
    assert terrain.slope_toward(90.0) == pytest.approx(0.0, abs=1e-9)  # across


def test_uphill_run_is_faster_than_the_same_fire_on_the_flat():
    """A northerly pushes fire south. Make the hill rise south so it climbs."""
    flat = project(incident(), weather(wind_from_deg=0.0))
    uphill = project(
        incident(), weather(wind_from_deg=0.0), terrain=hillside(slope_pct=35.0, aspect_deg=180.0)
    )

    assert (
        by_id(uphill, "expected").spread.head_ros_m_per_min
        > by_id(flat, "expected").spread.head_ros_m_per_min
    )


def test_downhill_is_never_boosted():
    """Heading downhill must not speed the fire up.

    The slope factor is defined for upslope only; we apply none going down
    rather than invent a reduction, which over-predicts slightly — the safe
    direction to be wrong for an evacuation tool.
    """
    flat = project(incident(), weather(wind_from_deg=0.0))
    downhill = project(
        incident(), weather(wind_from_deg=0.0), terrain=hillside(slope_pct=35.0, aspect_deg=0.0)
    )

    assert by_id(downhill, "expected").spread.head_ros_m_per_min == pytest.approx(
        by_id(flat, "expected").spread.head_ros_m_per_min
    )


def test_terrain_driven_scenario_climbs_instead_of_following_wind():
    """On steep ground in light wind a fire follows the hill.

    This is the scenario that can point somewhere the forecast never would, so
    it is worth pinning: the wind blows from the north (fire would run south),
    but the hill rises east, so this case must head east.
    """
    result = project(
        incident(), weather(wind_from_deg=0.0), terrain=hillside(slope_pct=30.0, aspect_deg=90.0)
    )

    assert by_id(result, "expected").spread.direction_deg == pytest.approx(180.0)
    assert by_id(result, "terrain_driven").spread.direction_deg == pytest.approx(90.0)


def test_terrain_driven_falls_back_to_wind_on_flat_ground():
    """With nothing to climb it must not invent a direction."""
    result = project(
        incident(), weather(wind_from_deg=0.0), terrain=hillside(slope_pct=1.0, aspect_deg=90.0)
    )
    assert by_id(result, "terrain_driven").spread.direction_deg == pytest.approx(180.0)


def test_caveats_say_whether_terrain_was_measured():
    without = project(incident(), weather())
    with_terrain = project(incident(), weather(), terrain=hillside())

    assert any("treated as flat" in c for c in without.caveats)
    assert any("Copernicus" in c for c in with_terrain.caveats)
    assert with_terrain.terrain is not None


def test_wind_veer_scenarios_point_different_ways():
    """A northerly pushes fire south; the veer cases must straddle that."""
    result = project(incident(), weather(wind_from_deg=0.0))

    centre = by_id(result, "expected").spread.direction_deg
    left = by_id(result, "veer_left").spread.direction_deg
    right = by_id(result, "veer_right").spread.direction_deg

    assert centre == pytest.approx(180.0)
    assert left == pytest.approx(150.0)
    assert right == pytest.approx(210.0)


def test_envelope_grows_with_horizon():
    result = project(incident(), weather())
    areas = [result.envelope_areas_ha[m] for m in result.horizons_minutes]
    assert areas == sorted(areas)
    assert areas[0] > 0


def test_envelope_contains_every_scenario():
    """The envelope is a union, so nothing may stick out of it."""
    from shapely.geometry import shape

    result = project(incident(), weather())
    horizon = result.horizons_minutes[-1]
    envelope = shape(result.envelope[horizon]).buffer(1e-9)

    for projection in result.projections:
        footprint = shape(projection.footprints[horizon])
        assert envelope.contains(footprint) or envelope.intersection(footprint).area == pytest.approx(
            footprint.area, rel=1e-6
        ), projection.scenario.id


def test_core_is_contained_by_envelope_and_smaller():
    """The core is what every scenario agrees on — necessarily the smaller claim."""
    from shapely.geometry import shape

    result = project(incident(), weather())
    horizon = result.horizons_minutes[-1]
    if horizon not in result.core:
        pytest.skip("scenarios did not overlap at this horizon")

    core = shape(result.core[horizon])
    envelope = shape(result.envelope[horizon])
    assert core.area <= envelope.area
    assert envelope.buffer(1e-9).contains(core.buffer(-1e-9)) or core.area < envelope.area


def test_threat_likelihood_is_a_fraction_of_the_ensemble():
    exposed = [
        ExposedElement(
            name="Downwind village",
            kind=ExposureKind.SETTLEMENT,
            latitude=38.02,   # due south of the fire, i.e. downwind of a northerly
            longitude=23.80,
            population=2000,
            distance_km=8.9,
            bearing_deg=180.0,
            is_downwind=True,
        )
    ]
    result = project(incident(), weather(wind_from_deg=0.0), exposed)

    assert result.threats, "a village 9 km straight downwind should be reached"
    threat = result.threats[0]
    assert 0.0 < threat.likelihood <= 1.0
    assert threat.hit_count <= threat.scenario_count == len(SCENARIOS)
    assert threat.earliest_minutes is not None
    assert threat.median_minutes >= threat.earliest_minutes


def test_upwind_places_are_not_reported_as_threatened():
    exposed = [
        ExposedElement(
            name="Upwind village",
            kind=ExposureKind.SETTLEMENT,
            latitude=38.30,   # north — the fire is moving away from it
            longitude=23.80,
            distance_km=22.0,
            bearing_deg=0.0,
            is_downwind=False,
        )
    ]
    result = project(incident(), weather(wind_from_deg=0.0), exposed)
    assert result.threats == []


def test_every_scenario_states_its_rationale():
    """Each scenario must justify its presence — the ensemble is meant to be argued with."""
    for scenario in SCENARIOS:
        assert scenario.rationale.strip()
        assert scenario.label.strip()


def test_projection_carries_its_caveats():
    result = project(incident(), weather())
    assert result.caveats
    assert any("could reach" in c for c in result.caveats)


# --------------------------------------------------------------------------
# Live fuel moisture through the ensemble
# --------------------------------------------------------------------------


def moisture(ndmi: float = -0.01, fuel: str = "maquis") -> FuelMoisture:
    """A Sentinel-2 observation, at a September-typical NDMI for Greek scrub."""
    return FuelMoisture(
        ndmi=ndmi,
        ndvi=0.36,
        live_moisture_pct=live_moisture_from_ndmi(ndmi, fuel),
        fuel=fuel,
        spread_factor=1.0,
        observed_at=NOW,
        scene_id="S2B_test",
        sample_pixels=5000,
        source="Copernicus Sentinel-2 L2A",
    )


def scrub() -> LandCover:
    return LandCover(code="323", label="Shrubland", fuel="maquis", source="test")


def test_an_unmeasured_fire_projects_exactly_as_before():
    """Installing the raster extra must not move fires it cannot see."""
    without = project(incident(), weather(), land_cover=scrub())
    with_none = project(incident(), weather(), land_cover=scrub(), fuel_moisture=None)
    assert [p.spread.head_ros_m_per_min for p in without.projections] == [
        p.spread.head_ros_m_per_min for p in with_none.projections
    ]
    assert all(p.live_moisture_pct is None for p in without.projections)


def test_dry_vegetation_speeds_every_scenario_up():
    baseline = project(incident(), weather(), land_cover=scrub())
    dry = project(
        incident(), weather(), land_cover=scrub(), fuel_moisture=moisture(ndmi=-0.05)
    )
    for before, after in zip(baseline.projections, dry.projections, strict=True):
        assert after.spread.head_ros_m_per_min > before.spread.head_ros_m_per_min


def test_green_vegetation_slows_every_scenario_down():
    baseline = project(incident(), weather(), land_cover=scrub())
    green = project(
        incident(), weather(), land_cover=scrub(), fuel_moisture=moisture(ndmi=0.25)
    )
    for before, after in zip(baseline.projections, green.projections, strict=True):
        assert after.spread.head_ros_m_per_min < before.spread.head_ros_m_per_min


def test_moisture_is_re_read_through_each_scenario_own_fuel():
    """The same NDMI is dangerously dry for phrygana and unremarkable for pine.

    A scenario that assumes different vegetation must re-derive the moisture
    through that vegetation's range, or the fuel-uncertainty cases would carry
    the measured fuel's moisture into ground it never described.
    """
    result = project(
        incident(), weather(), land_cover=scrub(), fuel_moisture=moisture(ndmi=0.15)
    )
    light = by_id(result, "light_fuel")   # forced to phrygana
    heavy = by_id(result, "heavy_fuel")   # forced to pine
    assert light.fuel_used == "phrygana" and heavy.fuel_used == "pine"
    assert light.live_moisture_pct != heavy.live_moisture_pct


def test_the_worst_case_assumes_drier_ground_than_was_measured():
    """Land cover is 100 m data from 2018 and the moisture mapping is
    uncalibrated, so the worst case has to stress the observation, not trust it."""
    result = project(
        incident(), weather(), land_cover=scrub(), fuel_moisture=moisture(ndmi=0.10)
    )
    worst = by_id(result, "worst_case")
    light = by_id(result, "light_fuel")   # same fuel, unstressed
    assert worst.live_moisture_pct < light.live_moisture_pct


def test_a_measured_fire_says_so_in_its_caveats():
    result = project(
        incident(), weather(), land_cover=scrub(), fuel_moisture=moisture()
    )
    assert any("Sentinel-2" in caveat for caveat in result.caveats)
    assert result.fuel_moisture is not None
