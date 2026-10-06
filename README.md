# 企业知识与工作流智能体平台

这是一个面向学习与求职展示的企业知识和工作流智能体平台，强调可测试、可解释的工程实现。

## 当前进度

阶段 0.2 已搭建后端基础：配置管理、FastAPI 健康检查接口和接口测试。阶段 0.3 的[数据模型与接口设计草案](docs/data-api-design-proposal.md)正等待评审。文档入库、检索、身份认证、智能体和前端尚未实现。规划范围见[架构设计](docs/architecture.md)与[开发路线图](docs/roadmap.md)。

## 本地启动（PowerShell）

需要 Python 3.11 或更高版本。当前开发环境使用 Python 3.13。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".\backend[dev]"
Copy-Item .env.example .env
python -m uvicorn app.main:app --reload
```

打开 `http://127.0.0.1:8000/health` 查看进程健康状态，打开 `http://127.0.0.1:8000/docs` 查看自动生成的接口文档。

在仓库根目录运行测试：

```powershell
python -m pytest backend/tests
```

健康检查接口只确认应用进程能够响应，不检查数据库或模型服务是否可用。

## 工程设计取舍

我们先采用模块化 FastAPI 应用，并计划使用 PostgreSQL 和 pgvector 构建检索增强生成链路。建立检索质量基线后，再根据需要考虑近似索引、重排、Redis、LangGraph 或 MCP。身份认证和权限控制实现之前，本项目仅是本地单用户原型。
