"""Common machinery for every external data source.

Design rules, in priority order:

1. **Never crash the platform because an upstream feed is down.** A source that
   fails logs loudly and returns nothing; the map still renders, just staler.
2. **Never require a credential to see the product.** Every source can fall back
   to a bundled fixture, so `docker compose up` gives you a working system with
   zero signup. Live keys upgrade it in place.
3. **Cache on disk.** Satellite feeds update every few hours at best; re-fetching
   on every request wastes their bandwidth and your time.
"""

from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Generic, TypeVar

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from amonhen.core.config import settings
from amonhen.core.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")


class SourceUnavailable(RuntimeError):
    """Raised internally when a feed cannot be reached; callers get fixtures."""


class JsonCache:
    """A small keyed disk cache, one JSON file per key.

    Split out of `DataSource` so derived products can use it too. Burned-area
    mapping and fuel moisture are not feeds — they are things computed *from*
    a feed — but they are every bit as expensive to recompute, so they want the
    same cache without inheriting the fixture-fallback machinery around it.
    """

    def __init__(self, name: str, ttl_seconds: int) -> None:
        self.directory = settings.cache_dir / name
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds

    def path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode()).hexdigest()[:16]
        return self.directory / f"{digest}.json"

    def read(self, key: str, force: bool = False) -> Any | None:
        if force:
            return None
        path = self.path(key)
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.ttl_seconds:
            return None
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return None

    def write(self, key: str, value: Any) -> None:
        self.path(key).write_text(json.dumps(value, default=str))


class DataSource(ABC, Generic[T]):
    """Base class for a feed of fire-relevant data.

    Subclasses implement `_fetch_live` (talk to the network) and
    `_load_fixture` (return the bundled sample). The base class decides which
    one to call, handles caching, and never lets an exception escape.
    """

    name: str = "unnamed"
    attribution: str = ""
    homepage: str = ""
    cache_ttl_seconds: int = 900

    #: Set False on subclasses that work without any credential.
    requires_credentials: bool = True

    def __init__(self) -> None:
        self._cache = JsonCache(self.name, self.cache_ttl_seconds)
        self.cache_dir = self._cache.directory

    # ---------------------------------------------------------------- public

    async def fetch(self, force: bool = False, **kwargs: Any) -> list[T]:
        """Get records, preferring live data and degrading to fixtures.

        `force` skips the disk cache. It exists for the Refresh button: a
        control labelled "Refresh" that hands back a ten-minute-old cached
        response is lying to the person pressing it.
        """
        if force:
            kwargs["force"] = True
        if self.is_live:
            try:
                records = await self._fetch_live(**kwargs)
                log.info("source.fetched", source=self.name, count=len(records), mode="live")
                return records
            except Exception as exc:  # noqa: BLE001 — deliberate catch-all, see rule 1
                log.warning(
                    "source.live_failed", source=self.name, error=str(exc), falling_back=True
                )
        records = self._load_fixture(**kwargs)
        log.info("source.fetched", source=self.name, count=len(records), mode="fixture")
        return records

    @property
    def is_live(self) -> bool:
        return not self.requires_credentials or self._has_credentials()

    def status(self) -> dict[str, Any]:
        """Reported at /api/v1/system/sources so the UI can show feed health."""
        return {
            "name": self.name,
            "mode": "live" if self.is_live else "fixture",
            "attribution": self.attribution,
            "homepage": self.homepage,
            "requires_credentials": self.requires_credentials,
        }

    # ------------------------------------------------------------- subclass

    @abstractmethod
    async def _fetch_live(self, **kwargs: Any) -> list[T]: ...

    @abstractmethod
    def _load_fixture(self, **kwargs: Any) -> list[T]: ...

    def _has_credentials(self) -> bool:
        return True

    # -------------------------------------------------------------- helpers

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    async def _get(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> httpx.Response:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url, params=params, **kw)
            response.raise_for_status()
            return response

    def _cache_path(self, key: str) -> Path:
        return self._cache.path(key)

    def _cache_read(self, key: str, force: bool = False) -> Any | None:
        return self._cache.read(key, force=force)

    def _cache_write(self, key: str, value: Any) -> None:
        self._cache.write(key, value)

    def _fixture_path(self, filename: str) -> Path:
        return settings.fixtures_dir / filename

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)
