# 企业知识与工作流智能体平台

这是一个面向学习与求职展示的企业知识和工作流智能体平台，强调可测试、可解释的工程实现。

## 当前进度

阶段 0.2 已搭建后端基础，阶段 0.3 已建立 PostgreSQL 元数据层与工作空间、知识库接口。阶段 1.1 按[文档入库设计](docs/phase1-document-ingestion.md)加入文本和 Markdown 单文件同步上传、原文件保存、解析状态查询，以及只读文件对账。2026-10-10 使用真实 PostgreSQL 完成当时的全部 52 项测试，并在临时空数据库通过 Alembic 迁移与模型一致性检查。解析成功时状态为 `parsed`，还需要切块和向量化。身份认证、智能体和前端属于后续开发。规划范围见[架构设计](docs/architecture.md)、[开发路线图](docs/roadmap.md)与[学习记录](docs/learning-progress.md)。

阶段 1.2 已完成确定性字符切块与 PostgreSQL 文本块持久化。每块保存文档 ID、顺序编号、内容和解析文本字符范围；同配置重复请求复用原来的块 UUID，不增加记录。文档行锁与整批事务处理并发和中途失败，不同参数或算法版本返回冲突。切块通过独立接口触发，上传后仍为 `parsed`。见 [ADR 0002](docs/adr/0002-deterministic-text-chunks.md) 与 [ADR 0003](docs/adr/0003-idempotent-chunk-persistence.md)。

阶段 1.3 已实现向量服务适配器、1024 维 pgvector 保存和独立触发接口。首个模型由学习者选定为百炼 `text-embedding-v4`；模型请求放在事务外，全部向量和 `ready` 状态在同一事务提交，同配置重试复用结果。已使用模拟 HTTP 与真实 PostgreSQL 验证故障和事务，并完成首个小语料的真实向量生成。模型选择、并发费用与事务取舍见 [ADR 0004](docs/adr/0004-embedding-generation-and-storage.md)。

阶段 1.4 已加入限定知识库的精确余弦检索接口。问题使用与文档相同的向量模型转换；SQL 先过滤工作空间、知识库、`ready` 文档和非空向量，再排序取前几项。返回文本、文档名、块编号、字符位置和距离。已在真实模型上验证链路，较大语料的检索质量仍待评测，没有阈值筛选或重排；取舍见 [ADR 0005](docs/adr/0005-scoped-vector-retrieval.md)。

阶段 1.5 加入独立生成适配器与基于证据的 `/answers` 接口。空结果直接弃答，有证据时在事务外请求结构化答案；程序校验引用编号和逐字引文，并返回真实文档、块和引文字符位置。主题相关不一定足够回答，引用存在也不证明结论正确；首轮真实模型联调已完成，广泛的回答质量评估仍待完成。见 [ADR 0006](docs/adr/0006-evidence-grounded-answers.md)。

2026-10-10 已完成[首次真实 RAG 验证](docs/evaluation/live-rag-smoke-2026-10-10.md)：虚构技术文档切成 5 块，百炼生成向量，DeepSeek 对 6 个固定问题给出 4 个带引用的答案、2 个合理弃答。原始应用响应、题集与可复跑脚本已入库。只有 5 个候选且取前 5 个，这次证明链路可运行，尚不能作为检索质量成绩；未收集 token、费用或部署网关数据。

阶段 1.6 已完成[带干扰资料的检索评测](docs/evaluation/retrieval-eval-2026-10-10.md)：独立知识库有 4 份公开虚构资料、29 个候选块和 6 个固定问题。按学习者选择，以支持问题的连续原文验收，见 [ADR 0007](docs/adr/0007-evidence-level-retrieval-evaluation.md)。真实百炼检索中，同一次前 10 响应的前 5 与前 10 均完整覆盖 4 道正例的 5 条必需证据；2 道负例不计覆盖分。这是小型固定证据基线，不是正式 Recall@K；本轮未调用回答模型，扩大语料后的回答忠实性与弃答仍待评估。

2026-10-10 最新完整本地回归为 **453 项通过、无跳过**（150.93 秒），本轮新增 31 项证据覆盖测试和 23 项评测脚本测试。回归使用真实 PostgreSQL/pgvector 与受控模型，不调用真实模型；真实联调与检索评测单独执行。两者都不能替代广泛的语义质量评测。本机项目库沿用 `20261010_0004`，本轮没有修改数据库结构；该迁移此前在临时库通过升级、降级后再升级及模型一致性检查。

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

当前可在 `/docs` 调用十一个资源接口：

