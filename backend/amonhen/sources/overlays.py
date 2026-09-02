"""Map overlays: what can be drawn on top of the basemap.

A catalogue, not a client. It says which layers exist, what each one is *for*,
and how to build a tile URL. The frontend renders them; the backend owns the
definitions so a layer can be added, retired or re-described without shipping
frontend code.

Two sources, both public and keyless:

* **Copernicus EMS — EFFIS/GWIS**, the European operational fire service. Its
  WMS sends `Access-Control-Allow-Origin: *` and caches for an hour, so the
  browser fetches tiles directly: no proxy, no storage, no backend load.
* **Esri World Hillshade**, for terrain. Now that slope drives the projections,
  being able to *see* the hills explains why a fire is heading where it is.

Every entry here was verified to return real pixels over Greece. Notes on what
is deliberately absent:

* **Time-aware layers must be given a date.** EFFIS defaults them to 2019-01-01
  and returns a valid, empty PNG — a failure that looks exactly like a broken
  layer. `{time}` is mandatory in those templates.
* **Burnt-area polygons (`nrt.ba.*`) and the GFAS smoke layers are not listed.**
  Both render empty for every date tried, including over the 2023 Evros
  mega-fire. A layer that silently shows nothing is worse than no layer.

Wording rule for this file: titles and descriptions are read by people who are
not fire scientists. Say what the layer *tells you*, not what it is called in
the literature. The technical name belongs in `technical_name`, for the reader
who wants it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

EFFIS_WMS = "https://maps.effis.emergency.copernicus.eu/gwis"
EFFIS_ATTRIBUTION = "Copernicus EMS — EFFIS / GWIS"
ESRI_ATTRIBUTION = "Esri, USGS, NOAA"

Category = Literal["danger", "behaviour", "fuel", "exposure", "detections", "terrain"]


@dataclass(frozen=True)
class Overlay:
    id: str
    title: str
    category: Category
    #: One plain line: what you learn by turning this on.
    description: str
    #: Why an analyst would want it — the decision it supports.
    why: str
    source: str
    attribution: str
    time_aware: bool = False
    default_opacity: float = 0.55
    #: EFFIS WMS layer name, when this is an EFFIS layer.
    layer: str = ""
    #: A ready-made tile template, for sources that are not EFFIS WMS.
    tile_template: str = ""
    #: The formal name, for anyone who wants to look it up.
    technical_name: str = ""

    def tile_url(self) -> str:
        if self.tile_template:
            return self.tile_template
        params = [
            "service=WMS", "version=1.3.0", "request=GetMap",
            f"layers={self.layer}", "styles=", "crs=EPSG:3857",
            "bbox={bbox-epsg-3857}", "width=256", "height=256",
            "format=image/png", "transparent=true",
        ]
        if self.time_aware:
            params.append("time={time}")
        return f"{EFFIS_WMS}?{'&'.join(params)}"

    def legend_url(self) -> str:
        """The layer's own colour key.

        `sld_version` is not optional: without it EFFIS answers
        GetLegendGraphic with an exception instead of an image. Non-EFFIS
        overlays have no legend service, and return empty.
        """
        if not self.layer:
            return ""
        return (
            f"{EFFIS_WMS}?service=WMS&version=1.3.0&sld_version=1.1.0"
            f"&request=GetLegendGraphic&layer={self.layer}&format=image/png"
        )

    def as_dict(self) -> dict:
        return {**asdict(self), "tile_url": self.tile_url(), "legend_url": self.legend_url()}


OVERLAYS: tuple[Overlay, ...] = (
    Overlay(
        id="effis_fwi",
        title="Fire danger today",
        category="danger",
        description="The official European fire danger rating for today.",
        why=(
            "The number Europe's fire services work from. Compare it with the danger "
            "rating Amon Hen works out for each fire — if they disagree sharply, one "
            "of the two weather feeds is wrong and it is worth knowing which."
        ),
        technical_name="Fire Weather Index (ECMWF)",
        layer="ecmwf.fwi", time_aware=True, default_opacity=0.55,
        source="Copernicus EFFIS", attribution=EFFIS_ATTRIBUTION,
    ),
    Overlay(
        id="effis_fwi_anomaly",
        title="Unusual for the time of year",
        category="danger",
        description="How today's danger compares with a normal day at this date.",
        why=(
            "Turns a number into a judgement. High danger in Attica in August is an "
            "ordinary bad day; the same reading in May is not. This is the layer that "
            "tells you which one you are looking at."
        ),
        technical_name="FWI anomaly vs climatology",
        layer="ecmwf.anomaly", time_aware=True, default_opacity=0.55,
        source="Copernicus EFFIS", attribution=EFFIS_ATTRIBUTION,
    ),
    Overlay(
        id="effis_drought",
        title="How dry the ground is",
        category="danger",
        description="Dryness deep in the soil and heavy fuels, built up over weeks.",
        why=(
            "The slow-moving number that separates a fire from an unstoppable one. It "
            "changes over weeks, so it tells you where to place crews and aircraft "
            "before anything starts — not where to send them today."
        ),
        technical_name="Drought Code (FWI system)",
        layer="ecmwf.dc", time_aware=True, default_opacity=0.55,
        source="Copernicus EFFIS", attribution=EFFIS_ATTRIBUTION,
    ),
    Overlay(
        id="effis_isi",
        title="How fast fire would spread",
        category="behaviour",
        description="Expected speed of a fire front, from wind and how dry fine fuels are.",
        why="The wind-and-dryness half of spread, mapped across the country.",
        technical_name="Initial Spread Index",
        layer="ecmwf.isi", time_aware=True, default_opacity=0.55,
        source="Copernicus EFFIS", attribution=EFFIS_ATTRIBUTION,
    ),
    Overlay(
        id="effis_ros",
        title="Spread speed — second opinion",
        category="behaviour",
        description="An independent model's estimate of how fast a fire front moves.",
        why=(
            "A cross-check on the speed Amon Hen puts in every fire's summary. Ours "
            "uses the Canadian system; this is the Australian one on European weather. "
            "Where they agree, trust the rough size of the number. Where they differ, "
            "trust neither and look at the ground."
        ),
        technical_name="McArthur Mk5 rate of spread",
        layer="ecmwf.mark5.ros", time_aware=True, default_opacity=0.6,
        source="Copernicus EFFIS", attribution=EFFIS_ATTRIBUTION,
    ),
    Overlay(
        id="terrain_hillshade",
        title="Hills and valleys",
        category="terrain",
        description="Shaded relief — where the ground rises and falls.",
        why=(
            "Fire runs uphill far faster than along the flat, so terrain often explains "
            "a projection's shape better than the wind does. Amon Hen now measures "
            "slope from satellite elevation data; this is that same ground, drawn."
        ),
        technical_name="Esri World Hillshade",
        tile_template=(
            "https://services.arcgisonline.com/arcgis/rest/services/Elevation/"
            "World_Hillshade/MapServer/tile/{z}/{y}/{x}"
        ),
        # Higher than the others on purpose: hillshade is a subtle grey wash, and
        # at 45% over a dark basemap enabling it looks like nothing happened.
        default_opacity=0.7,
        source="Esri World Hillshade", attribution=ESRI_ATTRIBUTION,
    ),
    Overlay(
        id="effis_fuel",
        title="What would burn",
        category="fuel",
        description="The type of vegetation on the ground: scrub, forest, farmland.",
        why=(
            "Amon Hen assumes one vegetation type for the whole country, which is the "
            "biggest single source of error in its spread estimates. This shows what is "
            "actually there, so you can see where that assumption breaks."
        ),
        technical_name="GWIS global fuel map",
        layer="fuel_map", default_opacity=0.5,
        source="Copernicus EFFIS", attribution=EFFIS_ATTRIBUTION,
    ),
    Overlay(
        id="effis_landcover",
        title="Land cover",
        category="fuel",
        description="Broad ground cover: forest, cropland, urban, water.",
        why="Coarser than the vegetation layer, but current, and easier to read at a glance.",
        technical_name="MODIS MCD12 land cover 2024",
        layer="landcover.mcd12.2024", default_opacity=0.5,
        source="Copernicus EFFIS", attribution=EFFIS_ATTRIBUTION,
    ),
    Overlay(
        id="effis_builtup",
        title="Towns and buildings",
        category="exposure",
        description="Where people and buildings actually are.",
        why=(
            "Far more detailed than Amon Hen's list of about fifty places. Use it to "
            "check an 'at risk' list that looks suspiciously short."
        ),
        technical_name="Global Human Settlement Layer",
        layer="ghsl", default_opacity=0.55,
        source="Copernicus EFFIS", attribution=EFFIS_ATTRIBUTION,
    ),
    Overlay(
        id="effis_protected",
        title="Nature reserves",
        category="exposure",
        description="Protected land — national parks and Natura 2000 sites.",
        why="What is at stake ecologically, not just in buildings.",
        technical_name="World Database on Protected Areas",
        layer="wdpa.poly_0_03", default_opacity=0.45,
        source="Copernicus EFFIS", attribution=EFFIS_ATTRIBUTION,
    ),
    Overlay(
        id="effis_viirs",
        title="Heat spots — European feed",
        category="detections",
        description="Europe's own view of satellite heat detections.",
        why=(
            "A second pair of eyes on our own data. If Europe shows a hot spot where "
            "Amon Hen has no fire, something in our filtering or grouping dropped it."
        ),
        technical_name="EFFIS VIIRS hotspots",
        layer="viirs.hs", time_aware=True, default_opacity=0.85,
        source="Copernicus EFFIS", attribution=EFFIS_ATTRIBUTION,
    ),
)


def current_layer_date() -> str:
    """Which date to request for time-aware layers.

    EFFIS publishes forecast fire-danger fields, so today's data exists from the
    start of the day and today is the right answer. It is computed here rather
    than in the browser so that when that stops being true — a publishing delay,
    a change of product — there is one place to fix, and every client picks the
    fix up without being redeployed.
    """
    from datetime import UTC, datetime

    return datetime.now(UTC).date().isoformat()


def catalogue() -> list[dict]:
    return [overlay.as_dict() for overlay in OVERLAYS]


ATTRIBUTION = f"{EFFIS_ATTRIBUTION} · {ESRI_ATTRIBUTION}"
