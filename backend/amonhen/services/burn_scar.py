"""Burned area, measured rather than inferred.

Until now the platform reported burned area by counting distinct 375 m thermal
pixels. That number is explicitly a lower bound and it is a bad one: the
satellite only sees ground that was actively flaming at the moment of an
overpass, so anything that burned between passes is invisible, and a fire's
footprint is quantised to a grid twenty times coarser than the fire's edge. The
perimeter drawn from those pixels is a convex hull — a sketch the UI has to
apologise for every time it draws one.

This replaces both with a measurement, from Sentinel-2 at 20 m:

    NBR   = (B8A - B12) / (B8A + B12)
    dNBR  = NBR_before - NBR_after

Fire removes vegetation (which is bright in near-infrared) and leaves char and
ash (which are bright in shortwave infrared). Both halves of the ratio move the
same way, so a burn scar produces a large positive dNBR while almost nothing
else does. This is the standard measurement — Key & Benson (2006), and what
EFFIS itself maps burnt area with.

Two things this is careful about, because both produce confident nonsense:

**Cloud shadow.** A shadow darkens near-infrared exactly the way a burn scar
does. Unmasked, an afternoon of cumulus maps as a severe burn. Every pixel is
screened through the scene classification layer before it counts.

**Other people's fires.** A 20 m image over Greece in August contains several
scars, plus ploughed fields and harvested stubble that also read as change. So
the burned mask is polygonised and only connected components touching this
fire's own detections are kept. Components are kept *whole* — a scar that
extends well beyond the detections is exactly what we are trying to measure —
but a scar in the next valley never joins.

What this still cannot do: see through cloud, and see a fire that started this
morning. Sentinel-2 revisits every 2-3 days. Until a clear pass lands, there is
no measurement and the platform says so rather than substituting one.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from amonhen.core.config import settings
from amonhen.core.logging import get_logger
from amonhen.domain.entities import Detection, Incident, Perimeter
from amonhen.domain.enums import BurnSeverity
from amonhen.sources.base import JsonCache
from amonhen.sources.sentinel2 import (
    EO_AVAILABLE,
    IndexStack,
    Scene,
    Sentinel2Source,
    padded_bbox,
    post_fire_window,
    pre_fire_window,
    read_indices,
)

log = get_logger(__name__)

#: Below this, the change is not a burn. Key & Benson put the unburned/low
#: boundary at dNBR 0.10; the same figure is what EFFIS uses to close a burnt
#: area polygon. Lowering it picks up ploughed fields, raising it loses the
#: light surface burns that matter most for whether a village is defensible.
BURNED_DNBR = 0.10

#: Key & Benson (2006) severity classes, as (lower bound, class). The USGS
#: publishes these as dNBR x 1000; kept here in native units to avoid a
#: conversion nobody would remember to apply.
SEVERITY_BREAKS: tuple[tuple[float, BurnSeverity], ...] = (
    (0.66, BurnSeverity.HIGH),
    (0.44, BurnSeverity.MODERATE_HIGH),
    (0.27, BurnSeverity.MODERATE_LOW),
    (0.10, BurnSeverity.LOW),
)

#: Connected burned patches smaller than this are dropped as speckle. Half a
#: hectare is twelve 20 m pixels — below that we are looking at co-registration
#: error and single-pixel noise, not a fire.
MIN_PATCH_HA = 0.5

#: How far from a thermal detection a burned patch may start and still be
#: counted as this fire. Generous, because detections cluster on the active
#: front while the scar covers everything already burnt.
FIRE_SEED_BUFFER_KM = 1.5

#: Padding around the detections for the image window.
WINDOW_PAD_KM = 3.0

#: Below this fraction of clear pixels the scene is more cloud than ground over
#: this fire, and any area from it is a cloud map.
MIN_USABLE_FRACTION = 0.40

SQUARE_METRES_PER_HECTARE = 10_000.0

#: Bump when the measurement changes. It is part of the cache key, so a
#: correction takes effect at once rather than being masked for hours by results
#: the previous version wrote — which is how a physically impossible mean dNBR
#: of 8.8 survived a forced refresh during development.
MEASUREMENT_VERSION = 2

#: Vertex simplification tolerance, in metres. One pixel: the boundary cannot be
#: more precise than the grid it was measured on, so nothing real is lost.
SIMPLIFY_TOLERANCE_M = 20.0


@dataclass
class BurnScar:
    """A measured burn scar. Everything here comes from imagery, not inference."""

    incident_id: str
    burned_area_ha: float
    mean_dnbr: float
    #: Relative dNBR (Miller & Thode 2007), which divides out how much there was
    #: to burn in the first place. Reported because in sparse phrygana plain
    #: dNBR systematically under-rates severity — there was never much
    #: near-infrared signal there to lose.
    mean_rdnbr: float
    severity_ha: dict[str, float]
    #: GeoJSON MultiPolygon in EPSG:4326, or None when nothing burned.
    geometry: dict[str, Any] | None
    pre_scene_id: str
    post_scene_id: str
    pre_image_date: datetime
    post_image_date: datetime
    #: Fraction of the window that was clear enough to measure.
    usable_fraction: float
    #: 0-1. How much to trust this figure — see `_confidence`.
    confidence: float
    #: Plain-language, for the dossier.
    note: str = ""

    @property
    def dominant_severity(self) -> str:
        if not self.severity_ha:
            return BurnSeverity.UNBURNED.value
        return max(self.severity_ha.items(), key=lambda item: item[1])[0]

    @property
    def is_partial(self) -> bool:
        """True while the fire was still burning when the image was taken."""
        return self.note.startswith("Still burning")


@dataclass
class BurnScarUnavailable:
    """Why there is no measurement. Shown to the operator instead of silence.

    A missing burned area is not a bug and not an error — most often it means
    the satellite has not passed over yet. Saying which of those it is stops
    people concluding the feature is broken.
    """

    reason: str
    detail: str = ""
    #: True when a later pass will fix it on its own.
    transient: bool = True


Outcome = BurnScar | BurnScarUnavailable


class BurnScarService:
    """Maps a fire's burn scar from a pre/post Sentinel-2 pair."""

    #: A scar does not change once the fire is out, but an active fire's scar
    #: grows daily. Twelve hours re-checks roughly once per satellite pass while
    #: still collapsing the repeat work of a rebuild every fifteen minutes.
    CACHE_TTL_SECONDS = 60 * 60 * 12

    def __init__(self, imagery: Sentinel2Source | None = None) -> None:
        self.imagery = imagery or Sentinel2Source()
        self._cache = JsonCache("burn_scar", self.CACHE_TTL_SECONDS)
        # Imagery reads are the slowest thing in a rebuild. Without this, twenty
        # simultaneous fires open sixty connections to the same bucket at once.
        self._reads = asyncio.Semaphore(max(1, settings.eo_max_concurrent_reads))

    @property
    def available(self) -> bool:
        return EO_AVAILABLE and settings.burn_scar_enabled

    async def assess(
        self,
        incident: Incident,
        detections: list[Detection],
        force: bool = False,
    ) -> Outcome:
        """Measure this fire's burn scar, or explain why we cannot.

        Never raises. A failed image read leaves the incident reporting its
        thermal-pixel estimate, which is what it did before this module existed.
        """
        if not EO_AVAILABLE:
            return BurnScarUnavailable(
                reason="Satellite imagery support is not installed",
                detail='Install the raster extra to enable this: uv pip install -e ".[eo]"',
                transient=False,
            )
        if not settings.burn_scar_enabled:
            return BurnScarUnavailable(
                reason="Burned-area mapping is switched off", transient=False
            )

        # Cache on the fire *and* how far it has got: a still-growing fire must
        # be re-measured as it grows, a finished one never needs re-reading.
        key = (
            f"{incident.id}"
            f"|{_as_utc(incident.last_detected_at).strftime('%Y-%m-%dT%H')}"
            f"|v{MEASUREMENT_VERSION}"
        )
        cached = self._cache.read(key, force=force)
        if cached is not None:
            return _from_cache(cached)

        outcome = await self._measure(incident, detections, force=force)
        self._cache.write(key, _to_cache(outcome))
        return outcome

    # --------------------------------------------------------------- internal

    async def _measure(
        self, incident: Incident, detections: list[Detection], force: bool = False
    ) -> Outcome:
        bbox = padded_bbox(
            [d.latitude for d in detections],
            [d.longitude for d in detections],
            pad_km=WINDOW_PAD_KM,
        )
        if bbox is None:
            return BurnScarUnavailable(
                reason="This cluster is too scattered to image as one fire",
                transient=False,
            )

        first_seen = _as_utc(incident.first_detected_at)

        post_start, post_end = post_fire_window(
            first_seen, _as_utc(incident.last_detected_at)
        )
        post = await self.imagery.best_scene(bbox, post_start, post_end, force=force)
        if post is None:
            return BurnScarUnavailable(
                reason="No clear satellite image since the fire started",
                detail=(
                    "Sentinel-2 passes every 2-3 days and cannot see through cloud. "
                    "Until one lands, burnt area is estimated from heat detections."
                ),
            )

        # The pre-fire image must come from the same map tile. A different tile
        # means a different UTM zone and an unaligned pixel grid, and
        # differencing two grids that do not line up measures the misalignment.
        pre_start, pre_end = pre_fire_window(first_seen)
        pre = await self.imagery.best_scene(
            bbox, pre_start, pre_end, grid_code=post.grid_code or None, force=force
        )
        if pre is None:
            return BurnScarUnavailable(
                reason="No clear satellite image from before the fire",
                detail="Nothing usable in the six weeks before it started, so there is "
                "no 'before' to compare against.",
            )

        async with self._reads:
            stacks = await asyncio.gather(
                asyncio.to_thread(read_indices, pre, bbox),
                asyncio.to_thread(read_indices, post, bbox),
            )
        pre_stack, post_stack = stacks
        if pre_stack is None or post_stack is None:
            return BurnScarUnavailable(reason="Could not read the satellite imagery")

        return _delineate(incident, detections, pre_stack, post_stack)


