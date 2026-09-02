"""NASA FIRMS — active fire / thermal anomaly detections.

FIRMS is the backbone of active-fire intelligence worldwide: NASA reprocesses
VIIRS and MODIS thermal bands into point detections and publishes them within
~3 hours of the satellite pass (NRT), or ~60 seconds for the US/Canada.

For Greece the practical picture is four useful passes a day from VIIRS
(Suomi-NPP, NOAA-20, NOAA-21) at 375 m, plus MODIS at 1 km. That is enough to
see a fire start and enough to watch it run, but *not* enough to see it in the
first fifteen minutes — which is why this is one input among several, not the
whole system.

Get a free key instantly: https://firms.modaps.eosdis.nasa.gov/api/map_key/
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from amonhen.core.config import settings
from amonhen.core.logging import get_logger
from amonhen.domain.entities import Detection, Provenance
from amonhen.domain.enums import DetectionConfidence, Satellite
from amonhen.sources.base import DataSource

log = get_logger(__name__)

#: FIRMS dataset identifiers, newest sensors first.
FIRMS_PRODUCTS: dict[str, Satellite] = {
    "VIIRS_NOAA21_NRT": Satellite.VIIRS_NOAA21,
    "VIIRS_NOAA20_NRT": Satellite.VIIRS_NOAA20,
    "VIIRS_SNPP_NRT": Satellite.VIIRS_SNPP,
    "MODIS_NRT": Satellite.MODIS_TERRA,
}

#: How FIRMS spells satellite names inside the CSV itself.
_SATELLITE_ALIASES: dict[str, Satellite] = {
    "N": Satellite.VIIRS_SNPP,
    "N20": Satellite.VIIRS_NOAA20,
    "N21": Satellite.VIIRS_NOAA21,
    "NOAA-20": Satellite.VIIRS_NOAA20,
    "NOAA-21": Satellite.VIIRS_NOAA21,
    "Suomi NPP": Satellite.VIIRS_SNPP,
    "Terra": Satellite.MODIS_TERRA,
    "Aqua": Satellite.MODIS_AQUA,
    "T": Satellite.MODIS_TERRA,
    "A": Satellite.MODIS_AQUA,
}


class FirmsSource(DataSource[Detection]):
    name = "nasa_firms"
    attribution = "NASA FIRMS (LANCE/EOSDIS)"
    homepage = "https://firms.modaps.eosdis.nasa.gov"
    cache_ttl_seconds = 600
    requires_credentials = True

    def _has_credentials(self) -> bool:
        return bool(settings.firms_map_key)

    # ------------------------------------------------------------------ live

    async def _fetch_live(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        day_range: int = 2,  # FIRMS area endpoint accepts 1-5 only
        products: list[str] | None = None,
        force: bool = False,
        **_: Any,
    ) -> list[Detection]:
        bbox = bbox or settings.area_of_interest_bbox
        products = products or list(FIRMS_PRODUCTS)
        area = ",".join(str(round(c, 4)) for c in bbox)

        detections: list[Detection] = []
        for product in products:
            url = (
                f"{settings.firms_base_url}/area/csv/{settings.firms_map_key}"
                f"/{product}/{area}/{day_range}"
            )
            cache_key = f"{product}:{area}:{day_range}"

            cached = self._cache_read(cache_key, force=force)
            if cached is not None:
                body = cached["body"]
            else:
                response = await self._get(url)
                body = response.text
                # FIRMS answers quota problems with a 200 and a prose body.
                if body.lstrip().lower().startswith(("invalid", "you have exceeded")):
                    log.warning("firms.rejected", product=product, body=body[:160])
                    continue
                self._cache_write(cache_key, {"body": body})

            parsed = self._parse_csv(body, default_satellite=FIRMS_PRODUCTS[product], url=url)
            log.debug("firms.product_parsed", product=product, count=len(parsed))
            detections.extend(parsed)

        return self._deduplicate(detections)

    # --------------------------------------------------------------- parsing

    def _parse_csv(self, body: str, default_satellite: Satellite, url: str) -> list[Detection]:
        """Turn a FIRMS CSV into Detections.

        VIIRS and MODIS use different column names for the same physical
        quantities (`bright_ti4` vs `brightness`), so we normalise here rather
        than leaking the difference into the rest of the platform.
        """
        retrieved_at = self._now()
        out: list[Detection] = []

        for row in csv.DictReader(io.StringIO(body)):
            try:
                observed_at = self._parse_acquisition(row["acq_date"], row["acq_time"])
            except (KeyError, ValueError):
                continue

            satellite = _SATELLITE_ALIASES.get(
                (row.get("satellite") or "").strip(), default_satellite
            )
            raw_confidence = (row.get("confidence") or "").strip()

            out.append(
                Detection(
                    latitude=float(row["latitude"]),
                    longitude=float(row["longitude"]),
                    observed_at=observed_at,
                    satellite=satellite,
                    frp_mw=_maybe_float(row.get("frp")),
                    brightness_k=_maybe_float(row.get("bright_ti4") or row.get("brightness")),
                    brightness_k_secondary=_maybe_float(
                        row.get("bright_ti5") or row.get("bright_t31")
                    ),
                    confidence=_normalise_confidence(raw_confidence),
                    confidence_raw=raw_confidence or None,
                    scan_km=_maybe_float(row.get("scan")),
                    track_km=_maybe_float(row.get("track")),
                    is_daytime=(row.get("daynight") or "").strip().upper() == "D",
                    provenance=Provenance(
                        source=self.name,
                        retrieved_at=retrieved_at,
                        url=url.replace(settings.firms_map_key, "<MAP_KEY>"),
                    ),
                )
            )
        return out

    @staticmethod
    def _parse_acquisition(acq_date: str, acq_time: str) -> datetime:
        """FIRMS gives `2026-08-27` and `1342` (UTC, zero-padding optional)."""
        hhmm = acq_time.strip().zfill(4)
        return datetime.strptime(f"{acq_date.strip()} {hhmm}", "%Y-%m-%d %H%M").replace(tzinfo=UTC)

    @staticmethod
    def _deduplicate(detections: list[Detection]) -> list[Detection]:
        """The same fire pixel can arrive from overlapping products.

        We key on rounded position plus the acquisition minute — close enough to
        collapse genuine duplicates, coarse enough not to merge two real pixels.
        """
        seen: set[tuple[float, float, str]] = set()
        unique: list[Detection] = []
        for d in detections:
            key = (round(d.latitude, 4), round(d.longitude, 4), d.observed_at.strftime("%Y%m%d%H%M"))
            if key not in seen:
                seen.add(key)
                unique.append(d)
        return unique

    # -------------------------------------------------------------- fixture

    def _load_fixture(self, **_: Any) -> list[Detection]:
        """Bundled sample so the platform is fully usable with no NASA key.

        The fixture is time-shifted to *now* on every load, so the demo always
        shows a fire that started a few hours ago rather than one from 2023.
        """
        path = self._fixture_path("firms_detections.json")
        if not path.exists():
            return []

        raw = json.loads(path.read_text())
        anchor = datetime.fromisoformat(raw["anchor"])
        shift = self._now() - anchor
        retrieved_at = self._now()

        return [
            Detection(
                latitude=r["latitude"],
                longitude=r["longitude"],
                observed_at=datetime.fromisoformat(r["observed_at"]) + shift,
                satellite=Satellite(r.get("satellite", "viirs_noaa20")),
                frp_mw=r.get("frp_mw"),
                brightness_k=r.get("brightness_k"),
                confidence=DetectionConfidence(r.get("confidence", "nominal")),
                scan_km=r.get("scan_km", 0.375),
                track_km=r.get("track_km", 0.375),
                is_daytime=r.get("is_daytime", True),
                provenance=Provenance(
                    source=f"{self.name}:fixture",
                    retrieved_at=retrieved_at,
                    note="Synthetic sample data — configure AMONHEN_FIRMS_MAP_KEY for live feeds",
                ),
            )
            for r in raw["detections"]
        ]


def _maybe_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "", "NaN") else None
    except (TypeError, ValueError):
        return None


def _normalise_confidence(raw: str) -> DetectionConfidence:
    """VIIRS reports l/n/h; MODIS reports 0-100. Map both onto one scale."""
    token = raw.strip().lower()
    if token in ("l", "low"):
        return DetectionConfidence.LOW
    if token in ("h", "high"):
        return DetectionConfidence.HIGH
    if token in ("n", "nominal"):
        return DetectionConfidence.NOMINAL
    try:
        pct = float(token)
    except ValueError:
        return DetectionConfidence.NOMINAL
    if pct < 30:
        return DetectionConfidence.LOW
    if pct >= 80:
        return DetectionConfidence.HIGH
    return DetectionConfidence.NOMINAL
