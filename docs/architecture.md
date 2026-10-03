# Architecture — Phase 0.1 decision record

## Problem and first use case

Enterprise documents are hard to search reliably. The first vertical slice lets a local user create a workspace and knowledge base, upload supported documents, and ask questions answered from retrieved passages with verifiable source metadata.

The initial dataset should be publicly shareable product or technical documentation. The exact document set and labeled questions will be selected before the ingestion work begins.

## MVP boundary

- API operations for workspaces and knowledge bases.
- Text, Markdown, and text-based PDF ingestion with explicit processing failures.
- Parsing, deterministic chunking, embeddings, and scoped exact vector retrieval.
- Evidence-grounded answers with validated document/page or section/chunk references; abstention when evidence is insufficient.
- A small labeled question set and API/retrieval/end-to-end tests.

This is a local single-user prototype. Workspace and knowledge-base filters organize data, but do not provide authorization. Multi-user deployment requires authentication and permission enforcement first.

## Module responsibilities

- `api`: request validation and HTTP response contracts.
- `core`: settings and cross-cutting application behavior.
- `db`: persistence and migrations when the schema is agreed.
- `knowledge`: document ingestion and retrieval use cases.
- `chat`: evidence assembly and answer use cases.
- `providers`: thin model and embedding adapters.

The later agent layer will call permissioned tools through explicit interfaces and reuse the knowledge retrieval service. Evaluation must call the same retrieval, answer, and agent entry points as the application.

## Data flow

Ingestion: upload and validate → store original and processing status → parse → chunk with source metadata → embed → store chunks and vectors in PostgreSQL.

Question answering: question plus workspace/knowledge-base scope → retrieve passages → assemble evidence → call the model → validate references against retrieved passages → return answer and references, or abstain.

## Decisions and revisit triggers

### Modular monolith

**Why:** One FastAPI deployment keeps the first end-to-end path simple while allowing clear module boundaries and tests.

**Trade-off:** Heavy ingestion and API traffic initially share a process. Introduce a durable worker when measured ingestion latency or reliability requires it.

### PostgreSQL + pgvector

**Why:** Metadata and vectors can live in one database. Start with exact search and scoped filtering to establish a quality baseline.

**Trade-off:** A dedicated vector service or approximate index may eventually improve scale. Benchmark data size, recall, and latency before changing storage.

### Thin provider adapters

**Why:** Keep model-specific request and response details outside the application use cases.

**Trade-off:** Avoid a general multi-provider framework until a second provider is actually integrated and exposes common requirements.

## Deferred design decisions

The exact database fields, API routes, file-size limits, embedding model, citation response schema, and authentication model will be reviewed with the project owner before implementation. Redis, LangGraph, MCP, hybrid retrieval, reranking, and frontend work are outside the first vertical slice.
