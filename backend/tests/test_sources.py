"""Tests for the shared machinery in `sources.base`.

What is pinned here is honesty about a feed, not the feed itself. A source that
is configured for live data but quietly serving its bundled sample is the one
failure this platform most needs to announce — and measured with an invalid
FIRMS key, it did not: the map showed demo fires while the status endpoint
reported the feed "live".
"""

from __future__ import annotations

from typing import Any

import httpx

from amonhen.sources.base import DataSource

#: The shape of a real FIRMS failure. The key sits in the URL path, and httpx
#: quotes the URL in its error message.
KEY = "d3adb33fd3adb33fd3adb33fd3adb33f"
FIRMS_URL = (
    "https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
    f"{KEY}/VIIRS_NOAA21_NRT/19.3,34.7,29.7,41.8/5"
)


def rejected_key() -> httpx.HTTPStatusError:
    """What FIRMS answers to an invalid key: HTTP 400."""
    request = httpx.Request("GET", FIRMS_URL)
    return httpx.HTTPStatusError(
        f"Client error '400 Bad Request' for url '{FIRMS_URL}'",
        request=request,
        response=httpx.Response(400, request=request),
    )


class ScriptedSource(DataSource[str]):
    """A source whose next live fetch returns, or raises, whatever it is told."""

    name = "test_scripted_source"
    requires_credentials = True

    def __init__(self, has_key: bool = True) -> None:
        super().__init__()
        self.has_key = has_key
        self.next_live: list[str] | Exception = ["live record"]

    async def _fetch_live(self, **_: Any) -> list[str]:
        if isinstance(self.next_live, Exception):
            raise self.next_live
        return self.next_live

    def _load_fixture(self, **_: Any) -> list[str]:
        return ["demo record"]

    def _has_credentials(self) -> bool:
        return self.has_key


# --------------------------------------------------------------------------
# Configured is not the same as serving
# --------------------------------------------------------------------------


def test_a_configured_source_is_live_before_anything_has_failed():
    assert ScriptedSource().status()["mode"] == "live"


def test_a_source_with_no_key_is_on_fixtures_with_nothing_to_explain():
    """No key is not a failure. The banner tells this operator to add one; a
    note here would make it tell them something else."""
    status = ScriptedSource(has_key=False).status()
    assert status["mode"] == "fixture"
    assert "note" not in status


async def test_a_rejected_key_is_reported_as_fixture_not_live():
    source = ScriptedSource()
    source.next_live = rejected_key()

    records = await source.fetch()

    assert records == ["demo record"], "a failing feed still degrades rather than crashing"
    status = source.status()
    assert status["mode"] == "fixture"
    assert "HTTP 400" in status["note"]


async def test_a_later_success_clears_the_failure():
    """Otherwise one bad request at 03:00 would leave the banner up all day."""
    source = ScriptedSource()
    source.next_live = rejected_key()
    await source.fetch()

    source.next_live = ["live record"]
    assert await source.fetch() == ["live record"]

    status = source.status()
    assert status["mode"] == "live"
    assert "note" not in status


# --------------------------------------------------------------------------
# The reason goes on screen, so it must not carry the key
# --------------------------------------------------------------------------


async def test_the_failure_note_never_contains_the_key():
    """The note is served by the API and drawn in the banner — where screenshots
    get taken and shared."""
    source = ScriptedSource()
    source.next_live = rejected_key()
    await source.fetch()
    assert KEY not in str(source.status())


async def test_a_network_failure_is_named_by_its_type_not_its_message():
    source = ScriptedSource()
    source.next_live = httpx.ConnectTimeout(f"timed out connecting to {FIRMS_URL}")
    await source.fetch()

    note = source.status()["note"]
    assert "ConnectTimeout" in note
    assert KEY not in note


# --------------------------------------------------------------------------
# End to end: what the banner reads
# --------------------------------------------------------------------------


async def test_a_rejected_firms_key_takes_the_whole_picture_off_live(monkeypatch):
    """`live_sources` is the single flag the frontend banner hides on. It must
    go false when the fire feed is really serving demo data."""
    from amonhen.api.routes import system
    from amonhen.core.config import settings
    from amonhen.services.operations import operations

    monkeypatch.setattr(settings, "firms_map_key", KEY)
    monkeypatch.setattr(operations.firms, "_fallback_reason", "HTTP 400")

    status = await system.status()

    assert status.live_sources is False
    firms = next(s for s in status.sources if s.name == "nasa_firms")
    assert firms.mode == "fixture"
    assert firms.note
