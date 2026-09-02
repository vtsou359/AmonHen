"""Central configuration.

Every knob the platform has lives here, is typed, and is overridable from the
environment or a `.env` file. Nothing else in the codebase reads `os.environ`
directly — if you want to know what can be configured, this file is the answer.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"),
        env_prefix="AMONHEN_",
        extra="ignore",
    )

    # ------------------------------------------------------------------ app
    environment: Literal["local", "staging", "production"] = "local"
    debug: bool = True
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:3000"]

    # ------------------------------------------------------------- database
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "amonhen"
    postgres_password: str = "amonhen"
    postgres_db: str = "amonhen"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # -------------------------------------------------------- data sources
    # NASA FIRMS. Free key, issued instantly: https://firms.modaps.eosdis.nasa.gov/api/map_key/
    # Leave empty and the platform runs on bundled fixtures instead of failing.
    firms_map_key: str = ""
    firms_base_url: str = "https://firms.modaps.eosdis.nasa.gov/api"

    # Open-Meteo needs no key at all — this is why it is our default weather source.
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"

    # Copernicus EFFIS / Global Wildfire Information System.
    effis_wfs_url: str = "https://maps.effis.emergency.copernicus.eu/gwis"

    # Copernicus Data Space Ecosystem (Sentinel-2/3). Only needed for the
    # post-fire modules; register at https://dataspace.copernicus.eu
    cdse_client_id: str = ""
    cdse_client_secret: str = ""

    # ------------------------------------------------------------- ingest
    ingest_enabled: bool = True
    firms_poll_minutes: int = 15
    weather_poll_minutes: int = 60
    detection_retention_days: int = 90

    # -------------------------------------------------- operational area
    # Greece. Bounding box is (west, south, east, north) in EPSG:4326.
    area_of_interest_name: str = "Greece"
    # A bbox alone cannot express "Greece": every rectangle around it also
    # contains western Turkey, Albania, North Macedonia and southern Bulgaria.
    # The bbox is what FIRMS accepts; the polygon is what we actually mean.
    # See services/boundary.py and scripts/build_boundary.py.
    restrict_to_boundary: bool = True
    boundary_name: str = "greece"
    # Fires do not respect borders. Detections this far outside the polygon are
    # still kept — a fire 400 m into North Macedonia is Greece's problem too.
    # 0 means strict. Raising this past ~20 km starts pulling in genuinely
    # unrelated Turkish and Albanian fires, which is what we were escaping.
    boundary_margin_km: float = 5.0
    area_of_interest_bbox: tuple[float, float, float, float] = (19.3, 34.7, 29.7, 41.8)
    # FIRMS country code used for its country-scoped CSV endpoints.
    firms_country: str = "GRC"
    timezone: str = "Europe/Athens"

    # -------------------------------------------------------- incident model
    # Spatio-temporal DBSCAN parameters used to group raw satellite detections
    # into human-meaningful incidents. Tuned for VIIRS 375 m pixels.
    #
    # eps_hours was raised from 12 to 30 after measuring the real gap
    # distribution in live FIRMS data over Greece: consecutive detections of
    # the same burning ground pile up at ~24 h (the daily overpass cycle), and
    # 10.5% of gaps exceeded 12 h — so one continuous fire was fragmenting into
    # roughly one incident per day. 30 h bridges 99.7% of observed gaps while
    # still keeping fires a week apart separate.
    cluster_eps_km: float = 3.0
    cluster_eps_hours: float = 30.0
    cluster_min_samples: int = 2

    # ----------------------------------------------------------- filesystem
    data_dir: Path = REPO_ROOT / "data"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fixtures_dir(self) -> Path:
        return self.data_dir / "fixtures"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def live_sources_available(self) -> bool:
        """False when no credentials are configured, i.e. we serve fixtures."""
        return bool(self.firms_map_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
