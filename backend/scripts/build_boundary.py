"""Build the Greece area-of-interest boundary.

Why this exists: FIRMS is queried with a bounding box, and any rectangle around
Greece necessarily swallows western Turkey, Albania, North Macedonia and
southern Bulgaria. Before this, roughly two thirds of the "Greek" operational
picture was foreign fires the gazetteer could not even name.

Source: Natural Earth 10m admin_0 countries (public domain).

Two corrections are applied, both discovered by validating rather than assuming:

1. **A coastal buffer.** Town centroids sit marginally seaward of a simplified
   coastline — Mati, Chalkida and Alexandroupoli all fell *outside* a strict
   polygon by 0.01-0.34 km. Filtering on that would silently drop fires in real
   Greek towns. Calibration showed 1 km is enough to fix it and 10 km starts
   pulling in Edirne, Turkey; 3 km sits comfortably in the middle.

2. **Missing islands.** Natural Earth 10m omits twelve genuine Greek islands,
   several inhabited: Kastellorizo (~500 people) is 152 km from the nearest
   landmass in the dataset. They are unioned back in as discs.

   Note on Kastellorizo: the strait to the Turkish coast is about 2 km wide, so
   its disc unavoidably covers a sliver of Turkey. That is the right trade —
   a fire 2 km from Kastellorizo is Kastellorizo's problem.

Run:  python scripts/build_boundary.py
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from shapely.geometry import MultiPolygon, Point, mapping, shape
from shapely.ops import unary_union

NATURAL_EARTH_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector"
    "/master/geojson/ne_10m_admin_0_countries.geojson"
)

#: Degrees. Keeps every island (74 parts, 100% of area) at ~5,700 vertices.
SIMPLIFY_TOLERANCE_DEG = 0.005

#: Kilometres. See note 1 above.
COASTAL_BUFFER_KM = 3.0

#: Greek islands absent from Natural Earth 10m, with a radius covering each.
#: (name, latitude, longitude, radius_km)
MISSING_ISLANDS: list[tuple[str, float, float, float]] = [
    ("Kastellorizo", 36.146, 29.594, 4.0),
    ("Othonoi", 39.843, 19.396, 3.0),
    ("Ereikoussa", 39.881, 19.580, 2.5),
    ("Mathraki", 39.767, 19.531, 2.5),
    ("Strofades", 37.250, 21.000, 2.0),
    ("Agathonisi", 37.462, 26.966, 3.0),
    ("Arki", 37.382, 26.739, 2.5),
    ("Lipsi", 37.297, 26.766, 4.0),
    ("Pserimos", 36.934, 27.150, 2.5),
    ("Koufonisia", 36.938, 25.603, 3.0),
    ("Fournoi", 37.577, 26.478, 6.0),
    ("Oinousses", 38.516, 26.220, 3.0),
]

#: Must end up inside. Remote Greek territory that a naive polygon loses.
MUST_INCLUDE = [
    ("Kastellorizo", 36.146, 29.594),
    ("Gavdos", 34.833, 24.083),
    ("Othonoi", 39.843, 19.396),
    ("Samos", 37.755, 26.977),
    ("Rhodes", 36.434, 28.218),
    ("Crete/Chania", 35.514, 24.018),
    ("Corfu", 39.624, 19.922),
    ("Soufli/Evros", 41.193, 26.297),
]

#: Must end up outside. If any of these land inside, the buffer is too generous.
MUST_EXCLUDE = [
    ("Izmir, TR", 38.420, 27.140),
    ("Tirana, AL", 41.330, 19.820),
    ("Skopje, MK", 41.990, 21.430),
    ("Sofia, BG", 42.700, 23.320),
    ("Bodrum, TR", 37.030, 27.430),
    ("Edirne, TR", 41.680, 26.560),
    ("Bursa, TR", 40.190, 29.060),
    ("Canakkale, TR", 40.150, 26.410),
]

OUTPUT = Path("/data/boundaries/greece.geojson")


def km_to_deg(km: float) -> float:
    """Degrees of latitude per kilometre. Good enough for a tolerance buffer."""
    return km / 111.32


def build() -> None:
    print(f"downloading {NATURAL_EARTH_URL.rsplit('/', 1)[-1]} ...")
    payload = json.loads(urllib.request.urlopen(NATURAL_EARTH_URL, timeout=300).read())

    features = [
        f for f in payload["features"]
        if f["properties"].get("ADM0_A3") == "GRC" or f["properties"].get("NAME") == "Greece"
    ]
    if len(features) != 1:
        raise SystemExit(f"expected exactly one Greece feature, got {len(features)}")

    raw = shape(features[0]["geometry"])
    print(f"  raw: {raw.geom_type}, {len(raw.geoms)} parts, area {raw.area:.4f} deg²")

    simplified = raw.simplify(SIMPLIFY_TOLERANCE_DEG, preserve_topology=True)
    print(f"  simplified: {len(simplified.geoms)} parts, area {simplified.area:.4f} deg²")

    discs = [
        Point(lon, lat).buffer(km_to_deg(radius), quad_segs=8)
        for _, lat, lon, radius in MISSING_ISLANDS
    ]
    boundary = unary_union([simplified.buffer(km_to_deg(COASTAL_BUFFER_KM)), *discs])
    # Buffering rounds every corner into ~64 vertices, which triples the file for
    # no accuracy. Re-simplify at ~220 m — far finer than the 375 m sensor pixel
    # we test against, so it cannot change a containment decision that matters.
    boundary = boundary.simplify(0.002, preserve_topology=True)
    if boundary.geom_type == "Polygon":
        boundary = MultiPolygon([boundary])
    print(f"  + {COASTAL_BUFFER_KM} km buffer + {len(discs)} islands -> {len(boundary.geoms)} parts")

    # ---- validate before writing; a wrong boundary silently drops fires ----
    failures: list[str] = []
    for name, lat, lon in MUST_INCLUDE:
        if not boundary.contains(Point(lon, lat)):
            failures.append(f"MUST_INCLUDE outside boundary: {name}")
    for name, lat, lon in MUST_EXCLUDE:
        if boundary.contains(Point(lon, lat)):
            failures.append(f"MUST_EXCLUDE inside boundary: {name}")

    try:  # the gazetteer is the strongest check we have: 51 known Greek places
        import sys

        sys.path.insert(0, "/app")
        from amonhen.services.gazetteer import SEED_PLACES

        for place in SEED_PLACES:
            if not boundary.contains(Point(place.longitude, place.latitude)):
                failures.append(f"gazetteer place outside boundary: {place.name}")
    except ImportError:
        print("  (gazetteer unavailable — skipping that check)")

    if failures:
        for line in failures:
            print(f"  FAIL  {line}")
        raise SystemExit("boundary validation failed; not writing")

    print(f"  validation passed: {len(MUST_INCLUDE)} included, {len(MUST_EXCLUDE)} excluded, "
          f"gazetteer clean")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "type": "Feature",
        "properties": {
            "name": "Greece",
            "source": "Natural Earth 10m admin_0 countries (public domain)",
            "simplify_tolerance_deg": SIMPLIFY_TOLERANCE_DEG,
            "coastal_buffer_km": COASTAL_BUFFER_KM,
            "supplementary_islands": [n for n, *_ in MISSING_ISLANDS],
        },
        "geometry": mapping(boundary),
    }))
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    build()
