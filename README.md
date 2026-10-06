# 企业知识与工作流智能体平台

这是一个面向学习与求职展示的企业知识和工作流智能体平台，强调可测试、可解释的工程实现。

## 当前进度

阶段 0.2 已搭建后端基础：配置管理、FastAPI 健康检查接口和接口测试。阶段 0.3 的[数据模型与接口设计](docs/data-api-design-proposal.md)已确认文档归属、同步处理方式和知识库命名规则；本轮建立 PostgreSQL 元数据层（`Workspace`、`KnowledgeBase`、`Document`）及工作空间、知识库的创建和读取接口。文件上传处理、`Chunk`、向量检索、问答、身份认证、智能体和前端属于后续开发。规划范围见[架构设计](docs/architecture.md)与[开发路线图](docs/roadmap.md)。

## 本地启动（PowerShell）

需要 Python 3.11 或更高版本，以及可用的 Docker Compose。当前开发环境使用 Python 3.13。在仓库根目录依次执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".\backend[dev]"
Copy-Item .env.example .env
docker compose -f compose.yaml up -d --wait db
python -m alembic -c backend/alembic.ini upgrade head
python -m uvicorn app.main:app --reload
```

打开 `http://127.0.0.1:8000/health` 查看进程健康状态，打开 `http://127.0.0.1:8000/docs` 查看自动生成的接口文档。

`.env.example` 中的 `EAAP_DATABASE_URL` 对应本地 Compose 的 PostgreSQL 服务；如端口或账号不同，请在本机 `.env` 中调整。示例密码仅用于本机开发。数据库结构通过 Alembic 迁移建立，不依赖应用启动时自动建表。

当前可在 `/docs` 调用四个资源接口：

- `POST /api/v1/workspaces` 和 `GET /api/v1/workspaces/{workspace_id}`；
- `POST /api/v1/workspaces/{workspace_id}/knowledge-bases` 和 `GET /api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}`。

知识库名称在同一工作空间内重复时返回 `409`，路径中的父子资源不匹配时返回 `404`。

首次创建数据库卷时，Compose 会额外建立独立的 `eaap_test` 测试库。在仓库根目录、数据库服务运行时执行完整测试：

```powershell
$env:EAAP_TEST_DATABASE_URL = "postgresql+psycopg://eaap:eaap_local_only@127.0.0.1:5432/eaap_test"
python -m pytest backend/tests
Remove-Item Env:EAAP_TEST_DATABASE_URL
```

数据库测试在测试库中建立临时模式，结束后删除；未设置 `EAAP_TEST_DATABASE_URL` 时会跳过数据库集成测试。不要把测试地址指向日常使用的 `eaap` 数据库。

健康检查接口只确认应用进程能够响应，不检查数据库或模型服务是否可用。

## 工程设计取舍

我们先采用模块化 FastAPI 应用，并计划使用 PostgreSQL 和 pgvector 构建检索增强生成链路。建立检索质量基线后，再根据需要考虑近似索引、重排、Redis、LangGraph 或 MCP。身份认证和权限控制实现之前，本项目仅是本地单用户原型。
