"""Open-Meteo — fire weather, current and forecast.

Chosen as the default weather source for one reason above all others: it needs
no API key, so `docker compose up` gives you real, live weather with zero
signup. It serves ECMWF IFS and DWD ICON at ~9 km, hourly, free for
non-commercial use.

For research-grade reanalysis (long climatologies, FWI baselines) use ERA5-Land
via the Copernicus CDS instead — see `docs/DATA_SOURCES.md`. Open-Meteo also
proxies ERA5 through its archive endpoint, which is what `fetch_history` uses.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from amonhen.core.config import settings
from amonhen.core.logging import get_logger
from amonhen.domain.entities import Provenance, WeatherObservation
from amonhen.sources.base import DataSource

log = get_logger(__name__)

HOURLY_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "precipitation",
]


class OpenMeteoSource(DataSource[WeatherObservation]):
    name = "open_meteo"
    attribution = "Open-Meteo (ECMWF IFS / DWD ICON)"
    homepage = "https://open-meteo.com"
    cache_ttl_seconds = 1800
    requires_credentials = False  # <- the whole point

    async def _fetch_live(
        self,
        latitude: float | None = None,
        longitude: float | None = None,
        forecast_days: int = 3,
        past_days: int = 1,
        force: bool = False,
        **_: Any,
    ) -> list[WeatherObservation]:
        west, south, east, north = settings.area_of_interest_bbox
        latitude = latitude if latitude is not None else (south + north) / 2
        longitude = longitude if longitude is not None else (west + east) / 2

        params = {
            "latitude": round(latitude, 4),
            "longitude": round(longitude, 4),
            "hourly": ",".join(HOURLY_VARIABLES),
            "past_days": past_days,
            "forecast_days": forecast_days,
            "timezone": "UTC",
            "wind_speed_unit": "kmh",
        }
        cache_key = f"forecast:{params['latitude']}:{params['longitude']}:{past_days}:{forecast_days}"

        payload = self._cache_read(cache_key, force=force)
        if payload is None:
            response = await self._get(settings.open_meteo_forecast_url, params=params)
            payload = response.json()
            self._cache_write(cache_key, payload)

        return self._parse(payload, latitude, longitude)

    def _parse(
        self, payload: dict[str, Any], latitude: float, longitude: float
    ) -> list[WeatherObservation]:
        hourly = payload.get("hourly") or {}
        times: list[str] = hourly.get("time", [])
        retrieved_at = self._now()
        now = self._now()

        observations: list[WeatherObservation] = []
        for i, timestamp in enumerate(times):
            observed_at = datetime.fromisoformat(timestamp).replace(tzinfo=UTC)
            observations.append(
                WeatherObservation(
                    latitude=latitude,
                    longitude=longitude,
                    observed_at=observed_at,
                    is_forecast=observed_at > now,
                    temperature_c=_at(hourly, "temperature_2m", i),
                    relative_humidity_pct=_at(hourly, "relative_humidity_2m", i),
                    wind_speed_kmh=_at(hourly, "wind_speed_10m", i),
                    wind_direction_deg=_at(hourly, "wind_direction_10m", i),
                    wind_gust_kmh=_at(hourly, "wind_gusts_10m", i),
                    precipitation_mm=_at(hourly, "precipitation", i),
                    provenance=Provenance(
                        source=self.name,
                        retrieved_at=retrieved_at,
                        url=settings.open_meteo_forecast_url,
                    ),
                )
            )
        return observations

    def _load_fixture(self, **_: Any) -> list[WeatherObservation]:
        """A hot, dry, windy Greek August afternoon — the conditions that matter.

        Only used if Open-Meteo itself is unreachable, since no key is required.
        """
        west, south, east, north = settings.area_of_interest_bbox
        latitude, longitude = (south + north) / 2, (west + east) / 2
        start = self._now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=24)

        observations: list[WeatherObservation] = []
        for hour in range(96):
            observed_at = start + timedelta(hours=hour)
            hour_of_day = observed_at.hour
            # Crude diurnal cycle: hot and dry in the afternoon, recovering at night.
            temperature = 24 + 12 * max(0.0, _diurnal(hour_of_day))
            humidity = max(12.0, 62 - 40 * max(0.0, _diurnal(hour_of_day)))
            observations.append(
                WeatherObservation(
                    latitude=latitude,
                    longitude=longitude,
                    observed_at=observed_at,
                    is_forecast=observed_at > self._now(),
                    temperature_c=round(temperature, 1),
                    relative_humidity_pct=round(humidity, 1),
                    wind_speed_kmh=round(18 + 14 * max(0.0, _diurnal(hour_of_day)), 1),
                    wind_direction_deg=25.0,  # a northerly: the Greek summer meltemi
                    wind_gust_kmh=round(30 + 22 * max(0.0, _diurnal(hour_of_day)), 1),
                    precipitation_mm=0.0,
                    provenance=Provenance(
                        source=f"{self.name}:fixture",
                        retrieved_at=self._now(),
                        note="Synthetic fire-weather profile — Open-Meteo unreachable",
                    ),
                )
            )
        return observations


def _at(hourly: dict[str, Any], key: str, index: int) -> float | None:
    series = hourly.get(key)
    if not series or index >= len(series):
        return None
    value = series[index]
    return float(value) if value is not None else None


def _diurnal(hour: int) -> float:
    """Peaks around 15:00 local, troughs before dawn. Range roughly -1..1."""
    import math

    return math.sin((hour - 9) / 24 * 2 * math.pi)
