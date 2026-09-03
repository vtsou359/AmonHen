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

Every number this module returns should be read as "roughly, on flat ground, in
uniform fuel, if the wind holds". The three assumptions it names are exactly the
three that break in real Greek terrain, so the API reports a confidence band
rather than a single number, and the UI must show it that way.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from amonhen.services.gazetteer import compass_point

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


def estimate_spread(
    isi: float,
    wind_direction_deg: float,
    wind_speed_kmh: float,
    fuel: str = DEFAULT_FUEL,
    slope_pct: float = 0.0,
    live_moisture_factor: float = 1.0,
    live_moisture_pct: float | None = None,
) -> SpreadEstimate:
    """Head/flank/back rate of spread for the given conditions.

    `wind_direction_deg` follows the meteorological convention — the direction
    the wind is coming *from*. Fire spreads the opposite way, which is a
    conversion that has caused real operational mistakes, so it is done once,
    here, and never again anywhere else in the codebase.

    `slope_pct` is the grade *along the direction of spread*, signed: positive
    uphill, negative downhill. Callers get it from `Terrain.slope_toward()`.

    `live_moisture_factor` scales the head rate for how wet the *living*
    vegetation is, measured from Sentinel-2. The FBP equations take dead fuel
    moisture through ISI and model live moisture not at all, so this adds a
    missing effect rather than double-counting an existing one. Callers get it
    from `services.fuel_moisture`; 1.0 means nothing was measured.
    """
    model = FUEL_MODELS.get(fuel, FUEL_MODELS[DEFAULT_FUEL])

    head_ros = model.a * (1.0 - math.exp(-model.b * max(isi, 0.0))) ** model.c
    head_ros *= live_moisture_factor

    # Slope effect (Van Wagner): fire runs uphill roughly exponentially with grade.
    #
    # Now that real terrain is supplied, `slope_pct` can be negative — a fire
    # heading downhill. FBP defines the factor for upslope only, and rather than
    # invent a downslope reduction we apply none: the fire is treated as if on
    # the flat. That over-predicts slightly on descending ground, which is the
    # safe direction to be wrong in for an evacuation tool.
    if slope_pct > 0:
        head_ros *= math.exp(3.533 * min(slope_pct / 100.0, 0.6) ** 1.2)

    length_to_breadth = 1.0 + 8.729 * (1.0 - math.exp(-0.030 * max(wind_speed_kmh, 0.0))) ** 2.155
    length_to_breadth = max(length_to_breadth, 1.0)

    eccentricity = math.sqrt(max(length_to_breadth**2 - 1.0, 0.0)) / length_to_breadth
    back_ros = head_ros * (1.0 - eccentricity) / (1.0 + eccentricity)
    flank_ros = (head_ros + back_ros) / (2.0 * length_to_breadth)

    spread_direction = (wind_direction_deg + 180.0) % 360.0

    caveats = [
        f"Fuel treated as uniform {model.label.lower()} — {model.note}",
        "No terrain data — treated as flat ground, which under-predicts on slopes."
        if slope_pct == 0
        else (
            f"Measured {slope_pct:.0f}% uphill grade along the spread direction."
            if slope_pct > 0
            else f"Heading downhill ({slope_pct:.0f}% grade); treated as flat, so this "
            "estimate is on the high side."
        ),
        "Spotting and crown fire are not modelled; both can outrun this estimate.",
    ]
    if wind_speed_kmh > 40:
        caveats.append("Above ~40 km/h, spread becomes erratic and this model degrades sharply.")
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
        confidence="low" if wind_speed_kmh > 40 or isi > 30 else "moderate",
        caveats=caveats,
    )


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
