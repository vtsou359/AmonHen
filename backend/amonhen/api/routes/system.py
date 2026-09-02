"""System status and self-description.

The `/sources` endpoint exists so the UI can be honest about what it is showing.
A platform that silently serves demo data looks identical to one serving live
satellite data, and that is precisely the confusion that gets someone hurt. The
banner in the frontend is driven from here.
"""

from __future__ import annotations

from fastapi import APIRouter

from amonhen.api.schemas import SourceStatus, SystemStatus
from amonhen.core.config import settings
from amonhen.services import boundary
from amonhen.services.operations import operations

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Deliberately does no I/O — it answers 'is the process up'."""
    return {"status": "ok"}


@router.get("/status", response_model=SystemStatus)
async def status() -> SystemStatus:
    sources = [
        SourceStatus(**operations.firms.status()),
        SourceStatus(**operations.weather.status()),
        SourceStatus(**operations.elevation.status()),
        SourceStatus(**operations.land_cover.status()),
    ]
    any_fixture = any(s.mode == "fixture" for s in sources)

    return SystemStatus(
        status="degraded" if any_fixture else "ok",
        environment=settings.environment,
        area_of_interest=settings.area_of_interest_name,
        bbox=settings.area_of_interest_bbox,
        live_sources=not any_fixture,
        sources=sources,
        picture_generated_at=(
            operations._picture.generated_at if operations._picture else None  # noqa: SLF001
        ),
        boundary_filter_active=settings.restrict_to_boundary and boundary.is_available(),
        boundary_name=settings.boundary_name if boundary.is_available() else None,
    )


@router.get("/ontology")
async def ontology() -> dict[str, list[str]]:
    """Export the controlled vocabularies so the frontend never hardcodes them.

    Gotham's strength is a shared ontology; this is how we keep ours in one
    place instead of duplicating enum values across two languages.
    """
    from amonhen.domain import enums

    return {
        name.lower(): [member.value for member in enum_class]
        for name, enum_class in vars(enums).items()
        if isinstance(enum_class, type) and issubclass(enum_class, enums.StrEnum)
        and enum_class is not enums.StrEnum
    }