# --------------------------------------------------------------------------
# The measurement
# --------------------------------------------------------------------------


def _delineate(
    incident: Incident,
    detections: list[Detection],
    pre: IndexStack,
    post: IndexStack,
) -> Outcome:
    """Difference the two scenes and turn the burned mask into a polygon."""
    import numpy as np
    from rasterio import features
    from rasterio.warp import transform_geom
    from shapely.geometry import MultiPoint, mapping, shape
    from shapely.ops import unary_union

    if pre.shape != post.shape:
        # Should be impossible once both scenes share an MGRS tile, but a
        # silent shape mismatch would difference unrelated ground.
        log.warning("burn_scar.shape_mismatch", pre=pre.shape, post=post.shape)
        return BurnScarUnavailable(reason="The two images do not line up")

    both_clear = pre.valid & post.valid & ~post.water
    usable_fraction = round(float(both_clear.sum()) / max(both_clear.size, 1), 3)
    if usable_fraction < MIN_USABLE_FRACTION:
        return BurnScarUnavailable(
            reason="Too much cloud over this fire to measure it",
            detail=f"Only {usable_fraction:.0%} of the area was visible in both images.",
        )

    dnbr = pre.nbr - post.nbr
    burned = np.where(both_clear, dnbr, np.nan) >= BURNED_DNBR
    if not burned.any():
        return BurnScarUnavailable(
            reason="No burn scar visible yet",
            detail="The satellite has looked, and the ground has not changed enough to "
            "measure. Common for a fire only a few hours old.",
        )

    # --- keep only patches belonging to this fire ---------------------------
    #
    # Polygonise first, then filter. Filtering the raster instead would clip the
    # scar to a buffer around the detections, which is the thing we are trying
    # not to do: the whole point is that the burn extends past what the thermal
    # sensor happened to catch.
    detection_hull = MultiPoint([(d.longitude, d.latitude) for d in detections]).convex_hull
    seed = shape(
        transform_geom("EPSG:4326", post.crs, mapping(detection_hull), precision=2)
    ).buffer(FIRE_SEED_BUFFER_KM * 1000.0)

    pixel_area_m2 = abs(post.transform.a * post.transform.e)
    min_patch_m2 = MIN_PATCH_HA * SQUARE_METRES_PER_HECTARE

    patches = []
    for geometry, value in features.shapes(
        burned.astype("uint8"), mask=burned, transform=post.transform
    ):
        if not value:
            continue
        polygon = shape(geometry)
        if polygon.area < min_patch_m2 or not polygon.intersects(seed):
            continue
        patches.append(polygon)

    if not patches:
        return BurnScarUnavailable(
            reason="No burn scar found at this fire",
            detail="Ground did change nearby, but not where the heat was detected — so "
            "it is another fire or farm work, not this one.",
        )

    scar = unary_union(patches)
    burned_area_ha = round(scar.area / SQUARE_METRES_PER_HECTARE, 1)

    # Polygonising a raster traces every pixel corner, so a large scar arrives
    # as a staircase of tens of thousands of vertices — invisible on screen and
    # expensive to ship. Simplifying at one pixel keeps the boundary honest to
    # the resolution it was measured at. Area is taken *before* this, so the
    # figure reported is the measured one and not a artefact of smoothing.
    simplified = scar.simplify(SIMPLIFY_TOLERANCE_M, preserve_topology=True)
    if not simplified.is_empty:
        scar = simplified

    # --- severity, over the kept scar only ----------------------------------
    scar_mask = features.geometry_mask(
        [mapping(scar)], out_shape=post.shape, transform=post.transform, invert=True
    )
    inside = scar_mask & both_clear & (dnbr >= BURNED_DNBR)
    values = dnbr[inside]
    if values.size == 0:
        return BurnScarUnavailable(reason="No burn scar found at this fire")

    severity_ha: dict[str, float] = {}
    for lower, severity in SEVERITY_BREAKS:
        upper = _upper_bound(lower)
        count = int(((values >= lower) & (values < upper)).sum())
        if count:
            severity_ha[severity.value] = round(
                count * pixel_area_m2 / SQUARE_METRES_PER_HECTARE, 1
            )

    # RdNBR normalises by how much there was to lose. Guarded because pre-fire
    # NBR near zero — bare rock, a dry riverbed — would otherwise divide by ~0
    # and report an infinitely severe burn on ground that never had fuel.
    pre_nbr = pre.nbr[inside]
    denominator = np.sqrt(np.abs(np.where(np.abs(pre_nbr) < 0.001, 0.001, pre_nbr)))
    rdnbr = values / denominator

    # --- back to lon/lat for the map ----------------------------------------
    geometry_4326 = transform_geom(post.crs, "EPSG:4326", mapping(scar), precision=6)

    still_burning = _as_utc(incident.last_detected_at) > post.scene.observed_at
    scar_result = BurnScar(
        incident_id=incident.id,
        burned_area_ha=burned_area_ha,
        mean_dnbr=round(float(np.nanmean(values)), 3),
        mean_rdnbr=round(float(np.nanmean(rdnbr)), 3),
        severity_ha=severity_ha,
        geometry=geometry_4326,
        pre_scene_id=pre.scene.id,
        post_scene_id=post.scene.id,
        pre_image_date=pre.scene.observed_at,
        post_image_date=post.scene.observed_at,
        usable_fraction=usable_fraction,
        confidence=_confidence(usable_fraction, pre.scene, post.scene, still_burning),
        note=_note(post.scene, still_burning),
    )
    log.info(
        "burn_scar.measured",
        incident=incident.id,
        area_ha=burned_area_ha,
        thermal_estimate_ha=incident.estimated_area_ha,
        mean_dnbr=scar_result.mean_dnbr,
        post_image=post.scene.date_label,
    )
    return scar_result


