"""Terrain, from the Copernicus DEM.

This closed the largest accuracy gap the platform had. `estimate_spread()`
accepted a slope from the beginning and nothing supplied one, so every
projection ran on imaginary flat ground — while fire runs uphill roughly
exponentially with grade. In Greek terrain that is routinely a factor of two,
which is the difference between a village having four hours and having two.

What this module produces is the *full gradient*: a steepest grade and the
compass bearing it rises along. Both go to `estimate_spread` together, which
converts the grade to an equivalent wind speed and adds it to the real wind as a
vector — so the hill helps decide which way the fire goes, not only how fast.
See "Slope is a wind, not a multiplier" in `services/spread.py` for why the
obvious alternative, resolving the slope along a direction chosen in advance,
cannot work.

Source: Open-Meteo's elevation endpoint, which serves Copernicus DEM GLO-90.
Chosen over the Copernicus Data Space or OpenTopography for one reason that
matters more than resolution: **it needs no credentials**, so terrain-aware
projections work on a fresh clone with no signup. GLO-90 is ~90 m, which is finer
than the 375 m satellite pixel we locate fires with, so it is not the limiting
factor.

What matters for fire is not the steepest slope but the slope *along the
direction the fire is travelling*. A fire spreading north over a hill that rises
east gets no upslope run at all. So this fits a plane to a local grid and
reports the full gradient, letting callers ask for the component in any bearing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from amonhen.core.config import settings
from amonhen.core.logging import get_logger
from amonhen.sources.base import DataSource

log = get_logger(__name__)

#: Copernicus GLO-90 is ~90 m. Sampling finer than this reads DEM noise as
#: terrain; sampling much coarser misses the ridge the fire actually climbs.
#: 400 m over a 1.6 km window describes "the ground this fire will run across"
#: rather than the bump it is standing on.
SAMPLE_SPACING_M = 400.0
GRID_RADIUS = 2  # -> 5x5 = 25 points, one request

KM_PER_DEGREE_LAT = 111.32


@dataclass(frozen=True)
class Terrain:
    """Local terrain around a point, as a fitted plane."""

    latitude: float
    longitude: float
    elevation_m: float
    #: Steepest slope, as a percentage grade.
    slope_pct: float
    #: Compass bearing of steepest *ascent*, degrees from north.
    aspect_deg: float
    relief_m: float           # max - min across the sampled window
    sample_count: int
    source: str

    def slope_toward(self, bearing_deg: float) -> float:
        """Grade in a given direction. Positive is uphill, negative downhill.

        The spread model no longer consumes this. It used to: slope was resolved
        along a direction of travel decided in advance from wind. That could not
        survive the discovery that on a steep hill in light air the *hill*
        decides the direction, so `estimate_spread` now takes the full gradient
        (`slope_pct` and `aspect_deg`) and resolves it itself.

        Kept because it is the honest way to answer "how steep is it that way",
        which is a real question for anyone reading a specific bearing off the
        map — just not the one the model asks.
        """
        offset = math.radians(bearing_deg - self.aspect_deg)
        return self.slope_pct * math.cos(offset)

    @property
    def descriptor(self) -> str:
        """Plain words for the UI. Grades mean little to most readers."""
        grade = abs(self.slope_pct)
        if grade < 5:
            return "flat"
        if grade < 15:
            return "gently sloping"
        if grade < 30:
            return "hilly"
        if grade < 50:
            return "steep"
        return "very steep"


class ElevationSource(DataSource[Terrain]):
    name = "copernicus_dem"
    attribution = "Copernicus DEM GLO-90 via Open-Meteo"
    homepage = "https://open-meteo.com/en/docs/elevation-api"
    #: Terrain does not change. Cache effectively forever; the only reason this
    #: is not infinite is so a corrupted entry eventually expires.
    cache_ttl_seconds = 60 * 60 * 24 * 30
    requires_credentials = False

    ENDPOINT = "https://api.open-meteo.com/v1/elevation"

    async def _fetch_live(
        self, latitude: float = 0.0, longitude: float = 0.0, **_: Any
    ) -> list[Terrain]:
        # Deliberately ignores `force`: the ground does not move, so re-fetching
        # it on every manual refresh would be pure waste.
        cache_key = f"{latitude:.4f},{longitude:.4f}"
        cached = self._cache_read(cache_key)
        if cached is not None:
            return [Terrain(**cached)]

        lats, lons = _sample_grid(latitude, longitude)
        response = await self._get(
            self.ENDPOINT,
            params={
                "latitude": ",".join(f"{v:.5f}" for v in lats),
                "longitude": ",".join(f"{v:.5f}" for v in lons),
            },
        )
        elevations = response.json().get("elevation") or []
        if len(elevations) != len(lats):
            log.warning("elevation.short_response", expected=len(lats), got=len(elevations))
            return []

        terrain = _fit_plane(latitude, longitude, elevations, source=self.attribution)
        self._cache_write(cache_key, terrain.__dict__)
        return [terrain]

    def _load_fixture(self, latitude: float = 0.0, longitude: float = 0.0, **_: Any) -> list[Terrain]:
        """Flat ground, declared as such.

        Guessing a slope would be worse than admitting we have none: a fabricated
        grade silently changes every projection downstream.
        """
        return [
            Terrain(
                latitude=latitude,
                longitude=longitude,
                elevation_m=0.0,
                slope_pct=0.0,
                aspect_deg=0.0,
                relief_m=0.0,
                sample_count=0,
                source="unavailable — assuming flat",
            )
        ]

    async def terrain_at(self, latitude: float, longitude: float) -> Terrain | None:
        results = await self.fetch(latitude=latitude, longitude=longitude)
        return results[0] if results else None


def _sample_grid(latitude: float, longitude: float) -> tuple[list[float], list[float]]:
    """A square grid centred on the point, in row-major order."""
    d_lat = (SAMPLE_SPACING_M / 1000.0) / KM_PER_DEGREE_LAT
    d_lon = d_lat / max(math.cos(math.radians(latitude)), 0.01)

    lats: list[float] = []
    lons: list[float] = []
    for row in range(-GRID_RADIUS, GRID_RADIUS + 1):
        for col in range(-GRID_RADIUS, GRID_RADIUS + 1):
            lats.append(latitude + row * d_lat)
            lons.append(longitude + col * d_lon)
    return lats, lons


def _fit_plane(
    latitude: float, longitude: float, elevations: list[float], source: str
) -> Terrain:
    """Least-squares plane through the sampled elevations.

    A plane fit rather than a central difference: it uses all 25 samples instead
    of 4, so a single noisy DEM cell cannot swing the result, and it degrades
    gracefully on broken ground where opposite neighbours happen to be level.
    """
    size = 2 * GRID_RADIUS + 1
    centre = elevations[len(elevations) // 2]

    # Local metres east/north, so the gradient comes out in metres per metre.
    xs: list[float] = []
    ys: list[float] = []
    for row in range(-GRID_RADIUS, GRID_RADIUS + 1):
        for col in range(-GRID_RADIUS, GRID_RADIUS + 1):
            xs.append(col * SAMPLE_SPACING_M)
            ys.append(row * SAMPLE_SPACING_M)

    n = len(elevations)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    mean_z = sum(elevations) / n

    sxx = sum((x - mean_x) ** 2 for x in xs)
    syy = sum((y - mean_y) ** 2 for y in ys)
    sxz = sum((x - mean_x) * (z - mean_z) for x, z in zip(xs, elevations, strict=True))
    syz = sum((y - mean_y) * (z - mean_z) for y, z in zip(ys, elevations, strict=True))

    # The grid is symmetric, so the x and y terms are orthogonal and the fit
    # separates into two independent slopes.
    dz_dx = sxz / sxx if sxx else 0.0
    dz_dy = syz / syy if syy else 0.0

    slope_pct = 100.0 * math.hypot(dz_dx, dz_dy)
    # atan2(east, north) gives a compass bearing; this points uphill.
    aspect_deg = (math.degrees(math.atan2(dz_dx, dz_dy)) + 360.0) % 360.0

    return Terrain(
        latitude=latitude,
        longitude=longitude,
        elevation_m=round(centre, 1),
        slope_pct=round(slope_pct, 1),
        aspect_deg=round(aspect_deg, 1),
        relief_m=round(max(elevations) - min(elevations), 1),
        sample_count=n,
        source=source,
    )
