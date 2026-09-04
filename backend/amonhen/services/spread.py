"""First-order fire spread estimation.

What this is: the Canadian Fire Behaviour Prediction (FBP) rate-of-spread
equations, driven by the Initial Spread Index we already compute, wrapped in the
standard elliptical growth model so we can answer "how long until it reaches
that village".

What this is *not*: a fire simulator. FARSITE, FlamMap and Cell2Fire exist,
model terrain and fuel continuity properly, and take minutes to hours to run.
This runs in microseconds and is meant for triage — ranking which of fifteen
simultaneous fires deserves the next aircraft, and giving a defensible order of
magnitude for evacuation lead time.

Every number this module returns should be read as "roughly, in uniform fuel, if
the wind holds". The assumptions it names are exactly the ones that break in real
Greek terrain, so the API reports a confidence band rather than a single number,
and the UI must show it that way.

----------------------------------------------------------------------------
Slope is a wind, not a multiplier
----------------------------------------------------------------------------

The obvious way to add terrain is to multiply the head rate of spread by a slope
factor. It is also wrong, and wrong in a way that is invisible until the wind
drops.

The ellipse's elongation comes from wind alone. At 2 km/h the length-to-breadth
ratio is 1.02 — the fire is a circle — so the head, flank and back rates are all
nearly equal. Multiplying "the head rate" by 5 for a 52% slope therefore
multiplies *every* direction by 5, including straight downhill. On a live
incident that produced a 40 km circle covering 125,000 ha, from a fire whose
measured burn scar was 18 ha.

A hill rises one way. So slope is handled the way the FBP System itself handles
it: converted to the wind speed that would produce the same increase in spread,
then **added to the real wind as a vector**. Three things follow, all of them
right:

* the boost points somewhere, instead of everywhere;
* it elongates the ellipse rather than inflating a circle, because it enters
  through the same term wind does;
* in light wind on steep ground the resultant points uphill, which is what fires
  actually do, and which the model previously needed a hand-written special case
  to express.

The conversion is exact rather than fitted. Wind enters rate of spread only
through ISI, as `exp(0.05039 * W)`, so a slope factor SF is worth
`ln(SF) / 0.05039` km/h of wind. A 52% grade comes out at 32 km/h, which is a
statement about a hillside that a fire officer can argue with.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from amonhen.services.gazetteer import compass_point

#: Wind's coefficient inside the Initial Spread Index, `exp(0.05039 * W)`. It
#: appears here rather than being imported from `fire_weather` because this
#: module inverts it — see `slope_equivalent_wind_kmh`.
ISI_WIND_COEFFICIENT = 0.05039

#: Van Wagner's slope factor saturates at a 60% grade. Above roughly 30-35
#: degrees the flame front attaches to the slope and steepening stops helping,
#: so the cap is physical rather than defensive.
MAX_EFFECTIVE_SLOPE = 0.60

# --------------------------------------------------------------------------
# Fuel models
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FuelModel:
    """FBP-style rate-of-spread coefficients: ROS = a * (1 - exp(-b*ISI))^c.

    Greek fuels have no exact FBP equivalent — the system was built for Canadian
    boreal forest. These are the closest published analogues, and they are the
    single largest source of error in this module. Calibrating them against
    Greek fire records is the obvious first improvement, and the reason the
    coefficients live in a table rather than being inlined.
    """

    key: str
    label: str
    a: float
    b: float
    c: float
    note: str


FUEL_MODELS: dict[str, FuelModel] = {
    "phrygana": FuelModel(
        "phrygana", "Phrygana / garrigue", 250.0, 0.0350, 1.7,
        "Low Mediterranean shrub. Mapped to FBP O-1b (standing grass): fast, wind-driven.",
    ),
    "maquis": FuelModel(
        "maquis", "Maquis / dense shrubland", 190.0, 0.0310, 1.4,
        "Tall evergreen shrub. Between grass and conifer; high fuel load, moderate spread.",
    ),
    "pine": FuelModel(
        "pine", "Aleppo / Calabrian pine", 110.0, 0.0282, 1.5,
        "Mapped to FBP C-2. Crowning and long-range spotting are NOT modelled here.",
    ),
    "mixed_forest": FuelModel(
        "mixed_forest", "Mixed broadleaf forest", 75.0, 0.0297, 1.3,
        "Mapped to FBP S-1. Slower; oak and chestnut hold moisture longer.",
    ),
    "agricultural": FuelModel(
        "agricultural", "Agricultural / stubble", 190.0, 0.0310, 1.4,
        "Highly seasonal — near zero when green, grass-like after harvest.",
    ),
}

DEFAULT_FUEL = "maquis"


@dataclass(frozen=True)
class SpreadEstimate:
    """A first-order forecast of where a fire goes next."""

    head_ros_m_per_min: float
    flank_ros_m_per_min: float
    back_ros_m_per_min: float
    length_to_breadth: float
    direction_deg: float
    direction_label: str
    fuel_model: str
    confidence: str          # "low" | "moderate" — never higher than moderate
    caveats: list[str]

    def ros_toward(self, bearing_deg: float) -> float:
        """Spread rate toward an arbitrary bearing, in m/min.

        Uses the standard elliptical model: fastest at the head, slowest
        directly upwind, smoothly interpolated around.
        """
        eccentricity = math.sqrt(max(self.length_to_breadth**2 - 1.0, 0.0)) / self.length_to_breadth
        theta = math.radians((bearing_deg - self.direction_deg + 180.0) % 360.0 - 180.0)
        denominator = 1.0 - eccentricity * math.cos(theta)
        if denominator <= 1e-9:
            return self.head_ros_m_per_min
        return self.head_ros_m_per_min * (1.0 - eccentricity) / denominator


def slope_factor(slope_pct: float) -> float:
    """Van Wagner's upslope multiplier for a grade, saturating at 60%."""
    if slope_pct <= 0.0:
        return 1.0
    return math.exp(3.533 * min(slope_pct / 100.0, MAX_EFFECTIVE_SLOPE) ** 1.2)


