"""创建并注册 FastAPI 应用与资源接口。"""

from fastapi import FastAPI

from app.api.answers import router as answers_router
from app.api.agent_runs import router as agent_runs_router
from app.api.conversations import router as conversations_router
from app.api.answer_preferences import router as answer_preferences_router
from app.api.answer_workflows import router as answer_workflows_router
from app.api.health import router as health_router
from app.api.documents import router as documents_router
from app.api.knowledge_bases import router as knowledge_bases_router
from app.api.search import router as search_router
from app.api.workspaces import router as workspaces_router
from app.core.config import Settings


def create_app() -> FastAPI:
    settings = Settings()
    application = FastAPI(title=settings.app_name)
    application.include_router(health_router)
    application.include_router(workspaces_router)
    application.include_router(knowledge_bases_router)
    application.include_router(documents_router)
    application.include_router(search_router)
    application.include_router(answers_router)
    application.include_router(agent_runs_router)
    application.include_router(conversations_router)
    application.include_router(answer_preferences_router)
    application.include_router(answer_workflows_router)
    return application


app = create_app()
