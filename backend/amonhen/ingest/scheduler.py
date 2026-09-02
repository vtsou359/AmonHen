"""Background refresh of the operational picture.

Satellites do not care that nobody is looking. The scheduler keeps the picture
warm so that when someone opens the map at 03:00 because their phone rang, the
data is already there rather than thirty seconds away.

Polling intervals are set from the *upstream* update cadence, not from how often
we would like fresh data: FIRMS NRT publishes roughly every 3 hours for Europe,
so polling every 15 minutes is already generous and anything faster is just
rudeness to NASA's servers.
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from amonhen.core.config import settings
from amonhen.core.logging import get_logger
from amonhen.services.operations import operations

log = get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def refresh_picture() -> None:
    """One refresh cycle. Never raises — a failed cycle must not kill the scheduler."""
    try:
        picture = await operations.rebuild()
        operations._picture = picture  # noqa: SLF001 — the scheduler owns the cache
        log.info(
            "ingest.cycle_complete",
            incidents=len(picture.incidents),
            active=picture.active_count,
            area_ha=picture.total_area_ha,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("ingest.cycle_failed", error=str(exc), exc_info=True)


def start() -> AsyncIOScheduler | None:
    global _scheduler
    if not settings.ingest_enabled:
        log.info("ingest.disabled")
        return None

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        refresh_picture,
        trigger=IntervalTrigger(minutes=settings.firms_poll_minutes),
        id="refresh_picture",
        name="Rebuild the operational picture",
        # If the process was asleep through several intervals, run once on wake
        # rather than firing a burst of catch-up cycles.
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    _scheduler.start()
    log.info("ingest.started", every_minutes=settings.firms_poll_minutes)
    return _scheduler


def shutdown() -> None:
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        log.info("ingest.stopped")
