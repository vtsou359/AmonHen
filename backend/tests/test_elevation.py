"""Tests for terrain derivation from the Copernicus DEM.

The plane fit turns 25 elevation samples into a slope and an aspect that then
shape every projection, so getting the sign or the axis wrong would quietly
push fires in the wrong direction. These pin the geometry.
"""

from __future__ import annotations

import math

import pytest

from amonhen.sources.elevation import (
    GRID_RADIUS,
    SAMPLE_SPACING_M,
    ElevationSource,
    Terrain,
    _fit_plane,
    _sample_grid,
)

SIZE = 2 * GRID_RADIUS + 1


def synthetic(dz_dx: float, dz_dy: float, base: float = 500.0) -> list[float]:
    """Elevations on a perfect plane rising by the given gradients (m per m)."""
    return [
        base + col * SAMPLE_SPACING_M * dz_dx + row * SAMPLE_SPACING_M * dz_dy
        for row in range(-GRID_RADIUS, GRID_RADIUS + 1)
        for col in range(-GRID_RADIUS, GRID_RADIUS + 1)
    ]


def test_grid_is_square_and_centred():
    lats, lons = _sample_grid(38.0, 23.0)
    assert len(lats) == len(lons) == SIZE * SIZE
    assert lats[len(lats) // 2] == pytest.approx(38.0)
    assert lons[len(lons) // 2] == pytest.approx(23.0)


def test_flat_ground_has_no_slope():
    terrain = _fit_plane(38.0, 23.0, [400.0] * (SIZE * SIZE), source="test")
    assert terrain.slope_pct == pytest.approx(0.0)
    assert terrain.relief_m == pytest.approx(0.0)
    assert terrain.descriptor == "flat"


def test_slope_magnitude_matches_the_plane():
    """A 20% grade in must give 20% out."""
    terrain = _fit_plane(38.0, 23.0, synthetic(dz_dx=0.0, dz_dy=0.20), source="test")
    assert terrain.slope_pct == pytest.approx(20.0, abs=0.1)


@pytest.mark.parametrize(
    ("dz_dx", "dz_dy", "expected_aspect"),
    [
        (0.0, 0.10, 0.0),     # rises to the north
        (0.10, 0.0, 90.0),    # rises to the east
        (0.0, -0.10, 180.0),  # rises to the south
        (-0.10, 0.0, 270.0),  # rises to the west
    ],
)
def test_aspect_points_uphill_on_a_compass_bearing(dz_dx, dz_dy, expected_aspect):
    """Aspect is the direction of steepest *ascent*, clockwise from north.

    Getting this axis or sign wrong would send the terrain-driven scenario down
    the hill instead of up it, which is exactly the mistake that would look
    plausible on a map.
    """
    terrain = _fit_plane(38.0, 23.0, synthetic(dz_dx, dz_dy), source="test")
    assert terrain.aspect_deg == pytest.approx(expected_aspect, abs=0.5)


def test_slope_toward_is_the_cosine_component():
    terrain = _fit_plane(38.0, 23.0, synthetic(0.0, 0.30), source="test")  # uphill north

    assert terrain.slope_toward(0.0) == pytest.approx(terrain.slope_pct, abs=0.1)
    assert terrain.slope_toward(180.0) == pytest.approx(-terrain.slope_pct, abs=0.1)
    assert terrain.slope_toward(90.0) == pytest.approx(0.0, abs=0.1)
    assert terrain.slope_toward(45.0) == pytest.approx(
        terrain.slope_pct * math.cos(math.radians(45)), abs=0.2
    )


def test_plane_fit_shrugs_off_one_bad_sample():
    """A single spurious DEM cell must not swing the answer.

    This is why it is a least-squares fit over 25 points rather than a central
    difference over 4.
    """
    clean = synthetic(0.0, 0.10)
    noisy = list(clean)
    noisy[0] += 300.0  # one wildly wrong corner

    a = _fit_plane(38.0, 23.0, clean, source="test")
    b = _fit_plane(38.0, 23.0, noisy, source="test")
    assert b.slope_pct == pytest.approx(a.slope_pct, rel=0.35)


@pytest.mark.parametrize(
    ("slope", "word"),
    [(2, "flat"), (10, "gently sloping"), (20, "hilly"), (40, "steep"), (70, "very steep")],
)
def test_descriptor_is_plain_language(slope, word):
    terrain = Terrain(38.0, 23.0, 500.0, slope, 0.0, 100.0, 25, "test")
    assert terrain.descriptor == word


def test_fixture_declares_itself_as_flat_rather_than_guessing():
    """With no DEM available we must say so, not invent a slope."""
    terrain = ElevationSource()._load_fixture(latitude=38.0, longitude=23.0)[0]
    assert terrain.slope_pct == 0.0
    assert terrain.sample_count == 0
    assert "unavailable" in terrain.source