def slope_equivalent_wind_kmh(slope_pct: float) -> float:
    """The wind speed that would push a fire as hard as this hill does.

    Exact, not fitted. Wind reaches rate of spread only through ISI, as
    `exp(0.05039 * W)`, so a slope factor SF is worth `ln(SF) / 0.05039` km/h.
    Inverting it this way is what lets slope be added to wind as a vector
    instead of multiplying a shape that has no direction — see the module
    docstring for why that distinction is the whole point.

    A 52% grade comes out at 32 km/h.
    """
    return math.log(slope_factor(slope_pct)) / ISI_WIND_COEFFICIENT


def _combine_wind_and_slope(
    wind_speed_kmh: float,
    wind_spread_bearing_deg: float,
    slope_pct: float,
    slope_aspect_deg: float | None,
) -> tuple[float, float]:
    """Add the slope's equivalent wind to the real wind, as vectors.

    Returns (effective speed, bearing the fire is pushed toward). With no
    terrain this returns the wind unchanged, so a caller that supplies no slope
    gets exactly the flat-ground answer it always did.

    Note there is no downhill case to special-case any more. The slope vector
    always points uphill; a fire being blown downhill simply has the two vectors
    partly cancel, which is the physics rather than a rule about it.
    """
    east = wind_speed_kmh * math.sin(math.radians(wind_spread_bearing_deg))
    north = wind_speed_kmh * math.cos(math.radians(wind_spread_bearing_deg))

    if slope_aspect_deg is not None and slope_pct > 0.0:
        equivalent = slope_equivalent_wind_kmh(slope_pct)
        east += equivalent * math.sin(math.radians(slope_aspect_deg))
        north += equivalent * math.cos(math.radians(slope_aspect_deg))

    speed = math.hypot(east, north)
    if speed < 1e-6:
        return 0.0, wind_spread_bearing_deg
    return speed, (math.degrees(math.atan2(east, north)) + 360.0) % 360.0


