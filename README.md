# 企业知识与工作流智能体平台

这是一个面向学习与求职展示的企业知识和工作流智能体平台，强调可测试、可解释的工程实现。

## 当前进度

阶段 0.2 已搭建后端基础，阶段 0.3 已建立 PostgreSQL 元数据层与工作空间、知识库接口。阶段 1.1 按[文档入库设计](docs/phase1-document-ingestion.md)加入文本和 Markdown 单文件同步上传、原文件保存、解析状态查询，以及只读文件对账。2026-10-10 使用真实 PostgreSQL 完成当时的全部 52 项测试，并在临时空数据库通过 Alembic 迁移与模型一致性检查。解析成功时状态为 `parsed`，尚不可检索。向量检索、问答、身份认证、智能体和前端属于后续开发。规划范围见[架构设计](docs/architecture.md)、[开发路线图](docs/roadmap.md)与[学习记录](docs/learning-progress.md)。

阶段 1.2 已完成确定性字符切块与 PostgreSQL 文本块持久化。每块保存文档 ID、顺序编号、内容和解析文本字符范围；同配置重复请求复用原来的块 UUID，不增加记录。文档行锁与整批事务处理并发和中途失败，不同参数或算法版本返回冲突。切块通过独立接口触发，上传后仍为 `parsed`。见 [ADR 0002](docs/adr/0002-deterministic-text-chunks.md) 与 [ADR 0003](docs/adr/0003-idempotent-chunk-persistence.md)。

阶段 1.3 已实现向量服务适配器、1024 维 pgvector 保存和独立触发接口。首个模型由学习者选定为百炼 `text-embedding-v4`；模型请求放在事务外，全部向量和 `ready` 状态在同一事务提交，同配置重试复用结果。已使用模拟 HTTP 与真实 PostgreSQL 验证故障和事务，真实模型调用待本机配置，检索和问答接口尚未实现。模型选择、并发费用与事务取舍见 [ADR 0004](docs/adr/0004-embedding-generation-and-storage.md)。

2026-10-10 完整本地回归为 **213 项通过、无跳过**，其中 20 项验证向量持久化，41 项模拟 HTTP，27 项验证向量协议，6 项验证配置。临时库通过升级、降级后再升级及 Alembic 模型一致性检查；本机项目库已升级到 `20261010_0004`，已有文档和文本块保留。

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

上传文件默认保存在仓库根目录的 `uploads/`，可用 `EAAP_UPLOAD_ROOT` 指向其他目录；`EAAP_MAX_UPLOAD_BYTES` 设置单文件字节上限，默认值为 5 MiB。端点按实际读取字节数检查上限。部署到共享环境时，还需要在入口层设置包含表单开销的请求体总量上限。

当前可在 `/docs` 调用九个资源接口：

- `POST /api/v1/workspaces` 和 `GET /api/v1/workspaces/{workspace_id}`；
- `POST /api/v1/workspaces/{workspace_id}/knowledge-bases` 和 `GET /api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}`。
- `POST /api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents` 和 `GET /api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents/{document_id}`。
- 在上面的具体文档路径后追加 `/chunks`，使用 `POST` 保存切块、`GET` 按编号读取切块。
- 在具体文档路径后追加 `/embeddings`，使用 `POST` 生成并保存向量，不需要请求体。

知识库名称在同一工作空间内重复时返回 `409`，路径中的父子资源不匹配时返回 `404`。上传接口的表单字段名是 `file`；首批支持 UTF-8 的 `.txt`、`.md`。解析失败仍会创建文档记录，响应中的 `failed` 状态和安全错误码用于判断原因。

切块接口请求体为 `{"chunk_size": 500, "overlap": 50}`，发送 `{}` 使用默认值。首次保存和同配置重试均返回 `200` 和块列表；未切块时查询返回空列表。参数或算法版本与已有结果不同、文档未成功解析时返回 `409`；整数或重叠参数无效、预估处理量超限时返回 `422`。单批上限为 10,000 块和累计 10 × 1024 × 1024 个字符，避免高重叠放大内存用量。原文件不可读或切块状态异常返回安全的 `500`，具体原因记录在服务端日志中。

字符位置用于切片解析文本，不是原文件字节偏移；同一份文档的原文件与切块配置当前均不支持原地修改。保存块并不表示已经可检索。

## 向量服务配置与验证

本轮先完成本地代码和故障测试。需要真实联调时，在本机 `.env` 配置以下字段，密钥不发到聊天或提交到 Git：

```dotenv
EAAP_EMBEDDING_BASE_URL=从控制台复制的兼容基础地址
EAAP_EMBEDDING_API_KEY=本机密钥
EAAP_EMBEDDING_MODEL=text-embedding-v4
EAAP_EMBEDDING_DIMENSIONS=1024
```

