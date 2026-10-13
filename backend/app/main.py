"""创建并注册 FastAPI 应用与资源接口。"""

import secrets

from fastapi import Depends, FastAPI

from app.api.answers import router as answers_router
from app.api.agent_runs import router as agent_runs_router
from app.api.auth import router as auth_router
from app.api.conversations import router as conversations_router
from app.api.answer_preferences import router as answer_preferences_router
from app.api.answer_workflows import router as answer_workflows_router
from app.api.health import router as health_router
from app.api.documents import router as documents_router
from app.api.knowledge_bases import router as knowledge_bases_router
from app.api.search import router as search_router
from app.api.workspaces import router as workspaces_router
from app.api.workspace_members import router as workspace_members_router
from app.auth.workspace_access import require_workspace_member
from app.core.config import Settings


def create_app() -> FastAPI:
    settings = Settings()
    configured_secret = (
        settings.jwt_secret_key.get_secret_value() if settings.jwt_secret_key is not None else None
    )
    if not configured_secret:
        if settings.app_env == "production":
            raise RuntimeError("生产环境必须配置至少 32 字符的 JWT 签名密钥")
        configured_secret = secrets.token_urlsafe(48)
    elif len(configured_secret) < 32:
        raise ValueError("JWT 签名密钥至少需要 32 字符")
    application = FastAPI(title=settings.app_name)
    application.state.jwt_secret_key = configured_secret
    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(workspaces_router)
    application.include_router(workspace_members_router)
    workspace_guard = [Depends(require_workspace_member)]
    application.include_router(knowledge_bases_router, dependencies=workspace_guard)
    application.include_router(documents_router, dependencies=workspace_guard)
    application.include_router(search_router, dependencies=workspace_guard)
    application.include_router(answers_router, dependencies=workspace_guard)
    application.include_router(agent_runs_router, dependencies=workspace_guard)
    application.include_router(conversations_router, dependencies=workspace_guard)
    application.include_router(answer_preferences_router, dependencies=workspace_guard)
    application.include_router(answer_workflows_router, dependencies=workspace_guard)
    return application


app = create_app()