- `POST /api/v1/workspaces` 和 `GET /api/v1/workspaces/{workspace_id}`；
- `POST /api/v1/workspaces/{workspace_id}/knowledge-bases` 和 `GET /api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}`。
- `POST /api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents` 和 `GET /api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents/{document_id}`。
- 在上面的具体文档路径后追加 `/chunks`，使用 `POST` 保存切块、`GET` 按编号读取切块。
- 在具体文档路径后追加 `/embeddings`，使用 `POST` 生成并保存向量，不需要请求体。
- `POST /api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/search`：在指定知识库内检索相关段落。
- `POST /api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/answers`：基于检索证据回答或弃答，返回逐字引用。

知识库名称在同一工作空间内重复时返回 `409`，路径中的父子资源不匹配时返回 `404`。上传接口的表单字段名是 `file`；首批支持 UTF-8 的 `.txt`、`.md`。解析失败仍会创建文档记录，响应中的 `failed` 状态和安全错误码用于判断原因。

切块接口请求体为 `{"chunk_size": 500, "overlap": 50}`，发送 `{}` 使用默认值。首次保存和同配置重试均返回 `200` 和块列表；未切块时查询返回空列表。参数或算法版本与已有结果不同、文档未成功解析时返回 `409`；整数或重叠参数无效、预估处理量超限时返回 `422`。单批上限为 10,000 块和累计 10 × 1024 × 1024 个字符，避免高重叠放大内存用量。原文件不可读或切块状态异常返回安全的 `500`，具体原因记录在服务端日志中。

字符位置用于切片解析文本，不是原文件字节偏移；同一份文档的原文件与切块配置当前均不支持原地修改。保存块并不表示已经可检索。

## 向量服务配置与验证

在本机 `.env` 配置以下字段以使用真实向量服务，密钥不发到聊天或提交到 Git：

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

再次调用同配置向量接口应返回 `reused: true`，不会再调用模型。`ready` 表示完整向量已保存，可作为检索候选；模拟测试不能证明真实模型效果。

默认每批 10 段、单次最多 200 块，可通过 `.env.example` 中的字段调整；HTTP 操作默认等待上限 30 秒，总处理预算 120 秒。同步读取后的预算检查不是严格墙钟截止。同一知识库固定接口身份、模型和维度；更换模型当前使用新知识库。生成失败保留 `parsed` 和已有文本块，方便重试。并发首次请求或数据库提交失败后的重试可能产生重复模型费用。

未解析、未切块或配置冲突返回 `409`，块数超限返回 `422`，模型调用或结果异常返回 `502`，超时返回 `504`；已有向量与状态不一致返回 `500`。单段输入还受服务商令牌上限约束，默认 500 字符切块用于首批小文件，真实语料需在联调时验证。

## 知识库检索

完成真实服务配置、文档切块和向量化后，通过 `/docs` 调用知识库的 `/search` 接口：

```json
{"query": "服务重启后如何恢复？", "top_k": 5}
```

问题去掉首尾空白后必须非空，最多 2000 字符；`top_k` 默认 5，必须为 1 至 20 的整数，不接受额外字段。响应包含 `metric: "cosine_distance"`、`model`、`dimensions` 和 `results`；每个结果包含 `chunk_id`、`document_id`、`document_name`、`chunk_index`、`content`、`start_char`、`end_char`、`distance`。不返回磁盘存储键或向量数组。

余弦距离范围为 0 至 2，越小越接近；相同距离按块 UUID 升序，保证排序稳定。距离不是答案正确率。接口最多返回指定条数，候选不足就返回实际条数，也可能为空；当前不判断这些段落是否足以回答问题。

模型配置正常时，空知识库或没有可检索块返回 `200` 和空结果，不调用模型；尚未建立模型配置时，响应中的 `model`、`dimensions` 为 `null`。HTTP 接口仍需要配置向量提供方依赖，未配置时返回 `503`。知识库不存在或归属不符返回 `404`，查询模型与已有向量配置不同返回 `409`，无效参数返回 `422`，模型故障返回 `502`、超时返回 `504`，选中数据的距离异常返回 `500`。

查询向量在数据库事务外生成，调用前后都检查知识库归属和完整模型身份；数据库读取阶段用短暂共享锁保持配置稳定。每次有候选的查询都会生成问题向量，当前没有查询缓存。资源路径只限定数据范围，当前仍未实现身份认证与用户权限控制。

## 基于证据的回答

学习者已选择 DeepSeek 官网的 `deepseek-flash` 作为首个回答模型，先建立问答基线；向量生成继续使用百炼 `text-embedding-v4`、1024 维。两类模型分别配置，新密钥只填写本机 `.env`。`.env.example` 已记录生成模型与官网基础地址，已有 `.env` 需要手动同步这两个字段，勿整体覆盖其他配置。

