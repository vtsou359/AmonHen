"""Land cover, and the fuel model it implies.

Until now the spread model assumed one uniform vegetation type — "maquis" —
for the whole country, using coefficients derived for Canadian boreal forest.
That was the single largest source of error in every projection: an Aleppo pine
stand and a ploughed field were treated identically.

Source: **CORINE Land Cover 2018**, the European reference dataset, served by the
European Environment Agency. Queried through the ArcGIS REST `identify`
endpoint, which — unlike WMS `GetFeatureInfo`, which returns nothing for the
EFFIS raster layers — gives a real class code for a real point, keyless, as JSON.
No new dependency and no raster processing.

A trap worth recording, because it silently produced plausible nonsense: ArcGIS
computes `identify` tolerance in *screen pixels*, derived from `mapExtent` and
`imageDisplay`. Passing a degenerate extent (the same point as both corners)
makes that pixel size meaningless, and the service answers with whatever polygon
is vaguely nearby. Parnitha National Park came back as "Continuous urban fabric"
that way. With a real extent it correctly returns coniferous forest. The extent
below is not decoration.

Resolution is 100 m with a 25 ha minimum mapping unit, and the vintage is 2018.
So this is the *landscape*, not this season's crop: good enough to tell pine from
pasture, not good enough to know a field was harvested last week.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from amonhen.core.logging import get_logger
from amonhen.sources.base import DataSource

log = get_logger(__name__)

EEA_IDENTIFY = (
    "https://image.discomap.eea.europa.eu/arcgis/rest/services"
    "/Corine/CLC2018_WM/MapServer/identify"
)

#: Half-width of the query window, in degrees (~1 km). See the docstring: this
#: has to be a real extent, not a point.
QUERY_HALF_WIDTH_DEG = 0.01

#: CORINE level-3 code -> one of our fuel models. Codes not listed here are
#: treated as non-burnable.
#:
#: The Mediterranean-specific calls, which are the ones that matter for Greece:
#:   323 sclerophyllous vegetation  = maquis. The classic Greek fire fuel.
#:   324 transitional woodland-shrub = maquis. Regrowth after previous fires,
#:       and some of the most dangerous ground there is.
#:   312 coniferous forest = Aleppo/Calabrian pine, which crowns and spots.
#:   223 olive groves burn readily despite being "agriculture" — the understory
#:       carries fire between trees, so they are not treated as cropland.
FUEL_BY_CORINE: dict[str, str] = {
    # --- shrubland: the dominant Greek fire fuel -------------------------
    "323": "maquis",
    "324": "maquis",
    "322": "phrygana",
    "321": "phrygana",
    "333": "phrygana",          # sparsely vegetated
    # --- forest ----------------------------------------------------------
    "312": "pine",
    "313": "mixed_forest",
    "311": "mixed_forest",
    "244": "mixed_forest",      # agro-forestry
    # --- agriculture ------------------------------------------------------
    "211": "agricultural",
    "212": "agricultural",
    "213": "agricultural",
    "221": "agricultural",
    "222": "agricultural",
    "223": "maquis",            # olive groves — see note above
    "231": "phrygana",          # pasture behaves like grass
    "241": "agricultural",
    "242": "agricultural",
    "243": "maquis",            # cropland mixed with natural vegetation
    # --- managed green space ----------------------------------------------
    "141": "phrygana",
    "142": "phrygana",
}

#: Ground that cannot carry a wildfire. Useful twice over: it stops us
#: projecting a fire across a bay, and a "fire" detected here is strong evidence
#: the heat source is industrial — the Parnitha detections sit on 131, a quarry.
NON_BURNABLE: dict[str, str] = {
    "111": "dense urban",
    "112": "urban",
    "121": "industrial or commercial",
    "122": "roads and railways",
    "123": "port",
    "124": "airport",
    "131": "quarry or mine",
    "132": "dump site",
    "133": "construction site",
    "331": "beach or dune",
    "332": "bare rock",
    "334": "burnt area",
    "335": "glacier",
    "411": "inland marsh",
    "412": "peat bog",
    "421": "salt marsh",
    "422": "salt pan",
    "423": "tidal flat",
    "511": "watercourse",
    "512": "water body",
    "521": "coastal lagoon",
    "522": "estuary",
    "523": "sea",
}


@dataclass(frozen=True)
class LandCover:
    """What is on the ground at a point, and how it burns."""

    code: str
    label: str
    #: One of the models in `services.spread.FUEL_MODELS`, or None if nothing
    #: here can carry a fire.
    fuel: str | None
    source: str

    @property
    def burnable(self) -> bool:
        return self.fuel is not None

    @property
    def descriptor(self) -> str:
        """Plain words for the UI."""
        return self.label.lower()


UNKNOWN = LandCover(code="", label="Unknown", fuel=None, source="unavailable")


class LandCoverSource(DataSource[LandCover]):
    name = "corine_land_cover"
    attribution = "CORINE Land Cover 2018 — Copernicus / EEA"
    homepage = "https://land.copernicus.eu/pan-european/corine-land-cover"
    #: CORINE is published every six years. Cache it for a month; the only
    #: reason this is not permanent is so a bad entry eventually expires.
    cache_ttl_seconds = 60 * 60 * 24 * 30
    requires_credentials = False

    async def _fetch_live(
        self, latitude: float = 0.0, longitude: float = 0.0, **_: Any
    ) -> list[LandCover]:
        cache_key = f"{latitude:.4f},{longitude:.4f}"
        cached = self._cache_read(cache_key)
        if cached is not None:
            return [LandCover(**cached)]

        half = QUERY_HALF_WIDTH_DEG
        response = await self._get(
            EEA_IDENTIFY,
            params={
                "geometry": (
                    f'{{"x":{longitude},"y":{latitude},'
                    f'"spatialReference":{{"wkid":4326}}}}'
                ),
                "geometryType": "esriGeometryPoint",
                "sr": "4326",
                "tolerance": "1",
                # A real extent, not a point. See the module docstring.
                "mapExtent": (
                    f"{longitude - half},{latitude - half},"
                    f"{longitude + half},{latitude + half}"
                ),
                "imageDisplay": "500,500,96",
                "returnGeometry": "false",
                "f": "json",
            },
        )
        cover = self._parse(response.json())
        if cover.code:
            self._cache_write(cache_key, cover.__dict__)
        return [cover]

    def _parse(self, payload: dict[str, Any]) -> LandCover:
        """Pull the class code out of whichever layer answered.

        The service replies with both a vector layer (carrying `Code_18`) and a
        raster layer (carrying `Raster.CODE_18` plus a human label). Either is
        acceptable; the raster one is preferred because it comes with the label.
        """
        code = ""
        label = ""
        for result in payload.get("results", []):
            attributes = result.get("attributes", {}) or {}
            if attributes.get("Raster.CODE_18"):
                code = str(attributes["Raster.CODE_18"])
                label = str(attributes.get("Raster.LABEL3") or "")
                break
            if not code:
                candidate = str(attributes.get("Code_18") or result.get("value") or "")
                if candidate.isdigit():
                    code = candidate

        if not code:
            return UNKNOWN

        fuel = FUEL_BY_CORINE.get(code)
        if not label:
            label = NON_BURNABLE.get(code) or _FUEL_LABELS.get(fuel or "", "Vegetation")
        return LandCover(code=code, label=label.capitalize(), fuel=fuel, source=self.attribution)

    def _load_fixture(self, **_: Any) -> list[LandCover]:
        """No guess.

        Inventing a fuel type would silently change every projection. Returning
        "unknown" makes the caller fall back to its documented default and say so.
        """
        return [UNKNOWN]

    async def cover_at(self, latitude: float, longitude: float) -> LandCover:
        results = await self.fetch(latitude=latitude, longitude=longitude)
        return results[0] if results else UNKNOWN


_FUEL_LABELS = {
    "maquis": "Shrubland",
    "phrygana": "Low scrub or grass",
    "pine": "Pine forest",
    "mixed_forest": "Mixed forest",
    "agricultural": "Farmland",
}
