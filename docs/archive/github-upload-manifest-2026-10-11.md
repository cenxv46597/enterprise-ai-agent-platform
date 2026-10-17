# GitHub 首次上传清单：2026-10-11

> 状态：开发与验证已完成，学习者已确认按本清单提交和推送。以下内容为上传前检查快照；实际提交与远程同步结果以 Git 记录为准。

## 推送目标与历史

- 仓库：[cenxv46597/enterprise-ai-agent-platform](https://github.com/cenxv46597/enterprise-ai-agent-platform)
- 远程：`origin`；分支：`main`。
- 本地已有 27 个历史提交；首次推送将上传已有历史和确认后的新提交。
- 目标仓库已只读核对，目前无分支或标签；不使用强制推送。

## 拟用提交信息

`实现：增加上下文证据覆盖评测并改用本机独立数据库口令`

## 本轮内容与验证

- 抽取共用的完整块选择函数，保持现有问答策略与引用行为。
- 增加离线上下文覆盖评测、执行脚本、故障测试和公开报告。
- 比较 36 次选择：600、2000、12000 字符预算覆盖 2/5、4/5、5/5 条必需证据，没有新模型调用。
- 共享函数抽取后完整回归 482 项通过、无跳过，165.37 秒；补充快照跨换行身份后，新模块 30 项专项全部通过。
- 更新中文 README、架构、路线图、ADR 和学习记录，加入确认后提交及推送的日常流程。
- 按学习者授权轮换本机 PostgreSQL 实际角色口令，将 Compose 与连接地址改为从 `.env` 读取独立随机值；新增凭据管理说明与 ADR 0009。
- 轮换后最新完整回归 **483 项通过、无跳过，146.59 秒**；新配置只读核对 Alembic 仍为 `20261010_0004`，未修改数据库结构。

## 上传边界

- 排除 `.env`、本机环境配置、密钥文件、虚拟环境、缓存、运行日志、上传资料和依赖目录。
- `.env.example` 保留公开配置示例，真实 API 密钥字段为空。
- `.env.example` 的数据库口令字段为空，Compose 强制要求本机非空口令；真实新口令仅在被忽略的 `.env` 中保存。
- 历史中的旧默认数据库口令最早出现在 `00ceea5`，共涉及 24 次历史提交。旧口令曾能登录本机数据库，已实际轮换并验证失效；历史保留原样，不宣称已经清除历史中的旧口令文本。
- 轮换后复查当前 102 个上传文件及全部 27 次历史提交，未发现本机现用数据库口令或本机现用 API 密钥的字面值，当前文件未发现高置信密钥格式；静态扫描不能保证所有形式的秘密均不存在。
- 已有真实响应只包含公开虚构资料；没有上传聊天记录或本机数据库文件。
- README 已存在且为中文；继续依据实际完成能力更新，不新增英文版或虚构功能。

## 本轮待提交文件

共 20 个文件（本机 `.env` 已更新，但被忽略，不在清单中）：

- `.gitignore`
- `.env.example`
- `README.md`
- `backend/app/evaluation/context.py`
- `backend/app/knowledge/answers.py`
- `backend/app/knowledge/context.py`
- `backend/scripts/evaluate_context.py`
- `backend/tests/test_context_evaluation.py`
- `backend/tests/test_context_evaluation_cli.py`
- `docs/adr/0008-context-evidence-coverage.md`
- `docs/adr/0009-local-database-credentials.md`
- `docs/architecture/architecture.md`
- `docs/evaluation/context-eval-2026-10-11.json`
- `docs/evaluation/context-eval-2026-10-11.md`
- `docs/archive/github-upload-manifest-2026-10-11.md`
- `docs/operations/github-workflow.md`
- `docs/progress/learning-progress.md`
- `docs/local-database-credentials.md`
- `docs/progress/roadmap.md`
- `compose.yaml`

## 首次上传后的完整项目文件

共 102 个文件（不含 `.git` 本身及忽略的本机文件）：

- `.env.example`
- `.gitattributes`
- `.gitignore`
- `README.md`
- `backend/alembic.ini`
- `backend/alembic/env.py`
- `backend/alembic/script.py.mako`
- `backend/alembic/versions/20261006_0001_metadata.py`
- `backend/alembic/versions/20261008_0002_document_parsed.py`
- `backend/alembic/versions/20261010_0003_document_chunks.py`
- `backend/alembic/versions/20261010_0004_chunk_embeddings.py`
- `backend/app/__init__.py`
- `backend/app/api/__init__.py`
- `backend/app/api/answers.py`
- `backend/app/api/documents.py`
- `backend/app/api/health.py`
- `backend/app/api/knowledge_bases.py`
- `backend/app/api/search.py`
- `backend/app/api/workspaces.py`
- `backend/app/core/__init__.py`
- `backend/app/core/config.py`
- `backend/app/db/__init__.py`
- `backend/app/db/base.py`
- `backend/app/db/models.py`
- `backend/app/db/session.py`
- `backend/app/evaluation/__init__.py`
- `backend/app/evaluation/context.py`
- `backend/app/evaluation/retrieval.py`
- `backend/app/knowledge/__init__.py`
- `backend/app/knowledge/answers.py`
- `backend/app/knowledge/audit.py`
- `backend/app/knowledge/chunk_repository.py`
- `backend/app/knowledge/chunking.py`
- `backend/app/knowledge/context.py`
- `backend/app/knowledge/embedding_repository.py`
- `backend/app/knowledge/ingestion.py`
- `backend/app/knowledge/retrieval.py`
- `backend/app/knowledge/storage.py`
- `backend/app/main.py`
- `backend/app/providers/__init__.py`
- `backend/app/providers/compatible_embeddings.py`
- `backend/app/providers/compatible_generation.py`
- `backend/app/providers/embeddings.py`
- `backend/app/providers/generation.py`
- `backend/pyproject.toml`
- `backend/scripts/evaluate_context.py`
- `backend/scripts/evaluate_retrieval.py`
- `backend/scripts/live_rag_smoke.py`
- `backend/tests/test_answers.py`
- `backend/tests/test_chunk_persistence.py`
- `backend/tests/test_chunking.py`
- `backend/tests/test_context_evaluation.py`
- `backend/tests/test_context_evaluation_cli.py`
- `backend/tests/test_db_constraints.py`
- `backend/tests/test_document_http_without_db.py`
- `backend/tests/test_document_storage.py`
- `backend/tests/test_document_upload.py`
- `backend/tests/test_embedding_configuration.py`
- `backend/tests/test_embedding_contract.py`
- `backend/tests/test_embedding_http_provider.py`
- `backend/tests/test_embedding_persistence.py`
- `backend/tests/test_generation_http_provider.py`
- `backend/tests/test_health.py`
- `backend/tests/test_ingestion_service.py`
- `backend/tests/test_live_rag_smoke.py`
- `backend/tests/test_resources.py`
- `backend/tests/test_retrieval.py`
- `backend/tests/test_retrieval_evaluation.py`
- `backend/tests/test_retrieval_evaluation_cli.py`
- `backend/tests/test_storage_audit.py`
- `compose.yaml`
- `docs/adr/0001-document-storage-audit.md`
- `docs/adr/0002-deterministic-text-chunks.md`
- `docs/adr/0003-idempotent-chunk-persistence.md`
- `docs/adr/0004-embedding-generation-and-storage.md`
- `docs/adr/0005-scoped-vector-retrieval.md`
- `docs/adr/0006-evidence-grounded-answers.md`
- `docs/adr/0007-evidence-level-retrieval-evaluation.md`
- `docs/adr/0008-context-evidence-coverage.md`
- `docs/adr/0009-local-database-credentials.md`
- `docs/architecture/architecture.md`
- `docs/architecture/data-api-design-proposal.md`
- `docs/evaluation/context-eval-2026-10-11.json`
- `docs/evaluation/context-eval-2026-10-11.md`
- `docs/evaluation/live-rag-smoke-2026-10-10.json`
- `docs/evaluation/live-rag-smoke-2026-10-10.md`
- `docs/evaluation/retrieval-eval-2026-10-10.json`
- `docs/evaluation/retrieval-eval-2026-10-10.md`
- `docs/archive/github-upload-manifest-2026-10-11.md`
- `docs/operations/github-workflow.md`
- `docs/progress/learning-progress.md`
- `docs/local-database-credentials.md`
- `docs/backend/phase1-document-ingestion.md`
- `docs/backend/phase1-retrieval-evaluation.md`
- `docs/progress/roadmap.md`
- `examples/evaluation/rag-smoke-cases.json`
- `examples/evaluation/retrieval-eval-cases.json`
- `examples/knowledge/demo-product.md`
- `examples/knowledge/retrieval-eval/demo-export-records.md`
- `examples/knowledge/retrieval-eval/demo-log-observability.md`
- `examples/knowledge/retrieval-eval/demo-permissions-collaboration.md`
- `infra/postgres/init/01-create-test-db.sql`
