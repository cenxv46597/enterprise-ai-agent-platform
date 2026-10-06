# Roadmap

This roadmap tracks implementation and evidence. A planned capability is not a completed feature.

## Phase 0 — foundation

1. **0.1 Architecture:** agree on the problem, MVP boundary, module flow, and trade-offs. Complete.
2. **0.2 Backend foundation:** initialize local Git, document decisions, add settings, a FastAPI health endpoint, and an API test.
3. **0.3 API and data design:** review Workspace, Knowledge Base, Document, and Chunk relationships and HTTP contracts together. Start from the [draft proposal](data-api-design-proposal.md), then add PostgreSQL/pgvector, Alembic migrations, and a database integration smoke test.

## Phase 1 — measurable RAG MVP

1. Create workspace and knowledge-base API operations.
2. Ingest Text/Markdown, then text-based PDF; preserve source metadata and failure states.
3. Add deterministic chunking, embeddings, and exact pgvector search scoped by workspace/knowledge base.
4. Build a small labeled question set; record retrieval Recall@5 and latency.
5. Generate evidence-grounded answers, validate citations, and abstain when unsupported.
6. Run API, retrieval, and end-to-end tests from a clean database.

## Later phases

- Advanced RAG: hybrid retrieval, query rewrite, reranking, citation quality, and comparative evaluation.
- Agent: bounded loop, state, tool registry, tool selection, and workflow execution before adopting LangGraph.
- Advanced Agent: memory, MCP, multi-step workflows, and recovery.
- Production engineering: authentication, RBAC, Redis when needed, tracing, limits, and deployment.
- Evaluation, frontend, and resume evidence follow the implemented and measured system.
