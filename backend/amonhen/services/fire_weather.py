"""The Canadian Forest Fire Weather Index (FWI) System.

Implemented from Van Wagner (1987), *Development and structure of the Canadian
Forest Fire Weather Index System*, Forestry Technical Report 35 — the same
system Copernicus EFFIS runs operationally across Europe.

The system is six numbers built on each other:

    FFMC  fine fuel moisture   — will a spark catch?          (hours to respond)
    DMC   duff moisture        — will it get into the litter?  (days)
    DC    drought code         — how deep is the drought?      (weeks/months)
      ISI = f(FFMC, wind)      — how fast will it run?
      BUI = f(DMC, DC)         — how much fuel is available?
        FWI = f(ISI, BUI)      — overall potential intensity

Each of the three moisture codes is a *memory*: today's value depends on
yesterday's. That is the whole point — a single hot afternoon does not make a
dangerous forest, six dry weeks do. So you cannot compute a meaningful FWI from
one weather reading; you must march it forward over a warm-up period, which is
what `run_series` does.

All inputs are noon-local standard-time values, per the original specification:
temperature (°C), relative humidity (%), 10 m wind (km/h), 24 h rain (mm).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from amonhen.domain.enums import DangerClass

# Effective day-length factors, by month index 0-11.
# The canonical tables are derived for ~46°N. Greece sits near 38°N, where days
# are shorter in summer and longer in winter; EFFIS applies a latitude
# adjustment. `_day_length` below implements Lawson & Armitage's adjustment so
# the indices are not silently biased for the Mediterranean.
_DMC_DAY_LENGTH_46N = [6.5, 7.5, 9.0, 12.8, 13.9, 13.9, 12.4, 10.9, 9.4, 8.0, 7.0, 6.0]
_DMC_DAY_LENGTH_20N = [7.9, 8.4, 8.9, 9.5, 9.9, 10.2, 10.1, 9.7, 9.1, 8.6, 8.1, 7.8]
_DC_DAY_LENGTH_46N = [-1.6, -1.6, -1.6, 0.9, 3.8, 5.8, 6.4, 5.0, 2.4, 0.4, -1.6, -1.6]
_DC_DAY_LENGTH_20N = [6.4] * 12

# Starting values for a fresh season, per the standard: fuels are assumed
# moderately dry after snowmelt / the end of the wet season.
DEFAULT_FFMC = 85.0
DEFAULT_DMC = 6.0
DEFAULT_DC = 15.0


@dataclass(frozen=True)
class FwiState:
    """The three moisture codes that carry forward from day to day."""

    ffmc: float = DEFAULT_FFMC
    dmc: float = DEFAULT_DMC
    dc: float = DEFAULT_DC


@dataclass(frozen=True)
class FwiResult:
    ffmc: float
    dmc: float
    dc: float
    isi: float
    bui: float
    fwi: float
    danger_class: DangerClass

    @property
    def state(self) -> FwiState:
        return FwiState(ffmc=self.ffmc, dmc=self.dmc, dc=self.dc)


# --------------------------------------------------------------------------
# The six components
# --------------------------------------------------------------------------


def fine_fuel_moisture_code(
    previous: float, temperature_c: float, humidity_pct: float, wind_kmh: float, rain_mm: float
) -> float:
    """FFMC — moisture of cured fine fuels (litter, grass) as a 0-101 code.

    High means dry means ignitable. Responds within hours, which is why it, not
    DC, is what changes between a safe morning and a dangerous afternoon.
    """
    humidity_pct = min(humidity_pct, 100.0)
    # Convert the code back to an actual moisture content (% oven-dry weight).
    mo = 147.2 * (101.0 - previous) / (59.5 + previous)

    # --- rainfall phase: wet the fuel, with a cap and a canopy-interception loss
    if rain_mm > 0.5:
        rf = rain_mm - 0.5
        mo += 42.5 * rf * math.exp(-100.0 / (251.0 - mo)) * (1.0 - math.exp(-6.93 / rf))
        if mo > 150.0:
            # Above 150% the fuel sheds water rather than absorbing it.
            mo += 0.0015 * (mo - 150.0) ** 2 * math.sqrt(rf)
        mo = min(mo, 250.0)

    # --- drying/wetting phase toward the equilibrium moisture content
    ed = (  # equilibrium for desorption (fuel drying out)
        0.942 * humidity_pct**0.679
        + 11.0 * math.exp((humidity_pct - 100.0) / 10.0)
        + 0.18 * (21.1 - temperature_c) * (1.0 - math.exp(-0.115 * humidity_pct))
    )

    if mo > ed:
        ko = 0.424 * (1.0 - (humidity_pct / 100.0) ** 1.7) + 0.0694 * math.sqrt(wind_kmh) * (
            1.0 - (humidity_pct / 100.0) ** 8
        )
        kd = ko * 0.581 * math.exp(0.0365 * temperature_c)
        m = ed + (mo - ed) * 10.0**-kd
    else:
        ew = (  # equilibrium for absorption (fuel taking moisture back up)
            0.618 * humidity_pct**0.753
            + 10.0 * math.exp((humidity_pct - 100.0) / 10.0)
            + 0.18 * (21.1 - temperature_c) * (1.0 - math.exp(-0.115 * humidity_pct))
        )
        if mo < ew:
            kl = 0.424 * (1.0 - ((100.0 - humidity_pct) / 100.0) ** 1.7) + 0.0694 * math.sqrt(
                wind_kmh
            ) * (1.0 - ((100.0 - humidity_pct) / 100.0) ** 8)
            kw = kl * 0.581 * math.exp(0.0365 * temperature_c)
            m = ew - (ew - mo) * 10.0**-kw
        else:
            m = mo  # already at equilibrium

    return _clamp(59.5 * (250.0 - m) / (147.2 + m), 0.0, 101.0)


def duff_moisture_code(
    previous: float,
    temperature_c: float,
    humidity_pct: float,
    rain_mm: float,
    month: int,
    latitude: float,
) -> float:
    """DMC — moisture of loosely-compacted organic layers. Responds over days."""
    humidity_pct = min(humidity_pct, 100.0)

    if rain_mm > 1.5:
        re = 0.92 * rain_mm - 1.27           # rain that actually reaches the duff
        mo = 20.0 + math.exp(5.6348 - previous / 43.43)
        if previous <= 33.0:
            b = 100.0 / (0.5 + 0.3 * previous)
        elif previous <= 65.0:
            b = 14.0 - 1.3 * math.log(previous)
        else:
            b = 6.2 * math.log(previous) - 17.2
        mr = mo + 1000.0 * re / (48.77 + b * re)
        # log() is undefined at or below 20% moisture; the code floors at 0 there.
        previous = max(0.0, 244.72 - 43.43 * math.log(mr - 20.0)) if mr > 20.0 else 0.0

    temperature_c = max(temperature_c, -1.1)
    day_length = _day_length(_DMC_DAY_LENGTH_46N, _DMC_DAY_LENGTH_20N, month, latitude)
    drying = 1.894 * (temperature_c + 1.1) * (100.0 - humidity_pct) * day_length * 1e-6
    return max(0.0, previous + 100.0 * drying)


def drought_code(
    previous: float, temperature_c: float, rain_mm: float, month: int, latitude: float
) -> float:
    """DC — deep, compact organic matter. The seasonal drought memory.

    Slow: a time lag of about 53 days. In a Greek summer this is the number that
    quietly separates "a fire" from "a fire that cannot be stopped".
    """
    if rain_mm > 2.8:
        rd = 0.83 * rain_mm - 1.27
        qo = 800.0 * math.exp(-previous / 400.0)   # current moisture equivalent
        qr = qo + 3.937 * rd
        previous = max(0.0, 400.0 * math.log(800.0 / qr)) if qr > 0 else 0.0

    temperature_c = max(temperature_c, -2.8)
    day_length = _day_length(_DC_DAY_LENGTH_46N, _DC_DAY_LENGTH_20N, month, latitude)
    evapotranspiration = max(0.0, 0.36 * (temperature_c + 2.8) + day_length)
    return max(0.0, previous + 0.5 * evapotranspiration)


def initial_spread_index(ffmc: float, wind_kmh: float) -> float:
    """ISI — expected rate of forward spread, before accounting for fuel load."""
    m = 147.2 * (101.0 - ffmc) / (59.5 + ffmc)
    wind_effect = math.exp(0.05039 * wind_kmh)
    fuel_moisture_effect = 91.9 * math.exp(-0.1386 * m) * (1.0 + m**5.31 / 4.93e7)
    return 0.208 * wind_effect * fuel_moisture_effect


def buildup_index(dmc: float, dc: float) -> float:
    """BUI — total fuel available to the flaming front."""
    if dmc <= 0.0 and dc <= 0.0:
        return 0.0
    if dmc <= 0.4 * dc:
        bui = 0.8 * dmc * dc / (dmc + 0.4 * dc) if (dmc + 0.4 * dc) > 0 else 0.0
    else:
        denominator = dmc + 0.4 * dc
        correction = 1.0 - (0.8 * dc / denominator if denominator > 0 else 0.0)
        bui = dmc - correction * (0.92 + (0.0114 * dmc) ** 1.7)
    return max(0.0, bui)


def fire_weather_index(isi: float, bui: float) -> float:
    """FWI — the headline number: potential frontal fire intensity."""
    duff_function = (
        0.626 * bui**0.809 + 2.0 if bui <= 80.0 else 1000.0 / (25.0 + 108.64 * math.exp(-0.023 * bui))
    )
    intermediate = 0.1 * isi * duff_function
    if intermediate <= 1.0:
        return max(0.0, intermediate)
    return math.exp(2.72 * (0.434 * math.log(intermediate)) ** 0.647)


def classify_danger(fwi: float) -> DangerClass:
    """EFFIS six-class fire danger thresholds, as used across Europe."""
    if fwi < 5.2:
        return DangerClass.VERY_LOW
    if fwi < 11.2:
        return DangerClass.LOW
    if fwi < 21.3:
        return DangerClass.MODERATE
    if fwi < 38.0:
        return DangerClass.HIGH
    if fwi < 50.0:
        return DangerClass.VERY_HIGH
    return DangerClass.EXTREME


# --------------------------------------------------------------------------
# Driving the system
# --------------------------------------------------------------------------


def step(
    state: FwiState,
    temperature_c: float,
    humidity_pct: float,
    wind_kmh: float,
    rain_mm: float,
    month: int,
    latitude: float = 38.0,
) -> FwiResult:
    """Advance the FWI system by one day from `state`."""
    ffmc = fine_fuel_moisture_code(state.ffmc, temperature_c, humidity_pct, wind_kmh, rain_mm)
    dmc = duff_moisture_code(state.dmc, temperature_c, humidity_pct, rain_mm, month, latitude)
    dc = drought_code(state.dc, temperature_c, rain_mm, month, latitude)

    isi = initial_spread_index(ffmc, wind_kmh)
    bui = buildup_index(dmc, dc)
    fwi = fire_weather_index(isi, bui)

    return FwiResult(
        ffmc=round(ffmc, 2),
        dmc=round(dmc, 2),
        dc=round(dc, 2),
        isi=round(isi, 2),
        bui=round(bui, 2),
        fwi=round(fwi, 2),
        danger_class=classify_danger(fwi),
    )


def run_series(
    daily_weather: list[dict[str, float]],
    latitude: float = 38.0,
    initial: FwiState | None = None,
) -> list[FwiResult]:
    """March the system across a sequence of daily observations.

    Each item needs: `temperature_c`, `humidity_pct`, `wind_kmh`, `rain_mm`,
    `month` (1-12). Feed it at least 2-3 weeks of history before the day you
    care about, or DC will still be reflecting its arbitrary start value rather
    than the real drought.
    """
    state = initial or FwiState()
    results: list[FwiResult] = []
    for day in daily_weather:
        result = step(
            state,
            temperature_c=day["temperature_c"],
            humidity_pct=day["humidity_pct"],
            wind_kmh=day["wind_kmh"],
            rain_mm=day.get("rain_mm", 0.0),
            month=int(day["month"]),
            latitude=latitude,
        )
        results.append(result)
        state = result.state
    return results


def _day_length(table_46n: list[float], table_20n: list[float], month: int, latitude: float) -> float:
    """Interpolate the day-length factor between the 46°N and 20°N tables.

    Greece (~35-41°N) falls between the two published tables, so we interpolate
    rather than pretending it is Canada.
    """
    index = _clamp(month - 1, 0, 11)
    high, low = table_46n[int(index)], table_20n[int(index)]
    weight = _clamp((abs(latitude) - 20.0) / 26.0, 0.0, 1.0)
    return low + (high - low) * weight


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
