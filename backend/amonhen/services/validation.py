"""Is this actually a wildfire?

Satellites detect *heat*, not fire. A thermal anomaly is equally consistent with
a burning forest, a steel works, a gas flare, a landfill fire, a greenhouse under
lights, or sun glinting off a solar farm. FIRMS makes no attempt to tell them
apart, so an unfiltered incident list quietly mixes wildfires with industry.

The tempting fix is to cross-check against Copernicus EFFIS. It does not work:
**EFFIS active-fire hotspots come from the same MODIS and VIIRS sensors we
already ingest**, so agreement proves only that we both read the same pixel.
Genuinely independent evidence needs different physics (burnt area, i.e.
vegetation actually gone), a different sensor, or ground truth.

Burnt-area products are the honest independent check, but they are weakest
exactly where the noise is: EFFIS maps burnt areas above roughly 30 ha, hours to
days after the fact. A false positive produces no scar — and so does a real fire
that is small or started this morning. Absence of a scar cannot distinguish
those, so burnt area confirms large fires but cannot filter small ones.

What does work, using only data already in hand, is behaviour. Fire behaves in
ways industry does not:

    a wildfire            an industrial heat source
    ---------             -------------------------
    burns hottest         emits steadily, often only detected at night
      mid-afternoon         when the background is cold
    tens-hundreds of MW   a few MW at most
    moves and grows       never moves
    burns out in days     is there again next week

Scoring is transparent arithmetic with named reasons, not a classifier. When
this hides a fire from an operator, somebody has to be able to ask why, and get
an answer better than "the model said so".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from amonhen.core.logging import get_logger
from amonhen.domain.entities import Detection
from amonhen.sources.landcover import LandCover

log = get_logger(__name__)

Verdict = Literal["wildfire", "probable", "questionable", "likely_not_wildfire"]

#: Below this a thermal anomaly is not a spreading fire front. A hedgerow fire
#: registers more. Chosen from the observed noise floor: the persistent
#: industrial sources over Greece sit at 0.3-1.5 MW.
WEAK_FRP_MW = 2.0
#: Above this, argument is over — nothing but fire puts out this much heat over
#: a 375 m pixel.
STRONG_FRP_MW = 25.0

#: A source seen on this many separate days without moving is infrastructure.
PERSISTENT_DAYS = 3
#: "Without moving": everything inside this radius is one fixed point.
STATIC_RADIUS_KM = 1.0


@dataclass
class Plausibility:
    """Whether a cluster of detections behaves like a wildfire."""

    verdict: Verdict
    #: 0-1. How much the behaviour looks like fire, not how large the fire is.
    score: float
    #: Plain-language, shown in the UI. Every point that moved the score.
    reasons: list[str] = field(default_factory=list)
    #: The raw numbers behind the verdict, so it can be audited.
    signals: dict[str, float] = field(default_factory=dict)

    @property
    def is_suspect(self) -> bool:
        return self.verdict in ("questionable", "likely_not_wildfire")


def assess(
    detections: list[Detection],
    now: datetime | None = None,
    land_cover: LandCover | None = None,
) -> Plausibility:
    """Score a cluster on how much it behaves like a real wildfire.

    `land_cover` is the one genuinely *independent* input here — everything else
    is a property of the same satellite pixels. Ground that cannot carry a fire
    (a quarry, a port, open water) is strong evidence the heat is industrial,
    and it is evidence that does not come from the sensor that raised the alarm.
    """
    if not detections:
        return Plausibility(verdict="questionable", score=0.0, reasons=["No detections."])

    now = now or datetime.now(UTC)
    signals = _signals(detections)

    # Start neutral and let the evidence move it. 0.5 means "no idea", which is
    # the honest starting point for a single satellite pixel.
    score = 0.5
    reasons: list[str] = []

    # --- heat output ------------------------------------------------------
    peak = signals["peak_frp_mw"]
    if peak >= STRONG_FRP_MW:
        score += 0.30
        reasons.append(f"Strong heat output ({peak:.0f} MW) — consistent with an open fire.")
    elif peak >= 5.0:
        score += 0.10
    elif peak < WEAK_FRP_MW:
        score -= 0.22
        reasons.append(
            f"Very weak heat ({peak:.1f} MW). A spreading fire front puts out tens of "
            f"megawatts; this is closer to a chimney or a flare."
        )

    # --- time of day ------------------------------------------------------
    # Vegetation fires peak in the afternoon, when fuels are hottest and driest.
    # A source only ever caught at night is usually a steady industrial emitter
    # that the sensor can separate from a cold background.
    if signals["detection_count"] >= 3 and signals["night_fraction"] >= 0.99:
        score -= 0.18
        reasons.append(
            "Only ever detected at night. Wildfires burn hardest in the afternoon, "
            "so this pattern points to a steady heat source rather than a fire."
        )
    elif signals["night_fraction"] <= 0.5:
        score += 0.10

    # --- does it stay put? ------------------------------------------------
    if (
        signals["distinct_days"] >= PERSISTENT_DAYS
        and signals["spread_km"] < STATIC_RADIUS_KM
    ):
        score -= 0.28
        reasons.append(
            f"Same spot on {signals['distinct_days']:.0f} separate days without moving. "
            f"Wildfires spread and burn out; industrial sites do not."
        )
    elif signals["spread_km"] >= 2.0:
        score += 0.20
        reasons.append(
            f"Detections spread over {signals['spread_km']:.1f} km — the footprint is moving, "
            f"which a fixed installation cannot do."
        )

    # --- what is actually on the ground -----------------------------------
    # The only signal here not derived from the same pixels, so it carries
    # weight: a "fire" on a quarry or a dock is a furnace, a flare or a kiln.
    if land_cover is not None and land_cover.code:
        if not land_cover.burnable:
            score -= 0.30
            reasons.append(
                f"The ground here is mapped as {land_cover.descriptor} — there is "
                f"nothing to burn. Heat from this spot is almost certainly industrial."
            )
        else:
            score += 0.08

    # --- corroboration ----------------------------------------------------
    if signals["satellite_count"] >= 2 and signals["detection_count"] >= 6:
        score += 0.05

    score = max(0.0, min(1.0, score))

    if score >= 0.65:
        verdict: Verdict = "wildfire"
    elif score >= 0.45:
        verdict = "probable"
    elif score >= 0.25:
        verdict = "questionable"
    else:
        verdict = "likely_not_wildfire"

    if not reasons:
        reasons.append("Nothing unusual, but too little evidence to be confident either way.")

    return Plausibility(
        verdict=verdict, score=round(score, 3), reasons=reasons, signals=signals
    )


def _signals(detections: list[Detection]) -> dict[str, float]:
    frps = [d.frp_mw for d in detections if d.frp_mw is not None]
    times = [_as_utc(d.observed_at) for d in detections]
    days = {t.date().isoformat() for t in times}
    night = sum(1 for d in detections if d.is_daytime is False)
    known_phase = sum(1 for d in detections if d.is_daytime is not None)

    return {
        "detection_count": float(len(detections)),
        "peak_frp_mw": round(max(frps), 2) if frps else 0.0,
        "median_frp_mw": round(sorted(frps)[len(frps) // 2], 2) if frps else 0.0,
        "night_fraction": round(night / known_phase, 3) if known_phase else 0.0,
        "distinct_days": float(len(days)),
        "span_hours": round((max(times) - min(times)).total_seconds() / 3600.0, 1),
        "spread_km": round(_spread_km(detections), 2),
        "satellite_count": float(len({d.satellite for d in detections})),
    }


def _spread_km(detections: list[Detection]) -> float:
    """Greatest distance between any two detections, approximately.

    Uses the bounding box diagonal rather than an exact pairwise maximum: it is
    O(n) instead of O(n^2), and for deciding "did this thing move at all" the
    difference never matters.
    """
    lats = [d.latitude for d in detections]
    lons = [d.longitude for d in detections]
    mean_lat = sum(lats) / len(lats)

    height_km = (max(lats) - min(lats)) * 111.32
    width_km = (max(lons) - min(lons)) * 111.32 * math.cos(math.radians(mean_lat))
    return math.hypot(height_km, width_km)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
