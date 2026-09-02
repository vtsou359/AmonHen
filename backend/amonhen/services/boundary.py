"""Area-of-interest filtering.

FIRMS is queried with a bounding box, and no rectangle around Greece exists that
does not also contain western Turkey, Albania, North Macedonia and southern
Bulgaria. Left unfiltered, roughly two thirds of the "Greek" operational picture
was foreign fires — clutter the gazetteer could not even name, pushing genuine
Greek incidents down the list.

So the bbox stays (FIRMS needs one) and detections are clipped against an actual
country polygon afterwards. The polygon is built and validated by
`scripts/build_boundary.py`; see that file for why it carries a 3 km coastal
buffer and twelve hand-added islands.

Failure behaviour is deliberate: if the boundary file is missing or unreadable,
this module logs loudly and lets *everything* through. Over-reporting foreign
fires is a nuisance; silently dropping a Greek one is a safety failure.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from amonhen.core.config import settings
from amonhen.core.logging import get_logger

if TYPE_CHECKING:
    from amonhen.domain.entities import Detection

log = get_logger(__name__)


@lru_cache(maxsize=1)
def _prepared_boundary():
    """Load the polygon once and prepare it for fast repeated containment tests.

    `shapely.prepare` builds a spatial index over the polygon's edges, in place.
    Without it, testing thousands of detections against a 67-part MultiPolygon
    is a visible cost on every ingest cycle; with it, it disappears.

    Returns None when no boundary is available, which callers treat as
    "accept everything".
    """
    path = boundary_path()
    if not path.exists():
        log.warning(
            "boundary.missing",
            path=str(path),
            message="No boundary file — accepting all detections in the bbox. "
            "Run `python scripts/build_boundary.py` to enable country filtering.",
        )
        return None

    try:
        import shapely
        from shapely.geometry import shape

        feature = json.loads(path.read_text())
        geometry = shape(feature["geometry"])
        shapely.prepare(geometry)  # in-place; enables the fast vectorised paths
        properties = feature.get("properties", {})
        log.info(
            "boundary.loaded",
            name=properties.get("name"),
            parts=len(getattr(geometry, "geoms", [geometry])),
            buffer_km=properties.get("coastal_buffer_km"),
            margin_km=settings.boundary_margin_km,
        )
        return geometry
    except Exception as exc:  # noqa: BLE001
        log.error("boundary.load_failed", error=str(exc), accepting_all=True)
        return None


def boundary_path() -> Path:
    return settings.data_dir / "boundaries" / f"{settings.boundary_name}.geojson"


def contains(latitude: float, longitude: float) -> bool:
    """Is this point inside the area of interest?

    True when no boundary is loaded — see the module docstring on why the
    failure mode is permissive.
    """
    boundary = _prepared_boundary()
    if boundary is None:
        return True

    import shapely

    if settings.boundary_margin_km > 0:
        return bool(
            shapely.dwithin(boundary, shapely.Point(longitude, latitude), _margin_deg())
        )
    return bool(shapely.contains_xy(boundary, longitude, latitude))


def filter_detections(detections: list[Detection]) -> tuple[list[Detection], int]:
    """Keep only detections inside the area of interest.

    Returns (kept, dropped_count). The count is surfaced at
    /api/v1/system/status so it is visible that filtering is happening and by
    how much — a filter you cannot see is a filter you cannot debug.
    """
    if not settings.restrict_to_boundary:
        return detections, 0

    boundary = _prepared_boundary()
    if boundary is None:
        return detections, 0

    import numpy as np
    import shapely

    # Vectorised: one call into GEOS for the whole batch rather than a Python
    # loop of several thousand individual containment tests.
    lons = np.array([d.longitude for d in detections], dtype=np.float64)
    lats = np.array([d.latitude for d in detections], dtype=np.float64)

    if settings.boundary_margin_km > 0:
        # Fires do not respect borders. A margin keeps detections just outside
        # the country — the Evros and Balkan border fires are the obvious case —
        # rather than discarding something burning 400 m from Greek territory.
        mask = shapely.dwithin(boundary, shapely.points(lons, lats), _margin_deg())
    else:
        mask = shapely.contains_xy(boundary, lons, lats)

    kept = [d for d, inside in zip(detections, mask, strict=True) if inside]
    dropped = len(detections) - len(kept)
    if dropped:
        log.info(
            "boundary.filtered",
            kept=len(kept),
            dropped=dropped,
            area=settings.area_of_interest_name,
        )
    return kept, dropped


def _margin_deg() -> float:
    """Convert the cross-border margin to degrees of latitude.

    Approximate — a degree of longitude is shorter than a degree of latitude in
    Greece — so the margin is effectively a little wider east-west than the
    configured kilometres. For a tolerance band that is fine, and erring wide is
    the safe direction.
    """
    return settings.boundary_margin_km / 111.32


def is_available() -> bool:
    return _prepared_boundary() is not None
