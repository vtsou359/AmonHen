"""Sentinel-2 surface reflectance, and the three indices worth computing from it.

This is the module the `[eo]` extra exists for. Everything until now has been
point data — a 375 m thermal pixel, a weather grid cell, a land cover code. This
reads actual imagery, at 20 m, and it is where spectral bands finally pay for
themselves:

    NBR  = (B8A - B12) / (B8A + B12)   burn scars. Differenced pre/post, this is
                                       the standard measurement of burned area
                                       and severity.
    NDMI = (B8A - B11) / (B8A + B11)   water in living vegetation. The one
                                       band-derived quantity that genuinely
                                       informs how fast a fire spreads.
    NDVI = (B8A - B04) / (B8A + B04)   greenness. Tells us whether the farmland
                                       the land cover map found is standing
                                       green or harvested stubble.

One window read serves all three, which is why they live together: the
expensive part is the HTTP round-trips, not the arithmetic.

Source: **Sentinel-2 L2A**, Copernicus, via the Earth Search STAC catalogue and
the public `sentinel-cogs` bucket on AWS. Chosen over the Copernicus Data Space
Ecosystem for the reason that decides every source in this codebase: it needs no
credential. Same ESA pixels, same processing baseline, keyless. CDSE remains the
right answer the day we need bulk download or a product AWS does not mirror.

Cloud-Optimized GeoTIFFs mean we never download a scene. GDAL issues HTTP range
requests for just the tiles covering the fire — a few megabytes for a typical
incident instead of the ~1 GB the full granule would be.

----------------------------------------------------------------------------
The trap in this module: the reflectance offset is already applied
----------------------------------------------------------------------------

Sentinel-2 processing baseline 04.00 (January 2022) added a -1000 shift to the
digital numbers, so for ESA's own products reflectance is `DN * 0.0001 - 0.1`.
Every asset here duly carries `raster:bands: [{scale: 0.0001, offset: -0.1}]`,
and by the STAC convention that reads as an instruction to apply it.

**Applying it to these files is wrong.** Earth Search bakes the offset into the
COGs when it builds them, and says so in a different field entirely:
`earthsearch:boa_offset_applied: true`. The `raster:bands` offset describes the
ESA convention the product came from, not a correction still outstanding.

Applying it twice drives dark pixels negative, which is physically impossible
and arithmetically silent. Measured over Parnitha, red reflectance came out at
-0.034 for 67% of pixels, and NDVI — a quantity mathematically confined to
[-1, 1] — returned 1.457.

The check that settles it needs no documentation at all, only two images of the
same ground in the same week either side of the baseline change:

    2021, baseline 03.01, offset genuinely zero   red 0.0775   NIR 0.2683
    2026, baseline 05.12, scale only              red 0.0662   NIR 0.2909
    2026, baseline 05.12, offset applied          red -0.0338  NIR 0.1909

The first two agree; the third is impossible. So `boa_offset_applied` wins over
`raster:bands`, and `_normalised_difference` floors reflectance at zero as a
standing guard — if this ever regresses, it now fails loudly at the floor rather
than quietly through the severity classes.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from amonhen.core.config import settings
from amonhen.core.logging import get_logger
from amonhen.sources.base import DataSource

log = get_logger(__name__)

# --------------------------------------------------------------------------
# Optional dependency
# --------------------------------------------------------------------------
#
# rasterio pulls GDAL, which is by far the heaviest thing this project could
# depend on. The whole active-response path — detections, clustering, weather,
# spread, projection — works without it, so it stays out of the default image
# and every consumer of this module checks `EO_AVAILABLE` first. Absent, burn
# scars and fuel moisture are simply not reported; nothing else changes.
try:  # pragma: no cover - exercised by whether the extra is installed
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window, from_bounds

    EO_AVAILABLE = True
    EO_IMPORT_ERROR = ""
except ImportError as exc:  # pragma: no cover
    EO_AVAILABLE = False
    EO_IMPORT_ERROR = str(exc)


def _configure_gdal() -> None:
    """Make GDAL behave for range reads against a public bucket.

    Without `GDAL_DISABLE_READDIR_ON_OPEN` it lists the object's whole prefix on
    every open, which over HTTP is dozens of wasted requests per band.
    """
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
    os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "3")
    os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "1")
    os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")


if EO_AVAILABLE:  # pragma: no cover
    _configure_gdal()


# --------------------------------------------------------------------------
# What we ask for
# --------------------------------------------------------------------------

#: STAC asset keys -> the Sentinel-2 band each one is. All 20 m except red,
#: which only exists at 10 m and is averaged down onto the same grid.
ASSET_NIR = "nir08"      # B8A, 865 nm — the NIR both NBR and NDMI hang off
ASSET_SWIR1 = "swir16"   # B11, 1610 nm — vegetation water absorption
ASSET_SWIR2 = "swir22"   # B12, 2190 nm — char and ash
ASSET_RED = "red"        # B04, 665 nm, 10 m
ASSET_SCL = "scl"        # Scene classification, for cloud and shadow masking

#: Scene Classification Layer values that make a pixel unusable. Cloud shadow
#: (3) matters more than cloud here: a shadow darkens exactly the bands a burn
#: scar darkens, so an unmasked shadow reads as a severe burn. This single mask
#: is the difference between a burned-area figure and a cloud map.
SCL_UNUSABLE = frozenset(
    {
        0,   # no data
        1,   # saturated or defective
        3,   # cloud shadow
        8,   # cloud, medium probability
        9,   # cloud, high probability
        10,  # thin cirrus
        11,  # snow or ice
    }
)

#: Water is valid imagery but can never burn, so it is excluded from burned-area
#: counts. Kept separate from SCL_UNUSABLE because it is a different statement:
#: "we saw this clearly and it is sea", not "we could not see it".
SCL_WATER = 6

#: Sentinel-2 revisit is 2-3 days with three satellites, 5 nominal. Looking back
#: further than this for a pre-fire scene starts comparing against a different
#: season, which is its own signal rather than the fire's.
PRE_FIRE_SEARCH_DAYS = 45

#: Reading a window larger than this is a sign the cluster is scattered rather
#: than one fire; the read would be slow and the answer meaningless.
MAX_WINDOW_KM = 40.0

KM_PER_DEGREE_LAT = 111.32

#: Bumped whenever the reflectance conversion changes. It is part of the scene
#: cache key, so a correction takes effect immediately instead of waiting out
#: the cache.
_ASSET_METADATA_VERSION = 2


@dataclass(frozen=True)
class Scene:
    """One Sentinel-2 granule, as far as we care about it."""

    id: str
    observed_at: datetime
    cloud_cover_pct: float
    #: MGRS tile, e.g. "MGRS-34SGH". Pre- and post-fire scenes must share this:
    #: different tiles mean different UTM zones and unaligned pixel grids.
    grid_code: str
    #: asset key -> {href, scale, offset}
    assets: dict[str, dict[str, Any]] = field(default_factory=dict)
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    def href(self, asset: str) -> str:
        return str(self.assets.get(asset, {}).get("href", ""))

    def contains(self, bbox: tuple[float, float, float, float]) -> bool:
        west, south, east, north = bbox
        return (
            self.bbox[0] <= west
            and self.bbox[1] <= south
            and self.bbox[2] >= east
            and self.bbox[3] >= north
        )

    @property
    def date_label(self) -> str:
        return self.observed_at.strftime("%d %b %Y")


@dataclass
class IndexStack:
    """Co-registered index arrays for one scene over one window.

    Everything here is on the same 20 m grid: same shape, same transform, same
    CRS. That is what makes differencing two scenes pixel-by-pixel legitimate.
    """

    scene: Scene
    #: Normalised Burn Ratio. NaN where the pixel was unusable.
    nbr: Any
    #: Normalised Difference Moisture Index — water in living vegetation.
    ndmi: Any
    #: Normalised Difference Vegetation Index — greenness.
    ndvi: Any
    #: True where the pixel was clear enough to use.
    valid: Any
    #: True where SCL called it water.
    water: Any
    transform: Any
    crs: Any

    @property
    def shape(self) -> tuple[int, int]:
        return tuple(self.nbr.shape)  # type: ignore[return-value]

    @property
    def usable_fraction(self) -> float:
        """How much of the window we could actually see. Below ~0.4 the scene
        is more cloud than ground and any statistic from it is noise."""
        total = int(self.valid.size)
        return round(float(self.valid.sum()) / total, 3) if total else 0.0


class Sentinel2Source(DataSource[Scene]):
    """Finds Sentinel-2 scenes. Reading their pixels is `read_indices` below.

    Split that way because the two have completely different costs and cache
    lifetimes: the search is a small JSON query whose answer changes as new
    passes land, and the read is tens of megabytes of imagery that never changes
    once published.
    """

    name = "sentinel2"
    attribution = "Copernicus Sentinel-2 L2A via Earth Search (Element 84 / AWS Open Data)"
    homepage = "https://registry.opendata.aws/sentinel-2-l2a-cogs/"
    #: New passes land every 2-3 days, so a six-hour cache never hides one for
    #: long while still collapsing the repeat searches a rebuild generates.
    cache_ttl_seconds = 60 * 60 * 6
    requires_credentials = False

    @property
    def is_live(self) -> bool:
        """Live only when the raster stack is installed.

        Reporting "live" without rasterio would be a lie the status banner
        repeats: the search would succeed and every read would fail.
        """
        return EO_AVAILABLE

    def status(self) -> dict[str, Any]:
        state = super().status()
        if not EO_AVAILABLE:
            # Docker is the documented way to run this, and a `uv pip install`
            # inside a container is lost at the next rebuild, so lead with the
            # instruction that survives one.
            state["note"] = (
                "Raster support not installed. Set AMONHEN_INSTALL_EO=true in .env and run "
                "`docker compose up -d --build` (or, outside Docker, "
                'uv pip install -e ".[eo]") to enable burned-area mapping and live fuel moisture.'
            )
        return state

    async def _fetch_live(
        self,
        bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0),
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 40,
        force: bool = False,
        **_: Any,
    ) -> list[Scene]:
        if start is None or end is None:
            return []

        cache_key = (
            f"{bbox[0]:.3f},{bbox[1]:.3f},{bbox[2]:.3f},{bbox[3]:.3f}"
            f"|{start.date().isoformat()}|{end.date().isoformat()}"
            # The cached record carries each asset's scale and offset, so a
            # change to how those are derived must invalidate it. Without this,
            # correcting the offset silently kept serving the old conversion
            # until the cache aged out.
            f"|v{_ASSET_METADATA_VERSION}"
        )
        cached = self._cache_read(cache_key, force=force)
        if cached is not None:
            return [_scene_from_cache(entry) for entry in cached]

        response = await self._get(
            f"{settings.sentinel2_stac_url.rstrip('/')}/search",
            params={
                "collections": settings.sentinel2_collection,
                "bbox": ",".join(f"{v:.5f}" for v in bbox),
                "datetime": f"{_iso(start)}/{_iso(end)}",
                "limit": str(limit),
                # Newest first: for the post-fire pass we want the most recent
                # clear look, and for the pre-fire one the closest before ignition.
                "sortby": "-properties.datetime",
            },
        )
        scenes = [
            scene
            for item in response.json().get("features", [])
            if (scene := _scene_from_item(item)) is not None
        ]
        self._cache_write(cache_key, [_scene_to_cache(s) for s in scenes])
        log.info("sentinel2.searched", found=len(scenes), start=_iso(start), end=_iso(end))
        return scenes

    def _load_fixture(self, **_: Any) -> list[Scene]:
        """No scenes.

        There is no honest fixture for imagery. A fabricated burn scar would put
        a made-up polygon on the map labelled as a measurement, which is worse
        than the platform simply saying it has not seen the ground yet.
        """
        return []

    # ---------------------------------------------------------------- public

    async def best_scene(
        self,
        bbox: tuple[float, float, float, float],
        start: datetime,
        end: datetime,
        grid_code: str | None = None,
        max_cloud_pct: float | None = None,
        force: bool = False,
    ) -> Scene | None:
        """The most usable scene in a window, or None.

        "Most usable" is deliberately not "most recent": a fresh pass under 80%
        cloud tells you nothing, while a five-day-old clear one measures the
        scar precisely. Recency only breaks ties.
        """
        limit = max_cloud_pct if max_cloud_pct is not None else settings.sentinel2_max_cloud_pct
        scenes = await self.fetch(bbox=bbox, start=start, end=end, force=force)
        if not scenes:
            return None

        candidates = [s for s in scenes if s.cloud_cover_pct <= limit]
        if grid_code:
            candidates = [s for s in candidates if s.grid_code == grid_code]
        # Greek fires near 24°E straddle two UTM zones and match two MGRS tiles.
        # Prefer one that covers the whole fire over one that clips it.
        covering = [s for s in candidates if s.contains(bbox)]
        pool = covering or candidates
        if not pool:
            return None

        # Cloud cover in 10% bands, then newest. Splitting hairs between 3% and
        # 7% cloud is meaningless; a two-week-old scene versus a fresh one is not.
        return max(pool, key=lambda s: (-int(s.cloud_cover_pct // 10), s.observed_at))


# --------------------------------------------------------------------------
# Reading pixels
# --------------------------------------------------------------------------


def read_indices(scene: Scene, bbox: tuple[float, float, float, float]) -> IndexStack | None:
    """Read one scene over `bbox` and return NBR, NDMI and NDVI.

    Blocking: this makes HTTP range requests through GDAL. Callers run it in a
    worker thread (`asyncio.to_thread`) so the event loop keeps serving.

    Returns None when the raster stack is missing, the window falls outside the
    granule, or a band cannot be read. Never raises: a failed image read must
    degrade the picture, not break it.
    """
    if not EO_AVAILABLE:
        return None

    href_nir = scene.href(ASSET_NIR)
    if not href_nir:
        return None

    try:
        with rasterio.open(href_nir) as reference:
            bounds = transform_bounds("EPSG:4326", reference.crs, *bbox, densify_pts=21)
            window = from_bounds(*bounds, transform=reference.transform)
            window = window.round_offsets().round_lengths()
            window = _clip_window(window, reference.width, reference.height)
            if window is None:
                log.info("sentinel2.window_outside_granule", scene=scene.id)
                return None

            nir = _scaled(reference, window, scene.assets.get(ASSET_NIR, {}))
            transform = reference.window_transform(window)
            crs = reference.crs
            nir_nodata = reference.nodata

        swir1 = _read_scaled(scene, ASSET_SWIR1, window)
        swir2 = _read_scaled(scene, ASSET_SWIR2, window)
        red = _read_scaled(scene, ASSET_RED, window, out_shape=nir.shape, bounds=bounds)
        scl = _read_raw(scene, ASSET_SCL, window)
    except Exception as exc:  # noqa: BLE001 — an unreachable bucket must not break the picture
        log.warning("sentinel2.read_failed", scene=scene.id, error=str(exc))
        return None

    if any(band is None for band in (swir1, swir2, red, scl)):
        return None

    unusable = np.isin(scl, list(SCL_UNUSABLE))
    water = scl == SCL_WATER
    valid = ~unusable
    if nir_nodata is not None:
        # The scaled array has already had the offset applied, so compare
        # against the scaled nodata rather than the raw sentinel value.
        valid &= nir > _apply(float(nir_nodata), scene.assets.get(ASSET_NIR, {}))

    return IndexStack(
        scene=scene,
        nbr=_normalised_difference(nir, swir2, valid),
        ndmi=_normalised_difference(nir, swir1, valid),
        ndvi=_normalised_difference(nir, red, valid),
        valid=valid,
        water=water,
        transform=transform,
        crs=crs,
    )


def _normalised_difference(a: Any, b: Any, valid: Any) -> Any:
    """(a - b) / (a + b), bounded to [-1, 1], with unusable pixels as NaN.

    Two guards, both learned the hard way.

    **Reflectance is floored at zero.** Applying the -0.1 BOA offset to a dark
    pixel — deep shadow, water, a wet roof — produces a *negative* reflectance,
    which is physically meaningless but arithmetically fine. A negative
    denominator then sends the ratio outside its own range: measured over Pindus
    forest, this returned an NDVI of 1.457 for a quantity that cannot exceed 1.
    Flooring at zero is the standard treatment for sub-zero L2A reflectance.

    **The result is clipped to [-1, 1].** For non-negative inputs a normalised
    difference is mathematically confined to that interval, so a value outside
    it is not a reading — it is proof that something upstream is wrong. Clipping
    stops one bad pixel from dragging a median, and the floor above stops it
    happening in the first place.
    """
    # Floor rather than mask: a shadowed pixel is dark, not missing, and
    # dropping it would bias the sample toward bright ground.
    a = np.maximum(a, 0.0)
    b = np.maximum(b, 0.0)

    total = a + b
    out = np.full(a.shape, np.nan, dtype="float32")
    usable = valid & (total > 1e-6)
    np.divide(a - b, total, out=out, where=usable)
    out[~usable] = np.nan
    return np.clip(out, -1.0, 1.0)


def _read_scaled(
    scene: Scene,
    asset: str,
    window: Any,
    out_shape: tuple[int, int] | None = None,
    bounds: tuple[float, float, float, float] | None = None,
) -> Any | None:
    """Read one band as reflectance, resampled onto the reference grid if needed."""
    href = scene.href(asset)
    if not href:
        return None
    with rasterio.open(href) as src:
        if out_shape is not None and bounds is not None:
            # Red is 10 m and everything else is 20 m. Derive its window from the
            # same map bounds rather than doubling the reference window: that
            # holds even if a future product changes grid alignment.
            own = from_bounds(*bounds, transform=src.transform).round_offsets().round_lengths()
            own = _clip_window(own, src.width, src.height)
            if own is None:
                return None
            # Averaging, not nearest: four 10 m pixels genuinely make up one
            # 20 m pixel, and nearest would throw three of them away.
            return _scaled(src, own, scene.assets.get(asset, {}), out_shape=out_shape,
                           resampling=Resampling.average)
        return _scaled(src, window, scene.assets.get(asset, {}))


def _read_raw(scene: Scene, asset: str, window: Any) -> Any | None:
    """Read a classification band with no scaling — SCL values are categories."""
    href = scene.href(asset)
    if not href:
        return None
    with rasterio.open(href) as src:
        return src.read(1, window=window)


def _scaled(
    src: Any,
    window: Any,
    asset_meta: dict[str, Any],
    out_shape: tuple[int, int] | None = None,
    resampling: Any = None,
) -> Any:
    """Read a band and convert digital numbers to reflectance."""
    kwargs: dict[str, Any] = {"window": window}
    if out_shape is not None:
        kwargs["out_shape"] = out_shape
        kwargs["resampling"] = resampling or Resampling.average
    raw = src.read(1, **kwargs).astype("float32")
    scale = float(asset_meta.get("scale", 0.0001))
    offset = float(asset_meta.get("offset", 0.0))
    return raw * scale + offset


def _apply(value: float, asset_meta: dict[str, Any]) -> float:
    return value * float(asset_meta.get("scale", 0.0001)) + float(asset_meta.get("offset", 0.0))


def _clip_window(window: Any, width: int, height: int) -> Any | None:
    """Trim a window to the granule, or None if it falls entirely outside."""
    col_off = max(int(window.col_off), 0)
    row_off = max(int(window.row_off), 0)
    col_end = min(int(window.col_off) + int(window.width), width)
    row_end = min(int(window.row_off) + int(window.height), height)
    if col_end <= col_off or row_end <= row_off:
        return None
    return Window(col_off, row_off, col_end - col_off, row_end - row_off)


# --------------------------------------------------------------------------
# STAC item parsing
# --------------------------------------------------------------------------


def _scene_from_item(item: dict[str, Any]) -> Scene | None:
    properties = item.get("properties", {}) or {}
    observed = properties.get("datetime")
    if not observed:
        return None

    assets: dict[str, dict[str, Any]] = {}
    for key in (ASSET_NIR, ASSET_SWIR1, ASSET_SWIR2, ASSET_RED, ASSET_SCL):
        asset = item.get("assets", {}).get(key)
        if not asset or not asset.get("href"):
            return None  # an incomplete scene is not usable; skip it entirely
        scale, offset = _scale_offset(asset, properties, is_classification=key == ASSET_SCL)
        assets[key] = {"href": asset["href"], "scale": scale, "offset": offset}

    bbox = item.get("bbox") or [0.0, 0.0, 0.0, 0.0]
    return Scene(
        id=str(item.get("id", "")),
        observed_at=_parse_datetime(observed),
        cloud_cover_pct=float(properties.get("eo:cloud_cover") or 0.0),
        grid_code=str(properties.get("grid:code") or ""),
        assets=assets,
        bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
    )


def _scale_offset(
    asset: dict[str, Any], properties: dict[str, Any], is_classification: bool = False
) -> tuple[float, float]:
    """The reflectance conversion for one band. See the module docstring.

    `earthsearch:boa_offset_applied` outranks the offset declared on the asset,
    because the two describe different things: the flag says whether this file
    already has the shift baked in, while `raster:bands` describes the ESA
    product convention it came from. When the flag is set, the correct offset
    for *this file* is zero regardless of what the band metadata says.

    Defaulting to zero when nothing says otherwise is deliberate. An offset
    wrongly skipped shifts the index; an offset wrongly applied drives
    reflectance negative and puts the result outside its own valid range.
    """
    if is_classification:
        return 1.0, 0.0

    bands = asset.get("raster:bands") or asset.get("bands") or []
    band = (bands[0] or {}) if bands and isinstance(bands, list) else {}
    scale = band.get("scale", band.get("raster:scale"))
    scale = float(scale) if scale is not None else 0.0001

    if properties.get("earthsearch:boa_offset_applied"):
        return scale, 0.0

    offset = band.get("offset", band.get("raster:offset"))
    return scale, float(offset or 0.0)


def _scene_to_cache(scene: Scene) -> dict[str, Any]:
    return {
        "id": scene.id,
        "observed_at": scene.observed_at.isoformat(),
        "cloud_cover_pct": scene.cloud_cover_pct,
        "grid_code": scene.grid_code,
        "assets": scene.assets,
        "bbox": list(scene.bbox),
    }


def _scene_from_cache(entry: dict[str, Any]) -> Scene:
    bbox = entry.get("bbox") or [0.0, 0.0, 0.0, 0.0]
    return Scene(
        id=entry["id"],
        observed_at=_parse_datetime(entry["observed_at"]),
        cloud_cover_pct=float(entry["cloud_cover_pct"]),
        grid_code=entry.get("grid_code", ""),
        assets=entry.get("assets", {}),
        bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
    )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def padded_bbox(
    latitudes: list[float],
    longitudes: list[float],
    pad_km: float = 2.0,
) -> tuple[float, float, float, float] | None:
    """A lon/lat box around some points, padded, and capped at MAX_WINDOW_KM.

    The padding is not cosmetic: a fire's scar always extends past the thermal
    detections that found it, because the satellite only ever caught some of the
    passes. Without the margin we would measure the middle of the burn.
    """
    if not latitudes or not longitudes:
        return None

    mean_latitude = sum(latitudes) / len(latitudes)
    d_lat = pad_km / KM_PER_DEGREE_LAT
    d_lon = d_lat / max(math.cos(math.radians(mean_latitude)), 0.01)

    west, east = min(longitudes) - d_lon, max(longitudes) + d_lon
    south, north = min(latitudes) - d_lat, max(latitudes) + d_lat

    height_km = (north - south) * KM_PER_DEGREE_LAT
    width_km = (east - west) * KM_PER_DEGREE_LAT * math.cos(math.radians(mean_latitude))
    if max(height_km, width_km) > MAX_WINDOW_KM:
        log.info("sentinel2.window_too_large", width_km=round(width_km, 1),
                 height_km=round(height_km, 1))
        return None

    return (west, south, east, north)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def pre_fire_window(first_detected_at: datetime) -> tuple[datetime, datetime]:
    """When to look for a pre-fire image.

    Ends twelve hours *before* the first detection rather than at it: FIRMS
    reports the first pass that saw the fire, not ignition, and a scene from the
    morning of the same day may already show smoke or an early scar.
    """
    end = first_detected_at - timedelta(hours=12)
    return end - timedelta(days=PRE_FIRE_SEARCH_DAYS), end


#: How long after a fire stops burning we will still accept an image of its
#: scar. Long enough to outlast a cloudy fortnight, short enough that the scar
#: has not begun to green over.
POST_FIRE_GRACE_DAYS = 30


def post_fire_window(
    first_detected_at: datetime,
    last_detected_at: datetime | None = None,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """When to look for a post-fire image.

    Starts twelve hours *after the first detection*, not after the last one. A
    fire still burning has no "after", but it does already have a scar on the
    ground, and measuring what has burnt so far beats reporting a pixel count.

    Ends a month after the fire went quiet, and that bound is not optional. An
    unbounded window runs to "now", and since scene selection prefers a clear
    image, a fire from two summers ago picks up last week's image instead of the
    one taken days after it burnt. The scar is still faintly visible, so the
    measurement succeeds and quietly reports two years of regrowth as a light
    burn. Measured on the 2024 Varnavas fire, that error dragged mean dNBR from
    0.43 down to 0.30 and moved the dominant severity class from moderate-low to
    low.
    """
    now = now or datetime.now(UTC)
    start = first_detected_at + timedelta(hours=12)
    end = (last_detected_at or first_detected_at) + timedelta(days=POST_FIRE_GRACE_DAYS)
    return start, min(end, now)
