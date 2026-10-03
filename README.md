# Enterprise AI Agent Platform

An enterprise knowledge and workflow agent platform built as a learning-focused, testable engineering project.

## Current status

Phase 0.2 establishes the backend foundation: configuration, a FastAPI health endpoint, and an API test. Document ingestion, retrieval, authentication, agents, and the frontend are not implemented yet. See [the architecture](docs/architecture.md) and [roadmap](docs/roadmap.md) for their planned boundaries.

## Local quick start (PowerShell)

Requires Python 3.11 or newer. The current development environment uses Python 3.13.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".\backend[dev]"
Copy-Item .env.example .env
python -m uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/health` for the process health response and `http://127.0.0.1:8000/docs` for the generated API documentation.

Run the tests from the repository root:

```powershell
python -m pytest backend/tests
```

The health endpoint checks only that the API process responds. It does not check a database or model provider.

## Engineering decisions

We start with a modular FastAPI application and PostgreSQL with pgvector for the planned RAG pipeline. We will establish a retrieval baseline before adding approximate indexes, reranking, Redis, LangGraph, or MCP. This is a local single-user prototype until authentication and authorization are implemented.