def estimate_spread(
    isi: float,
    wind_direction_deg: float,
    wind_speed_kmh: float,
    fuel: str = DEFAULT_FUEL,
    slope_pct: float = 0.0,
    slope_aspect_deg: float | None = None,
    live_moisture_factor: float = 1.0,
    live_moisture_pct: float | None = None,
) -> SpreadEstimate:
    """Head/flank/back rate of spread for the given conditions.

    `wind_direction_deg` follows the meteorological convention — the direction
    the wind is coming *from*. Fire spreads the opposite way, which is a
    conversion that has caused real operational mistakes, so it is done once,
    here, and never again anywhere else in the codebase.

    `slope_pct` is the **steepest** grade, unsigned, and `slope_aspect_deg` the
    compass bearing of steepest ascent — both straight off `Terrain`. Earlier
    versions took the grade already resolved along the direction of travel,
    which cannot work now that the direction of travel is an *output*: the hill
    helps decide where the fire goes. Pass no aspect and terrain is ignored.

    `live_moisture_factor` scales the head rate for how wet the *living*
    vegetation is, measured from Sentinel-2. The FBP equations take dead fuel
    moisture through ISI and model live moisture not at all, so this adds a
    missing effect rather than double-counting an existing one. Callers get it
    from `services.fuel_moisture`; 1.0 means nothing was measured.
    """
    model = FUEL_MODELS.get(fuel, FUEL_MODELS[DEFAULT_FUEL])

    # Wind and slope combine into one push, with one direction. See the module
    # docstring: treating slope as a multiplier on a shape that has no direction
    # applied it in every direction at once, downhill included.
    wind_spread_bearing = (wind_direction_deg + 180.0) % 360.0
    effective_wind, spread_direction = _combine_wind_and_slope(
        wind_speed_kmh, wind_spread_bearing, slope_pct, slope_aspect_deg
    )

    # ISI is rescaled rather than recomputed, which needs no FFMC and is exact:
    # wind appears in ISI only as exp(0.05039 * W), so the ratio between two
    # wind speeds depends on nothing else.
    effective_isi = max(isi, 0.0) * math.exp(
        ISI_WIND_COEFFICIENT * (effective_wind - wind_speed_kmh)
    )

    head_ros = model.a * (1.0 - math.exp(-model.b * effective_isi)) ** model.c
    head_ros *= live_moisture_factor

    # Elongation follows the *combined* push. This is what stops a steep hill in
    # still air producing a huge circle: the slope's equivalent wind stretches
    # the ellipse exactly as a real wind of that speed would.
    length_to_breadth = 1.0 + 8.729 * (1.0 - math.exp(-0.030 * effective_wind)) ** 2.155
    length_to_breadth = max(length_to_breadth, 1.0)

    eccentricity = math.sqrt(max(length_to_breadth**2 - 1.0, 0.0)) / length_to_breadth
    back_ros = head_ros * (1.0 - eccentricity) / (1.0 + eccentricity)
    flank_ros = (head_ros + back_ros) / (2.0 * length_to_breadth)

    caveats = [
        f"Fuel treated as uniform {model.label.lower()} — {model.note}",
        _slope_caveat(slope_pct, slope_aspect_deg, wind_speed_kmh, effective_wind),
        "Spotting and crown fire are not modelled; both can outrun this estimate.",
    ]
    if effective_wind > 40:
        caveats.append(
            "Above ~40 km/h of combined wind and slope push, spread becomes erratic "
            "and this model degrades sharply."
        )
    if live_moisture_pct is not None:
        direction = "faster" if live_moisture_factor > 1.0 else "slower"
        caveats.append(
            f"Living vegetation measured at {live_moisture_pct:.0f}% moisture from "
            f"Sentinel-2, which makes this {abs(1.0 - live_moisture_factor) * 100:.0f}% "
            f"{direction} than weather alone would suggest."
            if abs(live_moisture_factor - 1.0) >= 0.02
            else f"Living vegetation measured at {live_moisture_pct:.0f}% moisture from "
            f"Sentinel-2 — about normal, so no adjustment was made."
        )

    return SpreadEstimate(
        head_ros_m_per_min=round(head_ros, 1),
        flank_ros_m_per_min=round(flank_ros, 1),
        back_ros_m_per_min=round(back_ros, 1),
        length_to_breadth=round(length_to_breadth, 2),
        direction_deg=round(spread_direction, 1),
        direction_label=compass_point(spread_direction),
        fuel_model=model.key,
        confidence="low" if effective_wind > 40 or effective_isi > 30 else "moderate",
        caveats=caveats,
    )


def _slope_caveat(
    slope_pct: float,
    slope_aspect_deg: float | None,
    wind_speed_kmh: float,
    effective_wind_kmh: float,
) -> str:
    """Say what the terrain did, in the terms it actually did it in."""
    if slope_aspect_deg is None or slope_pct <= 0:
        return "No terrain data — treated as flat ground, which under-predicts on slopes."

    equivalent = slope_equivalent_wind_kmh(slope_pct)
    line = (
        f"A {slope_pct:.0f}% slope pushes the fire uphill about as hard as "
        f"{equivalent:.0f} km/h of wind would."
    )
    # When the hill outweighs the forecast wind the fire goes where the hill
    # says, not where the forecast does, and that is worth stating outright.
    if equivalent > wind_speed_kmh * 2 and effective_wind_kmh > wind_speed_kmh:
        line += " With the wind this light, the slope is what decides the direction."
    return line


def minutes_to_reach(
    estimate: SpreadEstimate, distance_km: float, bearing_to_target_deg: float
) -> float | None:
    """How long until the fire front reaches something `distance_km` away.

    Returns None when the target is upwind and the backing rate is so low the
    answer would be meaningless — better to say "not on this trajectory" than to
    print a confident 40 hours.
    """
    ros_m_per_min = estimate.ros_toward(bearing_to_target_deg)
    if ros_m_per_min < 0.5:
        return None
    return round(distance_km * 1000.0 / ros_m_per_min, 1)


def projected_area_ha(estimate: SpreadEstimate, minutes: float) -> float:
    """Elliptical area the fire could cover in `minutes`, from a point source.

    Area of an ellipse with major axis (head + back) * t and the breadth implied
    by the length-to-breadth ratio.
    """
    length_m = (estimate.head_ros_m_per_min + estimate.back_ros_m_per_min) * minutes
    breadth_m = length_m / max(estimate.length_to_breadth, 1.0)
    return round(math.pi * (length_m / 2.0) * (breadth_m / 2.0) / 10_000.0, 1)