```dotenv
EAAP_GENERATION_BASE_URL=https://api.deepseek.com
EAAP_GENERATION_API_KEY=本机密钥
EAAP_GENERATION_MODEL=deepseek-flash
```

基础地址不要追加 `/chat/completions`；适配器使用兼容的非流式 Chat Completions 与 JSON 模式。支持 JSON 模式的模型也需要提示与程序校验，见 [DeepSeek 官方接口说明](https://api-docs.deepseek.com/api/create-chat-completion/)。学习者自行完成本机配置后，已调用真实模型完成首轮六题验证。

模型选择依据：当前官方列出的 Flash 价格低于 Pro，适合首轮反复调试；以后再用同一组标注问题比较 `deepseek-v4-pro` 的答案质量和成本。官网模型名称与基础地址见[官方模型与价格](https://api-docs.deepseek.com/quick_start/pricing/)。模型别名可能随服务商更新，真实验收时需记录返回的模型信息。当前适配器未显式关闭思考模式，输出预算与等待时间需要在联调时检查。

在 `/docs` 调用知识库 `/answers`，请求示例：

```json
{"question": "如何提交退款申请？", "top_k": 5}
```

问题去首尾空白后为 1 至 2000 字符，`top_k` 默认 5，严格整数 1 至 20，拒绝额外字段。正常回答返回 `answer`、`abstained: false` 和非空 `citations`。每条引用含 `chunk_id`、`document_id`、`document_name`、`chunk_index`、`quote`、`start_char`、`end_char`；字符位置对应引文在解析文本中的 `[start_char, end_char)`。同一块内引文重复时选择首次出现，来源信息由程序组装。

无检索结果或模型判断资料不足时返回 `200`：

```json
{"answer": "目前检索到的资料不足以确认这个问题的答案。", "abstained": true, "citations": []}
```

空结果跳过生成模型，不要求生成地址、密钥和模型名；HTTP 接口仍需要有效的向量配置。数值配置由全局 `Settings` 在启动时校验，必须处于声明范围。模型按契约弃答是正常业务结果。伪造来源、引文不存在、坏 JSON 或截断输出返回 `502`；有证据但生成地址、密钥或模型配置缺失或无效返回 `503`，模型超时返回 `504`。技术故障不会转换成“资料没有答案”。知识库范围、模型冲突与检索数据错误沿用检索接口的错误处理。

默认按排名选能完整放入 12000 字符预算的文本块，过长块跳过，不截断；全部块都放不下返回 `422`。用 `EAAP_GENERATION_MAX_CONTEXT_CHARACTERS` 调整字符预算；它不等于服务商 token 上限。`EAAP_GENERATION_MAX_TOKENS` 默认 2000，HTTP 操作默认等待 30 秒，总处理预算 60 秒；读取后的预算检查不是严格墙钟取消，具体模型的推理 token 与输入上限需要真实联调确认。

当前单次同步回答，没有会话保存、自动重试或缓存。提示把资料视为不可信数据，要求仅据证据回答或弃答；程序检查引用归属与原文存在，语义支持程度和提示注入抵抗能力仍需要评估。

## 本地测试

真实联调使用 `backend/scripts/live_rag_smoke.py --run`，会保存本机演示数据并产生 API 费用；复用本次资源用 `--resume`，新机器用新的 `--report` 路径。执行方法、响应报告、逐条复核与范围见[联调说明](docs/evaluation/live-rag-smoke-2026-10-10.md)。脚本独立运行，不属于下面的自动化回归。

检索评测先运行 `python backend/scripts/evaluate_retrieval.py --plan`，仅检查公开语料、标注与预计块数。真实执行使用 `--run --report docs/evaluation/retrieval-eval-local.json`，产生向量 API 用量；仅保留演示数据库资源的本机可用 `--resume`。该脚本不调用回答模型，详细复现步骤与统计边界见[检索评测说明](docs/evaluation/retrieval-eval-2026-10-10.md)。

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

使用同样的测试数据库配置，`python -m pytest backend/tests/test_retrieval.py -q` 验证知识库范围、余弦排序、来源定位和模型故障。向量由测试替身控制，用来验证数据库与接口行为；真实小语料已记录一次延迟，更大语料的 Recall@5、语义效果和延迟分布尚待评测。

`python -m pytest backend/tests/test_answers.py backend/tests/test_generation_http_provider.py -q` 验证问答链路、引用、弃答和生成故障，使用模拟模型，不消耗 API 额度。这不代表真实模型已经能可靠判断证据或生成忠实答案。

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
