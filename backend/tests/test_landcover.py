"""Tests for CORINE land cover and the fuel model it implies.

This feeds two things that matter: which fuel the spread model uses, and whether
a detection is on ground that can burn at all. Both change what an operator is
told, so the mapping is pinned rather than left to drift.
"""

from __future__ import annotations

import pytest

from amonhen.services.spread import FUEL_MODELS
from amonhen.sources.landcover import (
    FUEL_BY_CORINE,
    NON_BURNABLE,
    LandCover,
    LandCoverSource,
)


def cover(code: str) -> LandCover:
    """Build a LandCover the way the parser would, for a bare CORINE code."""
    return LandCoverSource()._parse(
        {"results": [{"attributes": {"Raster.CODE_18": code, "Raster.LABEL3": f"class {code}"}}]}
    )


def test_every_mapped_fuel_exists_in_the_spread_model():
    """A typo here would silently fall back to the default fuel for a whole
    vegetation class."""
    for code, fuel in FUEL_BY_CORINE.items():
        assert fuel in FUEL_MODELS, f"CORINE {code} maps to unknown fuel {fuel!r}"


def test_burnable_and_non_burnable_do_not_overlap():
    assert not (set(FUEL_BY_CORINE) & set(NON_BURNABLE))


@pytest.mark.parametrize(
    ("code", "expected_fuel"),
    [
        ("323", "maquis"),        # sclerophyllous — the classic Greek fire fuel
        ("324", "maquis"),        # transitional woodland-shrub, post-fire regrowth
        ("312", "pine"),          # Aleppo/Calabrian pine
        ("313", "mixed_forest"),
        ("321", "phrygana"),
        ("211", "agricultural"),
    ],
)
def test_key_greek_classes_map_to_the_right_fuel(code, expected_fuel):
    assert cover(code).fuel == expected_fuel


def test_olive_groves_are_not_treated_as_cropland():
    """Olive groves burn: the understory carries fire between the trees.

    CORINE files them under agriculture, which would otherwise give them the
    slowest fuel model in the set.
    """
    assert cover("223").fuel == "maquis"


def test_cropland_with_natural_vegetation_burns_like_scrub():
    assert cover("243").fuel == "maquis"


@pytest.mark.parametrize("code", ["111", "121", "122", "131", "512", "523"])
def test_unburnable_ground_yields_no_fuel(code):
    """Quarries, docks, roads and open water cannot carry a wildfire.

    This is what independently exposed the Parnitha and Thessaloniki detections
    as industrial: they sit on 131 (quarry) and 121 (industrial).
    """
    result = cover(code)
    assert result.fuel is None
    assert not result.burnable


def test_unknown_code_is_not_guessed():
    """An unrecognised class must not silently become shrubland."""
    result = cover("999")
    assert result.fuel is None


def test_missing_response_returns_unknown_rather_than_a_default():
    result = LandCoverSource()._parse({"results": []})
    assert result.code == ""
    assert result.fuel is None


def test_vector_layer_response_is_also_understood():
    """The service answers with a vector layer, a raster layer, or both."""
    result = LandCoverSource()._parse(
        {"results": [{"value": "312", "attributes": {"Code_18": "312"}}]}
    )
    assert result.code == "312"
    assert result.fuel == "pine"


def test_fixture_declines_to_invent_a_fuel():
    result = LandCoverSource()._load_fixture()[0]
    assert result.fuel is None
    assert "unavailable" in result.source
