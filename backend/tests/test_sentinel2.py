"""Tests for the Sentinel-2 connector.

These run whether or not the `[eo]` extra is installed — everything here is
metadata parsing and scene selection, which is where the mistakes that produce
*plausible* wrong answers live. The pixel arithmetic is guarded separately and
skipped without rasterio.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from amonhen.sources.sentinel2 import (
    ASSET_NIR,
    ASSET_RED,
    ASSET_SCL,
    ASSET_SWIR1,
    ASSET_SWIR2,
    MAX_WINDOW_KM,
    Scene,
    Sentinel2Source,
    _scale_offset,
    _scene_from_item,
    padded_bbox,
    post_fire_window,
    pre_fire_window,
)


def item(**overrides) -> dict:
    """A STAC item shaped like the ones Earth Search actually returns."""
    assets = {
        key: {
            "href": f"https://example.invalid/{key}.tif",
            "raster:bands": [{"scale": 0.0001, "offset": -0.1, "nodata": 0}],
        }
        for key in (ASSET_NIR, ASSET_SWIR1, ASSET_SWIR2, ASSET_RED, ASSET_SCL)
    }
    payload = {
        "id": "S2B_34SGH_20250827_0_L2A",
        "bbox": [23.0, 37.8, 24.2, 38.9],
        "assets": assets,
        "properties": {
            "datetime": "2025-08-27T09:19:52.943000Z",
            "eo:cloud_cover": 7.1,
            "grid:code": "MGRS-34SGH",
            "s2:processing_baseline": "05.11",
        },
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------
# The reflectance offset. Applying the declared one is the bug, not the fix.
# --------------------------------------------------------------------------


def test_a_declared_offset_is_ignored_when_it_is_already_applied():
    """`raster:bands` declares offset -0.1 and the STAC convention reads that as
    "apply this". Earth Search has already done so, and says so in a different
    field. Applying it twice drives dark pixels to negative reflectance."""
    properties = {**item()["properties"], "earthsearch:boa_offset_applied": True}
    assert _scale_offset(item()["assets"][ASSET_NIR], properties) == (0.0001, 0.0)


def test_a_declared_offset_is_honoured_when_it_is_not_already_applied():
    scale, offset = _scale_offset(
        item()["assets"][ASSET_NIR],
        {**item()["properties"], "earthsearch:boa_offset_applied": False},
    )
    assert (scale, offset) == (0.0001, -0.1)


def test_pre_baseline_products_declare_no_offset_at_all():
    """Anything before baseline 04.00 (January 2022) never carried the shift."""
    old_asset = {"href": "x", "raster:bands": [{"scale": 0.0001, "offset": 0}]}
    properties = {"s2:processing_baseline": "03.01", "earthsearch:boa_offset_applied": False}
    assert _scale_offset(old_asset, properties) == (0.0001, 0.0)


def test_the_default_is_no_offset():
    """When nothing says otherwise, skip it. A missed offset shifts the index;
    a spurious one puts reflectance below zero and the index outside [-1, 1],
    which is not a worse reading but an impossible one."""
    assert _scale_offset({"href": "x"}, {})[1] == 0.0


def test_classification_band_is_never_scaled():
    """SCL values are category codes. Multiplying them by 0.0001 would turn
    'cloud shadow' into 0.0003 and mask nothing at all."""
    assert _scale_offset(item()["assets"][ASSET_SCL], {}, is_classification=True) == (1.0, 0.0)


def test_wrongly_applying_the_offset_produces_impossible_reflectance():
    """A regression guard on the measurement that settled this.

    These are the real median digital numbers over Parnitha in August 2026.
    Applying the declared offset puts red reflectance below zero, and the
    scale-only figures are the ones that match a 2021 scene of the same ground
    from before the baseline changed (red 0.0775, NIR 0.2683).
    """
    dn_red, dn_nir = 662.0, 2909.0
    assert dn_red * 0.0001 + -0.1 < 0.0
    assert dn_red * 0.0001 == pytest.approx(0.0662)
    assert dn_nir * 0.0001 == pytest.approx(0.2909)

    # And the NDVI each convention implies: one is possible, one is not.
    def ndvi(nir, red):
        return (nir - red) / (nir + red)

    assert 0.0 < ndvi(0.2909, 0.0662) < 1.0
    assert ndvi(0.1909, -0.0338) > 1.0


# --------------------------------------------------------------------------
# Scene parsing
# --------------------------------------------------------------------------


def test_scene_is_parsed_from_a_stac_item():
    scene = _scene_from_item(item())
    assert scene is not None
    assert scene.grid_code == "MGRS-34SGH"
    assert scene.cloud_cover_pct == pytest.approx(7.1)
    assert scene.observed_at.tzinfo is not None
    assert scene.href(ASSET_SWIR2).endswith("swir22.tif")


def test_a_scene_missing_a_band_is_skipped_entirely():
    """Half a scene is worse than no scene: NBR needs both bands, and a partial
    read would silently compare a band against nothing."""
    broken = item()
    del broken["assets"][ASSET_SWIR2]
    assert _scene_from_item(broken) is None


def test_scene_containment():
    scene = _scene_from_item(item())
    assert scene is not None
    assert scene.contains((23.5, 38.0, 23.6, 38.1))
    assert not scene.contains((22.0, 38.0, 23.6, 38.1))


# --------------------------------------------------------------------------
# Scene selection
# --------------------------------------------------------------------------


def scene(days_ago: int, cloud: float, grid: str = "MGRS-34SGH") -> Scene:
    return Scene(
        id=f"S2_{days_ago}d_{cloud:.0f}pct",
        observed_at=datetime(2025, 8, 27, tzinfo=UTC) - timedelta(days=days_ago),
        cloud_cover_pct=cloud,
        grid_code=grid,
        assets={},
        bbox=(23.0, 37.8, 24.2, 38.9),
    )


async def pick(scenes: list[Scene], **kwargs) -> Scene | None:
    source = Sentinel2Source()
    source.fetch = lambda **_: _as_list(scenes)  # type: ignore[method-assign]
    return await source.best_scene(
        (23.5, 38.0, 23.6, 38.1),
        datetime(2025, 8, 1, tzinfo=UTC),
        datetime(2025, 8, 28, tzinfo=UTC),
        **kwargs,
    )


async def _as_list(scenes: list[Scene]) -> list[Scene]:
    return scenes


async def test_a_clear_older_scene_beats_a_cloudy_fresh_one():
    """Recency is the tiebreak, not the criterion. A pass yesterday under 60%
    cloud measures nothing; a clear one from last week measures the scar."""
    chosen = await pick([scene(days_ago=1, cloud=38.0), scene(days_ago=6, cloud=2.0)])
    assert chosen is not None and chosen.cloud_cover_pct == 2.0


async def test_recency_breaks_ties_within_a_cloud_band():
    """Splitting hairs between 3% and 7% cloud is meaningless, so within a 10%
    band the newer scene wins."""
    chosen = await pick([scene(days_ago=8, cloud=3.0), scene(days_ago=2, cloud=7.0)])
    assert chosen is not None and chosen.observed_at.day == 25


async def test_scenes_over_the_cloud_limit_are_rejected():
    assert await pick([scene(days_ago=1, cloud=95.0)]) is None


async def test_the_pre_fire_scene_is_constrained_to_one_map_tile():
    """Greek fires near 24 degrees east match two MGRS tiles, in different UTM
    zones. Differencing across them measures grid misalignment, not fire."""
    chosen = await pick(
        [scene(days_ago=2, cloud=1.0, grid="MGRS-35SKC"), scene(days_ago=9, cloud=4.0)],
        grid_code="MGRS-34SGH",
    )
    assert chosen is not None and chosen.grid_code == "MGRS-34SGH"


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------


def test_padded_bbox_surrounds_the_points():
    box = padded_bbox([38.0, 38.02], [23.7, 23.72], pad_km=2.0)
    assert box is not None
    west, south, east, north = box
    assert west < 23.7 and east > 23.72
    assert south < 38.0 and north > 38.02


def test_a_scattered_cluster_is_refused_rather_than_read():
    """Past this size we are not looking at one fire, and the read would be slow
    and the answer meaningless."""
    span = MAX_WINDOW_KM / 111.32 * 2
    assert padded_bbox([38.0, 38.0 + span], [23.7, 23.7], pad_km=2.0) is None


def test_padded_bbox_of_nothing_is_nothing():
    assert padded_bbox([], [], pad_km=2.0) is None


def test_the_pre_fire_window_ends_before_the_first_detection():
    """FIRMS reports the first pass that *saw* the fire, not ignition. An image
    from that morning may already show smoke or an early scar."""
    first = datetime(2025, 8, 20, 12, tzinfo=UTC)
    start, end = pre_fire_window(first)
    assert end < first
    assert (first - end) >= timedelta(hours=12)
    assert start < end


def test_the_post_fire_window_starts_from_ignition_not_containment():
    """A fire still burning has no 'after', but it does already have a scar —
    and measuring what has burnt so far beats reporting a pixel count."""
    first = datetime(2025, 8, 20, 12, tzinfo=UTC)
    now = datetime(2025, 8, 25, tzinfo=UTC)
    start, end = post_fire_window(first, now=now)
    assert start == first + timedelta(hours=12)
    assert end == now
