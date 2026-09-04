"""Live fuel moisture, observed instead of inferred.

The Fire Weather Index system the platform runs on models *dead* fuel moisture:
FFMC for the litter, DMC for the duff, DC for the deep drought. All three are
computed from weather — temperature, humidity, wind, rain — and all three are
inferences. They are good inferences, and they are also the entire moisture
story the model has.

Living vegetation is missing from that story, and in Greece it is most of the
fuel. A maquis hillside at 130% live moisture and the same hillside at 60% burn
completely differently, and no amount of weather history distinguishes them,
because the difference is the plant's root access to water over the season, not
last week's humidity.

Sentinel-2 measures it directly:

    NDMI = (B8A - B11) / (B8A + B11)

B11 sits in a water-absorption feature, so the wetter the leaf, the darker it
is at 1610 nm and the higher the index. This is the one genuinely useful
band-derived input to how fast a fire spreads.

A naming note, because the two are routinely conflated: what is called NDWI in
the wildfire literature is Gao (1996), `(NIR - SWIR) / (NIR + SWIR)` — the same
quantity as NDMI, computed here. McFeeters' NDWI, `(Green - NIR) / (Green +
NIR)`, is an entirely different index for mapping open water and is useless for
this.

----------------------------------------------------------------------------
Where the sample is taken, which decides whether any of this means anything
----------------------------------------------------------------------------

The obvious implementation — average NDMI over the fire — measures the burn
scar. Charred ground has almost no leaf water, so it returns a spectacular
dryness reading that is an artefact of the fire having already happened, and
then feeds it back in as a prediction that the fire will spread fast. A
self-fulfilling measurement of nothing.

So the sample is a **ring around the fire**, excluding the ground the fire has
already touched. That is not a workaround; it is the quantity actually wanted.
What matters for where a fire goes next is the moisture of the fuel it is about
to run into.

----------------------------------------------------------------------------
Honesty about the mapping
----------------------------------------------------------------------------

NDMI to a live fuel moisture percentage is an empirical relationship that varies
by species, season and site. The mapping below is first-order and **not
calibrated against Greek field measurements** — it is anchored on published
Mediterranean ranges per fuel type and interpolated linearly. It is a table for
exactly the same reason the FBP coefficients are a table: so that calibrating it
later is one file, not an archaeology expedition.

The spread modifier it produces is clamped hard, and the clamp encodes a real
argument. A satellite is watching this fire burn right now. Whatever an index
says, the fuel is evidently dry enough to carry fire — so a vegetation index is
allowed to adjust the spread rate, never to argue the fire away.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from amonhen.core.config import settings
from amonhen.core.logging import get_logger
from amonhen.domain.entities import Detection, Incident
from amonhen.sources.base import JsonCache
from amonhen.sources.sentinel2 import (
    EO_AVAILABLE,
    Sentinel2Source,
    padded_bbox,
    read_indices,
)

log = get_logger(__name__)


@dataclass(frozen=True)
class MoistureRange:
    """How one fuel type's live moisture maps onto NDMI, and when it stops burning.

    `ndmi_dry`/`ndmi_wet` are the observable range of the index over this
    vegetation; `lfmc_dry`/`lfmc_wet` the live fuel moisture content, as a
    percentage of oven-dry weight, at those two ends. `reference` is the value
    at which the FBP coefficients behave as published — the point where this
    module applies no correction at all.
    """

    ndmi_dry: float
    ndmi_wet: float
    lfmc_dry: float
    lfmc_wet: float
    #: Above this, the fuel is too green to carry fire on its own. Reported to
    #: the operator but deliberately *not* used to drive the spread multiplier —
    #: see `spread_factor` for why a satellite index never gets to conclude that
    #: a fire we are watching burn cannot burn.
    extinction: float
    #: The moisture at which no correction is applied — the seasonal-normal
    #: reading this vegetation gives, at which the FBP coefficients are taken to
    #: behave as published.
    reference: float


#: Per-fuel anchors.
#:
#: The NDMI ends are **measured**, not assumed: sampled over eight Greek sites
#: in April, June and September 2026, which spans the seasonal swing from wet
#: spring to the peak of fire season. The observed ranges came out as
#:
#:     agricultural   -0.107 .. +0.342     (widest: harvested stubble to irrigated)
#:     phrygana       -0.069 .. +0.240
#:     maquis         -0.013 .. +0.150
#:     mixed_forest   +0.076 .. +0.350
#:     pine           +0.144 .. +0.232     (narrowest: conifer foliage is buffered)
#:
#: which confirms the expected ordering — grass and farmland swing furthest,
#: conifer barely moves, so NDMI tells you much less about a pine stand than
#: about a hillside of scrub. The anchors below widen those slightly so a real
#: reading rarely clamps, and `reference` is set at each fuel's observed median
#: so a typical September reading produces a modest correction rather than
#: pinning the multiplier at its limit, which is what the first, guessed table
#: did at three of four test sites.
#:
#: The LFMC ends remain literature values for Mediterranean fuels and are the
#: part still wanting Greek field calibration.
MOISTURE_RANGES: dict[str, MoistureRange] = {
    "phrygana": MoistureRange(-0.10, 0.30, 30.0, 180.0, 130.0, 93.0),
    "maquis": MoistureRange(-0.05, 0.25, 55.0, 160.0, 140.0, 87.0),
    "pine": MoistureRange(0.10, 0.30, 80.0, 150.0, 150.0, 108.0),
    "mixed_forest": MoistureRange(0.05, 0.40, 75.0, 170.0, 160.0, 130.0),
    "agricultural": MoistureRange(-0.15, 0.40, 15.0, 200.0, 120.0, 45.0),
}

DEFAULT_RANGE = MOISTURE_RANGES["maquis"]

#: The modifier can never leave this band. See the module docstring: the fire is
#: observably burning, so an index must not be able to model it out of existence.
MIN_SPREAD_FACTOR = 0.65
MAX_SPREAD_FACTOR = 1.35

#: How much of the multiplier's band a full swing of this fuel's own moisture
#: range is allowed to use. 0.7 puts a typical seasonal extreme near — but not
#: at — the clamp, which is what keeps the reading informative instead of pinned.
RESPONSE = 0.7

#: Inner edge of the sampling ring: everything closer than this to a detection
#: may already have burnt. Matches the burn-scar seed buffer deliberately.
RING_INNER_KM = 1.5
#: Outer edge. Far enough for a decent sample, near enough to still be the
#: hillside this fire is on rather than the next catchment.
RING_OUTER_KM = 6.0

#: Vegetation moisture moves over weeks, so a slightly older clear scene is far
#: better than a fresh cloudy one. This is how far back we will look.
SEARCH_DAYS = 20

#: Fewer clear pixels than this in the ring and the median is not a measurement.
MIN_SAMPLE_PIXELS = 200

#: Bump when the mapping or the sampling changes, so recalibrating the table
#: takes effect immediately instead of waiting out a day of cached results.
MEASUREMENT_VERSION = 2


@dataclass
class FuelMoisture:
    """How wet the living vegetation around a fire is."""

    #: Normalised Difference Moisture Index, median over the ring.
    ndmi: float
    #: Greenness, median over the ring. Separates cured stubble from a standing
    #: green crop, which the land cover map cannot do.
    ndvi: float
    #: Live fuel moisture content, % of oven-dry weight. First-order — see the
    #: module docstring.
    live_moisture_pct: float
    #: The fuel model this was mapped through.
    fuel: str
    #: Multiplier applied to the head rate of spread. 1.0 means no correction.
    spread_factor: float
    observed_at: datetime
    scene_id: str
    sample_pixels: int
    source: str

    @property
    def descriptor(self) -> str:
        """Plain words. A percentage of oven-dry weight means nothing to most readers."""
        moisture = self.live_moisture_pct
        if moisture < 60:
            return "tinder dry"
        if moisture < 90:
            return "dry"
        if moisture < 120:
            return "normal for the season"
        return "green"

    @property
    def greenness(self) -> str:
        """What the vegetation looks like, from NDVI. Useful mostly for farmland:
        a green irrigated field will not carry fire, harvested stubble will."""
        if self.ndvi < 0.20:
            return "bare or cured"
        if self.ndvi < 0.35:
            return "sparse"
        if self.ndvi < 0.55:
            return "vegetated"
        return "lush"

    @property
    def above_extinction(self) -> bool:
        """Whether the vegetation is too green to carry fire on its own.

        Worth surfacing and worth not acting on. When this is true for a fire
        that is demonstrably burning, the fire is running on dead fuel, wind or
        slope rather than on the living vegetation — which is a real and
        informative situation, not a contradiction to be resolved by adjusting
        the spread rate.
        """
        band = MOISTURE_RANGES.get(self.fuel, DEFAULT_RANGE)
        return self.live_moisture_pct > band.extinction

    @property
    def is_significant(self) -> bool:
        """Whether this changed the answer enough to be worth showing."""
        return abs(self.spread_factor - 1.0) >= 0.05


class FuelMoistureService:
    """Measures live fuel moisture in a ring around a fire."""

    #: Live fuel moisture changes over weeks, and Sentinel-2 revisits in days.
    #: A day of cache costs nothing in accuracy and saves every read.
    CACHE_TTL_SECONDS = 60 * 60 * 24

    def __init__(self, imagery: Sentinel2Source | None = None) -> None:
        self.imagery = imagery or Sentinel2Source()
        self._cache = JsonCache("fuel_moisture", self.CACHE_TTL_SECONDS)
        self._reads = asyncio.Semaphore(max(1, settings.eo_max_concurrent_reads))

    @property
    def available(self) -> bool:
        return EO_AVAILABLE and settings.fuel_moisture_enabled

    async def observe(
        self,
        incident: Incident,
        detections: list[Detection],
        fuel: str,
        force: bool = False,
    ) -> FuelMoisture | None:
        """Live fuel moisture around this fire, or None if it cannot be measured.

        None is a normal outcome — cloud, no recent pass, or the raster extra
        not installed — and callers fall back to the weather-driven moisture
        codes, which is what they used before this module existed.
        """
        if not self.available:
            return None

        key = (
            f"{incident.id}|{fuel}"
            f"|{datetime.now(UTC).strftime('%Y-%m-%d')}"
            f"|v{MEASUREMENT_VERSION}"
        )
        cached = self._cache.read(key, force=force)
        if cached is not None:
            return _from_cache(cached) if cached else None

        result = await self._measure(incident, detections, fuel, force=force)
        self._cache.write(key, vars(result) if result else {})
        return result

    # --------------------------------------------------------------- internal

    async def _measure(
        self, incident: Incident, detections: list[Detection], fuel: str, force: bool = False
    ) -> FuelMoisture | None:
        bbox = padded_bbox(
            [d.latitude for d in detections],
            [d.longitude for d in detections],
            pad_km=RING_OUTER_KM + 1.0,
        )
        if bbox is None:
            return None

        now = datetime.now(UTC)
        scene = await self.imagery.best_scene(
            bbox, now - timedelta(days=SEARCH_DAYS), now, force=force
        )
        if scene is None:
            log.info("fuel_moisture.no_scene", incident=incident.id)
            return None

        async with self._reads:
            stack = await asyncio.to_thread(read_indices, scene, bbox)
        if stack is None:
            return None

        return _summarise_ring(stack, detections, fuel)


def _summarise_ring(stack: Any, detections: list[Detection], fuel: str) -> FuelMoisture | None:
    """Median NDMI and NDVI over unburnt ground around the fire."""
    import numpy as np
    from rasterio import features
    from rasterio.warp import transform_geom
    from shapely.geometry import MultiPoint, mapping, shape

    hull = MultiPoint([(d.longitude, d.latitude) for d in detections]).convex_hull
    projected = shape(transform_geom("EPSG:4326", stack.crs, mapping(hull), precision=2))

    inner = features.geometry_mask(
        [mapping(projected.buffer(RING_INNER_KM * 1000.0))],
        out_shape=stack.shape,
        transform=stack.transform,
        invert=True,
    )
    outer = features.geometry_mask(
        [mapping(projected.buffer(RING_OUTER_KM * 1000.0))],
        out_shape=stack.shape,
        transform=stack.transform,
        invert=True,
    )
    # The ring: inside the outer buffer, outside the inner one. Excluding the
    # inner disc is the whole point — see the module docstring.
    ring = outer & ~inner & stack.valid & ~stack.water

    ndmi_values = stack.ndmi[ring]
    ndvi_values = stack.ndvi[ring]
    ndmi_values = ndmi_values[~np.isnan(ndmi_values)]
    ndvi_values = ndvi_values[~np.isnan(ndvi_values)]

    if ndmi_values.size < MIN_SAMPLE_PIXELS:
        log.info("fuel_moisture.too_few_pixels", pixels=int(ndmi_values.size))
        return None

    # Median rather than mean: cloud edges, roads and rooftops all survive the
    # scene classification occasionally, and each one is an outlier.
    ndmi = float(np.median(ndmi_values))
    ndvi = float(np.median(ndvi_values)) if ndvi_values.size else 0.0
    moisture = live_moisture_from_ndmi(ndmi, fuel)

    return FuelMoisture(
        ndmi=round(ndmi, 3),
        ndvi=round(ndvi, 3),
        live_moisture_pct=round(moisture, 1),
        fuel=fuel,
        spread_factor=spread_factor(moisture, fuel),
        observed_at=stack.scene.observed_at,
        scene_id=stack.scene.id,
        sample_pixels=int(ndmi_values.size),
        source="Copernicus Sentinel-2 L2A",
    )


# --------------------------------------------------------------------------
# The mapping — the part that wants calibrating against Greek field data
# --------------------------------------------------------------------------


def live_moisture_from_ndmi(ndmi: float, fuel: str) -> float:
    """Live fuel moisture content (% oven-dry weight) from NDMI.

    Linear between the fuel's two published anchors, clamped outside them. The
    linearity is an admission, not a claim: the true relationship is neither
    linear nor species-independent, and pretending otherwise with a fitted curve
    would dress up the same ignorance in more decimal places.
    """
    band = MOISTURE_RANGES.get(fuel, DEFAULT_RANGE)
    span = band.ndmi_wet - band.ndmi_dry
    fraction = (ndmi - band.ndmi_dry) / span if span else 0.5
    fraction = max(0.0, min(1.0, fraction))
    return band.lfmc_dry + fraction * (band.lfmc_wet - band.lfmc_dry)


def spread_factor(live_moisture_pct: float, fuel: str) -> float:
    """How much to scale the head rate of spread for observed live moisture.

    The FBP rate-of-spread equations take dead fuel moisture through ISI and
    nothing else; live fuel moisture is absent from the model entirely. So this
    is adding a missing effect rather than double-counting one — which is the
    justification for applying it at all.

    Linear in the gap between the observed moisture and this fuel's reference,
    normalised by *this fuel's own moisture range*. Normalising by the range
    rather than by the distance to extinction is what makes the correction
    comparable across fuels: extinction sits much closer to the reference for
    phrygana than for pine, so scaling by it made the same relative dryness
    produce a 1.69 multiplier in scrub and 1.12 in forest — and the scrub case
    then clamped, throwing the difference away.

    Extinction is deliberately not consulted. A fuel above its moisture of
    extinction will not carry fire, but the fire in question is one a satellite
    has just detected burning, so the observation outranks the index.
    """
    band = MOISTURE_RANGES.get(fuel, DEFAULT_RANGE)
    span = band.lfmc_wet - band.lfmc_dry
    if span <= 0:
        return 1.0
    factor = 1.0 + (band.reference - live_moisture_pct) / span * RESPONSE
    return round(max(MIN_SPREAD_FACTOR, min(MAX_SPREAD_FACTOR, factor)), 3)


def _from_cache(entry: dict[str, Any]) -> FuelMoisture:
    data = dict(entry)
    observed = data.get("observed_at")
    if not isinstance(observed, datetime):
        parsed = datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
        data["observed_at"] = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return FuelMoisture(**data)
