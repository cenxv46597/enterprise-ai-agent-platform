"""FastAPI application factory."""

from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.config import Settings


def create_app() -> FastAPI:
    settings = Settings()
    application = FastAPI(title=settings.app_name)
    application.include_router(health_router)
    return application


app = create_app()
