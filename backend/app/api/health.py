"""Separate process liveness and database connectivity endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel
from starlette.responses import JSONResponse

from app.db.readiness import database_is_ready

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Confirm that the API process can respond to requests."""
    return HealthResponse(status="ok")


class ReadinessResponse(BaseModel):
    status: str
    database: str


@router.get(
    "/ready", response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse, "description": "Database unavailable"}},
)
def ready() -> JSONResponse:
    """Check current database connectivity without business writes or model calls."""
    available = database_is_ready()
    return JSONResponse(
        status_code=200 if available else 503,
        content={
            "status": "ready" if available else "not_ready",
            "database": "ok" if available else "unavailable",
        },
        headers={"Cache-Control": "no-store"},
    )
