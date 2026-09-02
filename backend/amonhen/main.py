"""Amon Hen API.

    uvicorn amonhen.main:app --reload

The application starts, warms the operational picture in the background, and
serves. It does not require Postgres, a NASA key, or any other setup to be
useful — that is a deliberate product decision, not an accident of scaffolding.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from amonhen.api.routes import danger, incidents, layers, system
from amonhen.core.config import settings
from amonhen.core.logging import configure_logging, get_logger
from amonhen.ingest import scheduler

configure_logging()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info(
        "amonhen.starting",
        environment=settings.environment,
        area=settings.area_of_interest_name,
        live_sources=settings.live_sources_available,
    )
    if not settings.live_sources_available:
        log.warning(
            "amonhen.demo_mode",
            message="No FIRMS key configured — serving bundled fixtures. "
            "Set AMONHEN_FIRMS_MAP_KEY for live detections.",
        )

    # Warm the cache without blocking startup: the health check should answer
    # immediately even while the first satellite fetch is still in flight.
    warmup = asyncio.create_task(scheduler.refresh_picture())
    scheduler.start()

    try:
        yield
    finally:
        warmup.cancel()
        scheduler.shutdown()
        log.info("amonhen.stopped")


app = FastAPI(
    title="Amon Hen",
    version="0.1.0",
    summary="Fire intelligence across the full fire management cycle",
    description=(
        "Open-source Earth observation, turned into decisions.\n\n"
        "Amon Hen ingests satellite active-fire detections and fire weather, "
        "clusters them into incidents, estimates spread, and ranks what is at "
        "risk. Every figure carries its provenance and its uncertainty."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (incidents.router, layers.router, danger.router, system.router):
    app.include_router(router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
async def root() -> JSONResponse:
    return JSONResponse(
        {
            "name": "Amon Hen",
            "tagline": "Fire intelligence for the full fire management cycle",
            "area_of_interest": settings.area_of_interest_name,
            "live_sources": settings.live_sources_available,
            "docs": "/docs",
            "api": settings.api_prefix,
        }
    )