基础地址必须使用 HTTPS，末尾为 `/compatible-mode/v1`，不要追加 `/embeddings`，也不要原样使用业务空间占位符。地域和业务空间对应的地址见[百炼官方接口说明](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api)。未配置时已有上传和切块仍可用，向量接口返回 `503`。

通过 `/docs` 依次上传文本或 Markdown、调用 `POST .../chunks`、调用 `POST .../embeddings`。成功响应示例中的块数取决于文档：

```json
{
  "document_id": "返回的文档 UUID",
  "status": "ready",
  "model": "text-embedding-v4",
  "dimensions": 1024,
  "chunk_count": 10,
  "reused": false
}
```

再次调用同配置向量接口应返回 `reused: true`，不会再调用模型。`ready` 当前表示完整向量已保存，检索接口留到阶段 1.4；模拟测试不能证明真实模型效果。

默认每批 10 段、单次最多 200 块，可通过 `.env.example` 中的字段调整；HTTP 操作默认等待上限 30 秒，总处理预算 120 秒。同步读取后的预算检查不是严格墙钟截止。同一知识库固定接口身份、模型和维度；更换模型当前使用新知识库。生成失败保留 `parsed` 和已有文本块，方便重试。并发首次请求或数据库提交失败后的重试可能产生重复模型费用。

未解析、未切块或配置冲突返回 `409`，块数超限返回 `422`，模型调用或结果异常返回 `502`，超时返回 `504`；已有向量与状态不一致返回 `500`。单段输入还受服务商令牌上限约束，默认 500 字符切块用于首批小文件，真实语料需在联调时验证。

## 本地测试

首次创建数据库卷时，Compose 会额外建立独立的 `eaap_test` 测试库并启用 `vector` 扩展。已有数据库卷的初始化脚本不会重跑，可执行一次：

```powershell
docker compose -f compose.yaml exec db psql -U eaap -d eaap_test -c "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"
```

在仓库根目录、数据库服务运行时执行完整测试：

```powershell
$env:EAAP_TEST_DATABASE_URL = "postgresql+psycopg://eaap:eaap_local_only@127.0.0.1:5432/eaap_test"
python -m pytest backend/tests
Remove-Item Env:EAAP_TEST_DATABASE_URL
```

数据库测试在测试库中建立临时模式，结束后删除；未设置 `EAAP_TEST_DATABASE_URL` 时会跳过数据库集成测试。不要把测试地址指向日常使用的 `eaap` 数据库。

在相同测试环境下，`python -m pytest backend/tests/test_chunk_persistence.py -q` 单独运行 43 项切块持久化测试；`python -m pytest backend/tests/test_chunking.py -q` 单独运行 24 项纯算法测试。

`python -m pytest backend/tests/test_embedding_persistence.py -q` 运行真实向量持久化与并发故障测试；`python -m pytest backend/tests/test_embedding_http_provider.py -q` 运行模拟 HTTP 测试。这些测试不调用真实模型、不消耗模型接口额度。以后记录检索质量时，必须另用真实模型和标注语料评估。

健康检查接口只确认应用进程能够响应，不检查数据库或模型服务是否可用。

## 文档存储对账

进程在文件保存后、数据库提交前退出，可能留下孤儿文件。在已激活虚拟环境、数据库可用且迁移完成时，从仓库根目录运行：

```powershell
python -m app.knowledge.audit
```

命令读取当前 `EAAP_DATABASE_URL` 和 `EAAP_UPLOAD_ROOT`，只读核对全部文档记录与本地文件，以 JSON 输出孤儿候选文件 `orphan_candidates`、宽限期内未关联文件 `recent_untracked` 和数据库引用的缺失文件 `missing_files`。默认宽限期一小时，可用 `--grace-seconds 3600` 调整。退出码：`0` 表示未发现差异，`1` 表示发现差异，`2` 表示配置或读取失败。

报告可能受并发上传影响，宽限期不能证明文件可以删除；排查时建议暂停上传并重复核对。命令不补建记录、不删除文件，也不修复停留的文档状态。扫描范围仅包含已发布且符合 UUID 命名规则的普通文件，临时文件和未知命名不计入报告。设计取舍与恢复边界见 [ADR 0001](docs/adr/0001-document-storage-audit.md)。

## 工程设计取舍

我们先采用模块化 FastAPI 应用，已使用 PostgreSQL 和 pgvector 保存文本块及向量，逐步构建检索增强生成链路。建立检索质量基线后，再根据需要考虑近似索引、重排、Redis、LangGraph 或 MCP。身份认证和权限控制实现之前，本项目仅是本地单用户原型。
