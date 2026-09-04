"""Tests for live fuel moisture from Sentinel-2 NDMI.

The mapping from index to moisture is first-order and uncalibrated, and it is
allowed to be — what is not allowed is for it to be *unbounded*. A satellite is
watching these fires burn; an index must be able to adjust a spread rate and
never to argue a fire out of existence. Most of what follows pins that.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from amonhen.services.fuel_moisture import (
    MAX_SPREAD_FACTOR,
    MIN_SPREAD_FACTOR,
    MOISTURE_RANGES,
    FuelMoisture,
    live_moisture_from_ndmi,
    spread_factor,
)
from amonhen.services.spread import FUEL_MODELS, estimate_spread

ALL_FUELS = sorted(MOISTURE_RANGES)


def observation(**overrides) -> FuelMoisture:
    defaults = dict(
        ndmi=0.12,
        ndvi=0.31,
        live_moisture_pct=86.0,
        fuel="maquis",
        spread_factor=1.1,
        observed_at=datetime(2025, 8, 25, tzinfo=UTC),
        scene_id="S2B_34SGH_20250825_0_L2A",
        sample_pixels=4200,
        source="Copernicus Sentinel-2 L2A",
    )
    defaults.update(overrides)
    return FuelMoisture(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# The mapping stays inside its own table
# --------------------------------------------------------------------------


def test_every_moisture_range_has_a_matching_fuel_model():
    """A typo here would silently fall through to the default range for a whole
    vegetation class, exactly as it would in the CORINE mapping."""
    for fuel in ALL_FUELS:
        assert fuel in FUEL_MODELS, f"{fuel!r} has a moisture range but no spread model"


@pytest.mark.parametrize("fuel", ALL_FUELS)
def test_wetter_vegetation_always_reads_as_more_moisture(fuel):
    assert live_moisture_from_ndmi(0.30, fuel) > live_moisture_from_ndmi(0.00, fuel)


@pytest.mark.parametrize("fuel", ALL_FUELS)
def test_moisture_is_clamped_to_the_published_range(fuel):
    band = MOISTURE_RANGES[fuel]
    assert live_moisture_from_ndmi(-5.0, fuel) == pytest.approx(band.lfmc_dry)
    assert live_moisture_from_ndmi(5.0, fuel) == pytest.approx(band.lfmc_wet)


@pytest.mark.parametrize("fuel", ALL_FUELS)
def test_the_anchors_are_hit_exactly(fuel):
    band = MOISTURE_RANGES[fuel]
    assert live_moisture_from_ndmi(band.ndmi_dry, fuel) == pytest.approx(band.lfmc_dry)
    assert live_moisture_from_ndmi(band.ndmi_wet, fuel) == pytest.approx(band.lfmc_wet)


def test_an_unknown_fuel_falls_back_rather_than_raising():
    assert live_moisture_from_ndmi(0.2, "not_a_fuel") == pytest.approx(
        live_moisture_from_ndmi(0.2, "maquis")
    )


def test_the_same_reading_means_different_things_in_different_vegetation():
    """This is why the projection re-derives moisture per scenario instead of
    reusing the measured fuel's figure: NDMI 0.10 is dry scrub and an
    unremarkable pine stand."""
    assert live_moisture_from_ndmi(0.10, "phrygana") != pytest.approx(
        live_moisture_from_ndmi(0.10, "pine")
    )


# --------------------------------------------------------------------------
# The spread modifier, and its clamp
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fuel", ALL_FUELS)
def test_no_correction_at_the_reference_moisture(fuel):
    """The reference is where the FBP coefficients behave as published. Applying
    a correction there would double-count what the model already includes."""
    assert spread_factor(MOISTURE_RANGES[fuel].reference, fuel) == pytest.approx(1.0)


@pytest.mark.parametrize("fuel", ALL_FUELS)
def test_drier_than_reference_speeds_the_fire_up_and_wetter_slows_it(fuel):
    band = MOISTURE_RANGES[fuel]
    assert spread_factor(band.reference - 25, fuel) > 1.0
    assert spread_factor(band.reference + 25, fuel) < 1.0


@pytest.mark.parametrize("fuel", ALL_FUELS)
def test_the_modifier_can_never_leave_its_band(fuel):
    """The clamp is the argument, not a safety net. A fire that satellites are
    watching burn is evidently in fuel dry enough to carry it, so an
    uncalibrated index gets to adjust the rate and not to overrule the
    observation."""
    for moisture in (-500.0, 0.0, 50.0, 300.0, 5000.0):
        assert MIN_SPREAD_FACTOR <= spread_factor(moisture, fuel) <= MAX_SPREAD_FACTOR


def test_bone_dry_vegetation_does_not_produce_an_unbounded_fire():
    assert spread_factor(0.0, "phrygana") == MAX_SPREAD_FACTOR


def test_soaking_vegetation_does_not_stop_the_fire_entirely():
    assert spread_factor(400.0, "maquis") == MIN_SPREAD_FACTOR


# --------------------------------------------------------------------------
# What it does to a spread estimate
# --------------------------------------------------------------------------


def test_the_modifier_reaches_the_rate_of_spread():
    baseline = estimate_spread(isi=12.0, wind_direction_deg=0.0, wind_speed_kmh=25.0)
    drier = estimate_spread(
        isi=12.0, wind_direction_deg=0.0, wind_speed_kmh=25.0, live_moisture_factor=1.3
    )
    assert drier.head_ros_m_per_min > baseline.head_ros_m_per_min


def test_the_modifier_also_moves_the_backing_rate():
    """Head, flank and back are all derived from head, so a modifier that only
    moved the head would leave the ellipse internally inconsistent."""
    baseline = estimate_spread(isi=12.0, wind_direction_deg=0.0, wind_speed_kmh=25.0)
    wetter = estimate_spread(
        isi=12.0, wind_direction_deg=0.0, wind_speed_kmh=25.0, live_moisture_factor=0.7
    )
    assert wetter.back_ros_m_per_min < baseline.back_ros_m_per_min
    assert wetter.flank_ros_m_per_min < baseline.flank_ros_m_per_min


def test_an_unmeasured_fire_is_unchanged():
    """The default has to be a no-op, or installing the raster extra would
    silently move every number on every fire it cannot see."""
    plain = estimate_spread(isi=12.0, wind_direction_deg=0.0, wind_speed_kmh=25.0)
    explicit = estimate_spread(
        isi=12.0, wind_direction_deg=0.0, wind_speed_kmh=25.0, live_moisture_factor=1.0
    )
    assert plain.head_ros_m_per_min == explicit.head_ros_m_per_min
    assert not any("Sentinel-2" in caveat for caveat in plain.caveats)


def test_a_measured_fire_says_so_in_its_caveats():
    estimate = estimate_spread(
        isi=12.0,
        wind_direction_deg=0.0,
        wind_speed_kmh=25.0,
        live_moisture_factor=1.25,
        live_moisture_pct=62.0,
    )
    caveat = next(c for c in estimate.caveats if "Sentinel-2" in c)
    assert "62% moisture" in caveat
    assert "faster" in caveat


# --------------------------------------------------------------------------
# Plain language
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("moisture", "expected"),
    [(45.0, "tinder dry"), (75.0, "dry"), (100.0, "normal for the season"), (160.0, "green")],
)
def test_moisture_is_described_in_words(moisture, expected):
    assert observation(live_moisture_pct=moisture).descriptor == expected


@pytest.mark.parametrize(
    ("ndvi", "expected"),
    [(0.10, "bare or cured"), (0.28, "sparse"), (0.45, "vegetated"), (0.70, "lush")],
)
def test_greenness_separates_stubble_from_a_standing_crop(ndvi, expected):
    """The land cover map says 'farmland' either way. This is what tells them
    apart, and the difference decides whether the ground carries fire at all."""
    assert observation(ndvi=ndvi).greenness == expected


def test_a_negligible_correction_is_not_worth_showing():
    assert not observation(spread_factor=1.02).is_significant
    assert observation(spread_factor=1.18).is_significant


# --------------------------------------------------------------------------
# The calibration, pinned against what Greek vegetation actually reads
# --------------------------------------------------------------------------

#: NDMI measured over eight Greek sites in April, June and September 2026 — the
#: observations the anchors in MOISTURE_RANGES were set from.
OBSERVED_NDMI: list[tuple[str, str, float]] = [
    ("Attica maquis Apr", "maquis", 0.108),
    ("Attica maquis Sep", "maquis", -0.013),
    ("Rhodes maquis Apr", "maquis", 0.150),
    ("Rhodes maquis Sep", "maquis", 0.041),
    ("Crete phrygana Apr", "phrygana", 0.004),
    ("Crete phrygana Sep", "phrygana", -0.069),
    ("Mani phrygana Apr", "phrygana", 0.240),
    ("Mani phrygana Sep", "phrygana", 0.133),
    ("Parnitha pine Apr", "pine", 0.184),
    ("Parnitha pine Sep", "pine", 0.144),
    ("Chalkidiki pine Sep", "pine", 0.169),
    ("Pindus forest Sep", "mixed_forest", 0.276),
    ("Thessaly farm Jun", "agricultural", -0.107),
    ("Thessaly farm Sep", "agricultural", -0.087),
]


@pytest.mark.parametrize(("site", "fuel", "ndmi"), OBSERVED_NDMI)
def test_real_readings_do_not_pin_the_modifier_at_its_clamp(site, fuel, ndmi):
    """A modifier stuck at its limit carries no information.

    The first, guessed version of the table did exactly that at three of four
    September sites — peak fire season, the one time of year this most needs to
    discriminate. These are the readings the anchors were recalibrated against.
    """
    factor = spread_factor(live_moisture_from_ndmi(ndmi, fuel), fuel)
    assert MIN_SPREAD_FACTOR < factor < MAX_SPREAD_FACTOR, f"{site} pinned at {factor}"


def test_september_reads_drier_than_april_at_the_same_site():
    """The seasonal signal has to survive the mapping, or none of this is
    measuring anything."""
    for fuel, april, september in (
        ("maquis", 0.108, -0.013),
        ("phrygana", 0.240, 0.133),
        ("pine", 0.184, 0.144),
    ):
        assert live_moisture_from_ndmi(september, fuel) < live_moisture_from_ndmi(april, fuel)
        assert spread_factor(live_moisture_from_ndmi(september, fuel), fuel) > spread_factor(
            live_moisture_from_ndmi(april, fuel), fuel
        )


def test_a_harvested_field_reads_far_drier_than_an_irrigated_one():
    """Thessaly, the same ground in April and June. This is the distinction the
    land cover map cannot make and the `agricultural` fuel model depends on."""
    irrigated = live_moisture_from_ndmi(0.342, "agricultural")
    harvested = live_moisture_from_ndmi(-0.107, "agricultural")
    assert irrigated > 150 and harvested < 40


def test_vegetation_above_its_extinction_moisture_is_flagged_not_acted_on():
    """A fire burning in fuel this green is running on dead fuel, wind or slope.
    That is worth telling the operator and not worth modelling away."""
    wet = observation(fuel="maquis", live_moisture_pct=155.0)
    assert wet.above_extinction
    assert spread_factor(155.0, "maquis") >= MIN_SPREAD_FACTOR
    assert not observation(fuel="maquis", live_moisture_pct=80.0).above_extinction
