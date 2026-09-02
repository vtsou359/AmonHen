"""Validation of the FWI implementation against published reference values.

The reference case is the worked example from Van Wagner (1987) Tech Report 35,
also used as the regression fixture in the Canadian `cffdrs` package: starting
from the standard season-start state, one day at 17 °C / 42 % RH / 25 km/h wind
/ no rain in April at 46 °N.
"""

from __future__ import annotations

import math

import pytest

from amonhen.domain.enums import DangerClass
from amonhen.services.fire_weather import (
    FwiState,
    buildup_index,
    classify_danger,
    fine_fuel_moisture_code,
    run_series,
    step,
)

REFERENCE_INPUT = dict(temperature_c=17.0, humidity_pct=42.0, wind_kmh=25.0, rain_mm=0.0)
REFERENCE_EXPECTED = {
    "ffmc": 87.69,
    "dmc": 8.55,
    "dc": 19.01,
    "isi": 10.85,
    "bui": 8.49,
    "fwi": 10.10,
}


def test_reference_day_matches_van_wagner():
    result = step(FwiState(85.0, 6.0, 15.0), month=4, latitude=46.0, **REFERENCE_INPUT)
    for code, expected in REFERENCE_EXPECTED.items():
        assert getattr(result, code) == pytest.approx(expected, abs=0.01), code


def test_fwi_chain_is_internally_consistent():
    """FWI must be reproducible from ISI and BUI alone — it is a pure function."""
    from amonhen.services.fire_weather import fire_weather_index

    result = step(FwiState(85.0, 6.0, 15.0), month=4, latitude=46.0, **REFERENCE_INPUT)
    assert fire_weather_index(result.isi, result.bui) == pytest.approx(result.fwi, abs=0.02)


def test_rain_reduces_fine_fuel_moisture_code():
    dry = fine_fuel_moisture_code(85.0, 30.0, 20.0, 20.0, rain_mm=0.0)
    wet = fine_fuel_moisture_code(85.0, 30.0, 20.0, 20.0, rain_mm=15.0)
    assert wet < dry, "rain must lower FFMC (wetter fine fuels)"


def test_drought_code_accumulates_over_a_dry_spell():
    """DC is the seasonal memory: 30 dry Greek summer days must build it up a lot."""
    hot_dry_day = dict(temperature_c=35.0, humidity_pct=20.0, wind_kmh=20.0, rain_mm=0.0, month=7)
    series = run_series([hot_dry_day] * 30, latitude=38.0)
    assert series[-1].dc > series[0].dc * 5
    assert series[-1].fwi > series[0].fwi


def test_codes_survive_degenerate_inputs():
    """Real feeds send 100 % humidity, 0 wind and torrential rain. No NaNs, no crashes."""
    result = step(
        FwiState(0.0, 0.0, 0.0),
        temperature_c=-5.0,
        humidity_pct=100.0,
        wind_kmh=0.0,
        rain_mm=120.0,
        month=1,
        latitude=38.0,
    )
    for code in ("ffmc", "dmc", "dc", "isi", "bui", "fwi"):
        value = getattr(result, code)
        assert math.isfinite(value) and value >= 0.0, code


def test_buildup_index_handles_zero_state():
    assert buildup_index(0.0, 0.0) == 0.0


@pytest.mark.parametrize(
    ("fwi", "expected"),
    [
        (0.0, DangerClass.VERY_LOW),
        (8.0, DangerClass.LOW),
        (15.0, DangerClass.MODERATE),
        (30.0, DangerClass.HIGH),
        (45.0, DangerClass.VERY_HIGH),
        (80.0, DangerClass.EXTREME),
    ],
)
def test_effis_danger_thresholds(fwi, expected):
    assert classify_danger(fwi) == expected