def _upper_bound(lower: float) -> float:
    """The next break above `lower`, or infinity for the top class."""
    higher = [bound for bound, _ in SEVERITY_BREAKS if bound > lower]
    return min(higher) if higher else math.inf


def _confidence(
    usable_fraction: float, pre: Scene, post: Scene, still_burning: bool
) -> float:
    """How much to trust this figure, 0-1.

    Three things degrade it, in rough order of how much damage they do:
    cloud in the window, a stale "before" image (the longer the gap, the more
    of the change is seasonal rather than fire), and a fire still burning when
    the picture was taken — which makes the area real but not final.
    """
    score = 0.95 * usable_fraction

    gap_days = abs((post.observed_at - pre.observed_at).days)
    if gap_days > 30:
        score -= 0.15
    elif gap_days > 14:
        score -= 0.05

    if still_burning:
        score -= 0.10
    return round(max(0.1, min(1.0, score)), 2)


def _note(post: Scene, still_burning: bool) -> str:
    """One plain-language line for the dossier."""
    when = post.date_label
    if still_burning:
        return (
            f"Still burning when this was measured. This is the area burnt as of "
            f"{when}, not the final total."
        )
    return f"Measured from a satellite image taken on {when}."


# --------------------------------------------------------------------------
# Turning a scar into the things the rest of the platform already understands
# --------------------------------------------------------------------------


def to_perimeter(scar: BurnScar) -> Perimeter | None:
    """The measured scar as a Perimeter, so the map can draw it like any other.

    `method` is what tells the UI to stop apologising: a detection hull is drawn
    as a sketch, this is drawn as a boundary, and the difference is real.
    """
    if scar.geometry is None:
        return None
    return Perimeter(
        incident_id=scar.incident_id,
        observed_at=scar.post_image_date,
        geometry=scar.geometry,
        area_ha=scar.burned_area_ha,
        method="sentinel2_dnbr",
        confidence=scar.confidence,
    )


def _to_cache(outcome: Outcome) -> dict[str, Any]:
    if isinstance(outcome, BurnScar):
        return {"kind": "scar", **{k: v for k, v in vars(outcome).items()}}
    return {"kind": "unavailable", **vars(outcome)}


def _from_cache(entry: dict[str, Any]) -> Outcome:
    data = {k: v for k, v in entry.items() if k != "kind"}
    if entry.get("kind") == "scar":
        data["pre_image_date"] = _parse(data["pre_image_date"])
        data["post_image_date"] = _parse(data["post_image_date"])
        return BurnScar(**data)
    return BurnScarUnavailable(**data)


def _parse(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
