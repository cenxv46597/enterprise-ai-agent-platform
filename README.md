# 企业知识与工作流智能体平台

这是一个面向学习与求职展示的企业知识和工作流智能体平台，强调可测试、可解释的工程实现。

## 当前进度

目前已有可运行的**本地单用户 RAG 后端**，完整企业级平台仍在开发。

- **文档链路：** 工作空间与知识库管理，TXT/Markdown 上传、磁盘保存、解析、确定性切块、事务与重复请求复用。
- **问答链路：** 百炼 1024 维向量、PostgreSQL/pgvector 知识库检索、DeepSeek 基于证据回答、逐字引用校验和证据不足时弃答。
- **检索增强：** [阶段 2.1](docs/phase2-hybrid-retrieval.md)已接入可选词项与向量混合策略，按块 ID 合并、融合名次并返回各路排名。小规模处理上限明确，真实两路召回收益尚待验收。
- **验证能力：** 文件对账、真实数据库故障测试、检索及上下文覆盖、逐项语义审核、模型辅助评审、生成输入快照和固定证据提示词对比。
- **已有真实演示：** [首次六题联调](docs/evaluation/live-rag-smoke-2026-10-10.md)给出四个回答和两个弃答；[带干扰资料检索](docs/evaluation/retrieval-eval-2026-10-10.md)使用四份文档、二十九个候选块。它们是小规模链路与证据覆盖验证，不能推广为企业资料的质量成绩。
- **当前验收：** [提示词对比](docs/phase1-prompt-comparison.md)支持六题全选、显式十二次尝试预算和缺失快照预检；受控模拟不调用模型。新的真实六题输入、真实配对与逐项语义核对尚未完成。
- **候选重排：** [阶段 2.2](docs/phase2-candidate-reranking.md)可选先召回二十段、再由已有生成模型排序，验证完整编号排列并保留引用来源；超时或输出故障明确标记降级。默认关闭，真实排序收益尚待验收。
- **知识智能体：** [阶段 3.1](docs/phase3-knowledge-agent.md)已有有界运行、只读知识工具、故障观察与引用校验；[阶段 3.2](docs/phase3-native-tools.md)增加原生工具调用、同编号结果回传和历史隔离，旧 JSON 协议可显式选择。多调用整体拒绝，重复动作与证据保护仍由后端执行；真实任务质量待验收。
- **受限计算器：** [阶段 3.3](docs/phase3-calculator.md)保留两个原文数值的十进制四则运算；[阶段 4.2](docs/phase4-sourced-calculations.md)新增会话内来源引用式计算器，接受已确认用户字段及当前运行先前成功的结果，不允许模型自由填写值。最多八次决策、七次成功计算；暂不做百分比解释、单位换算或业务公式验证。
- **会话接续：** [阶段 4.1](docs/phase4-conversation-state.md)保存会话、结构化待补条件与有界近期决策上下文；“三次”绑定到待补字段后跨请求恢复为 ready。随后用 `mode=execute` 显式恢复来源绑定算术，运行结果与轮次共同保存；ready 不代表年度费用任务完成。
- **受控业务流程：** [阶段 4.3](docs/phase4-standard-billing.md)新增标准月费加上门次费估算。完整原文绑定费率，逐项补齐次数及 1..12 个计费月数，用户显式确认规则适用后，由程序固定三步计算。completed 仅代表该标准估算，不是实际账单、扣费或通用政策验证。
- **显式答复偏好：** [阶段 4.4](docs/phase4-answer-preferences.md)已实现知识库级简洁/详细设置、版本化更新与清除，不从聊天自动抽取。只影响会话最终有证据生成，不能改变证据、工具或计费公式；完整程序回归通过、应用库已升级至 0008。这是本地单用户设置，不是私有用户记忆或完整长期记忆平台，真实风格与忠实性未知，学习者实践仍待完成。
- **只读 MCP 接入：** [阶段 4.5](docs/phase4-mcp-search.md)已实现并完成程序验收：自建本地 stdio Server、官方 Python SDK Client 和显式工具白名单；单次 Agent 可选择 MCP 搜索，Host 整批回验知识库范围与完整原文。默认本地搜索不变，不开放删除或任意命令，不静默回退。真实非空向量/模型端到端质量和学习者实践尚未验收。
- **检查点问答：** [阶段 4.6](docs/phase4-answer-workflows.md)独立固定两步工作流程序验收完成：创建任务、显式领取执行权、保存检索检查点、生成并校验引用、保存不可变回执；合法检查点在新请求中复验来源后继续生成，过期中断执行需显式接管。不是任意 Agent 恢复，也不能保证外部模型费用恰好一次；个人实践与真实语义质量待验收。
- **后续主线：** 先完成阶段 4.6 个人实践与逐题面试，再确认下一模块；后续计划权限、部署和前端，根据明确需求扩展长期记忆与工作流，同时完成真实检索和智能体任务验收、补齐 PDF 与资源列表。当前无身份认证或用户界面，上传仅支持 UTF-8 TXT/Markdown、默认 5 MiB。

阶段 4.3 最终历史完整回归 **1273 项通过、无跳过（523.88 秒）**，使用独立真实 PostgreSQL/pgvector 与受控模型，覆盖标准计费两条件恢复、固定公式、规则确认与结果、迁移、来源引用式链式算术、持久会话、原生协议、检索增强和既有六题工具。阶段 4.3 新增 139 项测试；真实模型选对收费原文与工具、实际业务语义仍待验收，没有新增真实模型调用。本机应用库已升级为 `20261011_0007`，既有六张表记录数保持一致。首轮完整 1272 项通过后另补引文限长保护，最终版本重新全量通过；此前各阶段的临时目录权限等故障仍保留在[学习记录](docs/learning-progress.md)。该结果不能用于宣称阶段 4.4 新代码已通过完整回归。测试数量不代表项目完成比例，也不替代模型语义验收。

阶段 4.4 恢复前纯程序/模拟 HTTP 专项 **324 项通过、无跳过（3.11 秒）**，包含此前
311 项而不相加；当时 **1361 项收集（2.48 秒）**不是全量执行。锁顺序 fake-op 仅验证
迁移调用顺序，不是真实并发锁验收。Docker Desktop 初始化 dockerInference 报文件无法访问，
当时进程启动但引擎未就绪、5432 检查为 False，数据库/API 用例未运行到断言。
用户随后恢复 Docker，既有数据库容器启动 Healthy，没有删除或重建卷。恢复后专项
**69 项通过、无跳过**，包括偏好 API、会话集成、故障、迁移及纯程序用例，不全是数据库测试。
pytest 报告 **4087.46 秒（1:08:07）**仅作执行记录，不是接口性能或正常耗时，不推断等待原因。
最终完整后端回归 **1361 项通过、无跳过**，独立 eaap_test PostgreSQL 与模拟模型，本轮
新增 88 项测试（1273 → 1361）。pytest 原样报告 **9840.44 秒（2:44:00）**仅作执行记录，
不作接口性能或正常耗时指标，不推断等待原因；324 项和 69 项专项是重叠子集，不能相加。
应用库已正常从 0007 升级至 0008，六张旧表记录数前后完全一致：workspaces=2、
knowledge_bases=2、documents=5、chunks=34、conversations=0、conversation_turns=0；
新 answer_preferences=0，compare_metadata=[]。阶段 4.4 程序验收完成；真实风格与忠实性、
学习者实践及三道面试题尚未完成。本轮按完成模块本地提交、不推送，不预填提交编号；真实模型调用为零。

模块完成证据与设计取舍集中在[路线图](docs/roadmap.md)、[架构设计](docs/architecture.md)、[ADR](docs/adr/)和[学习记录](docs/learning-progress.md)。公开演示文档描述的 PDF、管理页、令牌和备份是虚构产品内容，不是本项目已实现能力。

## 本地启动（PowerShell）

需要 Python 3.11 或更高版本，以及可用的 Docker Compose。当前开发环境使用 Python 3.13。在仓库根目录依次执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".\backend[dev]"
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    python -c "import secrets; from dotenv import set_key; set_key('.env', 'EAAP_POSTGRES_PASSWORD', secrets.token_urlsafe(32))"
}
docker compose -f compose.yaml up -d --wait db
python -m alembic -c backend/alembic.ini upgrade head
python -m uvicorn app.main:app --reload
```

打开 `http://127.0.0.1:8000/health` 查看进程健康状态，打开 `http://127.0.0.1:8000/docs` 查看自动生成的接口文档。

首次初始化会生成独立随机口令并写入被 Git 忽略的 `.env`，不会显示口令。已有 `.env` 不会被覆盖：请确认其中已有非空的 `EAAP_POSTGRES_PASSWORD`，并让数据库与测试库连接地址使用同一口令。

`.env.example` 没有可登录的默认密码。Compose 和应用配置分别从本机 `.env` 读取口令，连接地址中的 `${EAAP_POSTGRES_PASSWORD}` 由环境文件加载器展开；如端口或账号不同，请调整本机配置。不要运行会输出完整口令的 `docker compose config`，检查配置可使用 `docker compose config --quiet`。

已有 PostgreSQL 数据卷不会因为修改 Compose 的 `POSTGRES_PASSWORD` 自动更换数据库口令；轮换时还需要修改数据库角色口令，并同步应用配置，见[本机数据库凭据管理](docs/local-database-credentials.md)。数据库结构通过 Alembic 迁移建立，不依赖应用启动时自动建表。

上传文件默认保存在仓库根目录的 `uploads/`，可用 `EAAP_UPLOAD_ROOT` 指向其他目录；`EAAP_MAX_UPLOAD_BYTES` 设置单文件字节上限，默认值为 5 MiB。端点按实际读取字节数检查上限。部署到共享环境时，还需要在入口层设置包含表单开销的请求体总量上限。

当前可在 `/docs` 调用下列资源接口：

- `POST /api/v1/workspaces` 和 `GET /api/v1/workspaces/{workspace_id}`；
- `POST /api/v1/workspaces/{workspace_id}/knowledge-bases` 和 `GET /api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}`。
- `GET /api/v1/workspaces/{workspace_id}/knowledge-bases?limit=20&offset=0`：按工作空间列出知识库，默认每页 20 条，最多 100 条；先限定范围，按创建时间和 ID 升序后分页。返回元数据数组，不执行检索或调用模型；工作空间存在但结果为空返回 `200 []`，不存在返回 `404`。分页不是语义相关性筛选或身份授权，详细契约见[资源接口设计](docs/data-api-design-proposal.md#知识库列表小步补齐)。
- `POST /api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents` 和 `GET /api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents/{document_id}`。
- `GET /api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents?limit=20&offset=0`：分页浏览文档元数据，包含尚未就绪或处理失败的记录及安全错误码；默认 20、最多 100 条。管理列表不读取文件正文、不调用模型，也不改变检索仅使用 `ready` 文档的规则，见[文档列表契约](docs/data-api-design-proposal.md#文档列表小步补齐)。
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

可选重排请求示例：`{"query":"如何提交退款？","strategy":"hybrid","rerank":true,"candidate_k":20,"top_k":5}`。问答改用 `question` 字段。默认关闭，开启后复用生成服务做至多一次额外模型请求；正文总量上限 20000 字符，超限返回 422。`reranking` 提供成功、降级或空库跳过状态，原距离和融合分数保留用于解释召回，重排成功后的最终顺序由模型排列决定。完整协议和测试练习见[阶段 2.2](docs/phase2-candidate-reranking.md)。

`/search` 与 `/answers` 请求均可增加 `"strategy": "hybrid"`，省略时继续使用 `vector`。混合模式分别从词项与向量通道取前二十，再合并排序；搜索响应提供融合得分及两路排名，原 `distance` 仍是余弦距离，混合结果按融合分数排序。当前支持最多 2000 个就绪块和 100 万正文字符，超限返回 `422`；运行和学习实践见[混合检索说明](docs/phase2-hybrid-retrieval.md)。

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

上下文评测使用 `python backend/scripts/evaluate_context.py --plan` 验证已有快照，用 `--write-report --report docs/evaluation/context-eval-local.json` 保存离线比较。无需 `.env`、数据库或模型密钥，结果和学习实践见[上下文评测说明](docs/evaluation/context-eval-2026-10-11.md)。

逐项审核使用 `python backend/scripts/evaluate_answers.py --plan`，没有模型调用。模型辅助评审使用 `python backend/scripts/review_answers.py --plan`，先确认题目与调用预算；只有显式 `--run` 才读取 `EAAP_REVIEW_*` 并产生服务请求。首轮可用 `--case-id restart_recovery_steps --max-cases 1` 限定一题，具体运行、退出码和统计范围见[操作说明](docs/phase1-model-review.md)。

2026-10-11 已完成[首轮真实单题评审](docs/evaluation/model-review-first-case-2026-10-11.md)：调用一次，三项 AI 建议通过，人工审核尚未完成。公开演示脚本另支持 `--capture-generation`，在新报告中保存准备发送的生成消息与参数，供后续固定证据实验；默认关闭，记录失败阻止请求，尚未采集新的真实生成快照。用法与边界见[阶段 1.10](docs/phase1-generation-snapshots.md)。

两版提示词比较使用 `python backend/scripts/compare_prompts.py --plan` 规划，默认一题两次尝试；`--mock` 演练程序且不调用模型，`--run` 要求实际采集的公开快照。保留原问题、资料与顺序，只改变系统提示词，语义质量仍待审核，详见[阶段 1.11](docs/phase1-prompt-comparison.md)与[模拟记录](docs/evaluation/prompt-comparison-mock-2026-10-11.md)。

首次创建数据库卷时，Compose 会额外建立独立的 `eaap_test` 测试库并启用 `vector` 扩展。已有数据库卷的初始化脚本不会重跑，可执行一次：

```powershell
docker compose -f compose.yaml exec db psql -U eaap -d eaap_test -c "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"
```

在仓库根目录、数据库服务运行时执行完整测试：

```powershell
python -c "import os, subprocess, sys; from dotenv import dotenv_values; values = dotenv_values('.env'); os.environ['EAAP_TEST_DATABASE_URL'] = values['EAAP_TEST_DATABASE_URL']; sys.exit(subprocess.call([sys.executable, '-m', 'pytest', 'backend/tests', '-q']))"
```

上述命令从本机 `.env` 读取独立测试库地址，仅传入测试子进程，不显示口令。数据库测试在测试库中建立临时模式，结束后删除；直接运行 pytest 且未设置环境变量 `EAAP_TEST_DATABASE_URL` 时会跳过数据库集成测试。不要把测试地址指向日常使用的 `eaap` 数据库。

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

## 会话状态与短期记忆

在知识库路径后调用 `POST /conversations` 创建会话、`GET /conversations/{conversation_id}`
读取状态、`POST /conversations/{conversation_id}/messages` 提交消息。
消息须携带新的 `request_id`、当前 `expected_version` 与 `content`；重试保持原请求不变。
会话入口可通过 ask_user 保存原问题、规则原文快照和一个非负整数待补字段。
后续“三次”可绑定为全年上门次数 3，错单位或歧义回复保留等待。

会话保存到 PostgreSQL；近期最多三组完整问答、4000 字符只辅助决策，不进入原文证据池。
已提交请求可重放，版本冲突返回 409。`mode=new_task` 显式替换待办并重置任务上下文。
补充成功返回 ready 和 `execution_deferred=explicit_execution_required`，不自动执行。
阶段 4.2 在同一消息接口增加 `mode=execute`，只接受 ready，按保存的原问题、规则与
确认条件执行来源绑定算术。结果明确区分原文、用户输入和程序派生值；不会因算术运行
completed 就声称全年费用任务完成。旧执行结果不会成为下一执行的计算池。
阶段 4.4/4.5 本机应用库已正常升级为 `20261012_0008`；阶段 4.6 新迁移脚本为
`20261012_0009`，本机应用库已另行正常升级至0009，七张旧表计数前后相同，新两表为空。
新环境启动前运行正常 Alembic 升级，
不把脚本存在当作本机已经升级。实际本机升级和测试结果见
[学习记录](docs/learning-progress.md)。数据契约、实践和限制见[阶段 4.1](docs/phase4-conversation-state.md)
与 [ADR 0019](docs/adr/0019-persistent-conversation-clarification.md)。

来源引用参数、显式执行请求、舍入继承和限制见[阶段 4.2](docs/phase4-sourced-calculations.md)
与 [ADR 0020](docs/adr/0020-source-referenced-chained-calculations.md)。存在 execute 轮次时，
新迁移会拒绝无损降级，不删除或改写执行历史。

阶段 4.3 新任务可选择 prepare_billing，只接受完整块符合“月费 N 元，每次上门服务费 M 元”
的受控语法，金额最多两位小数；若所选块还含优惠、套餐、标题或其他条款则拒绝。
“三次”补齐次数后仍等待计费月数，两项补齐才 ready，不从“全年”无条件使用 12。
核对同一期间及所选规则适用后，再发送 `mode=execute`、`confirm_billing_rule=true`。
固定计算不调用模型，保存独立 billing_result 和三个带来源的步骤后才 completed。
结果仅是所选标准规则估算；没有自动验证其他块或文档中的政策，不支持通用单位换算。
旧单字段任务仍走原算术流程，不静默升级；完成后改变条件使用 new_task，不删除历史。
存在当前或历史标准计费数据时，0007 拒绝不兼容降级。完整请求与小实践见
[阶段 4.3](docs/phase4-standard-billing.md)和 [ADR 0021](docs/adr/0021-controlled-standard-billing.md)。

## 显式答复偏好：阶段 4.4 程序验收完成

知识库路径下新增 `GET/PUT /answer-preferences`，GET 返回 knowledge_base_id、version、
answer_detail；PUT 接收 expected_version 与 concise/detailed/null。null 清除当前值但保留
递增版本，避免陈旧更新重新写回。偏好一直保存到修改或清除，没有 TTL。

会话读取快照，最终有证据生成通过固定服务端风格句使用该版本；并发修改下轮生效。
MessageRead.answer_preference 记录进入偏好生成能力调用路径时的快照，不保证网络请求已发出、模型收到或实际符合风格。
无有效偏好或已清除时该字段整体为 null；清除后的值与墓碑版本通过偏好 GET 查询。
追问、空检索、停止、计算器和确定性计费不标记风格应用；单次 /answers、/agent-runs
暂不读取偏好。new_task 和新会话不清除设置，旧 request_id 重放保留旧响应与旧快照。
当前清除不擦除历史轮次记录，也没有聊天自动抽取、自由文本指令或身份权限隔离。
设计、显式请求和待用户实践见[阶段 4.4](docs/phase4-answer-preferences.md)与
[ADR 0022](docs/adr/0022-explicit-answer-preferences.md)。完整回归通过，本机已正常升级至 0008。
小实践优先用 TestClient、独立 retrieval_context 与 StyledGeneration 替身；在 /docs 直接发送
真实模型会话可能产生费用，真实请求需另行明确预算，不是本练习的默认要求。
0008 降级同事务先锁表再检查，存在任意偏好行（包括清除墓碑）就拒绝删表，避免并发误删。

## 工程设计取舍

我们先采用模块化 FastAPI 应用，已使用 PostgreSQL 和 pgvector 保存文本块及向量，逐步构建检索增强生成链路。已有可选重排与本地只读 MCP，近似索引、Redis 和 LangGraph 按明确需求决定是否引入。身份认证和权限控制实现之前，本项目仅是本地单用户原型。

## 自建只读 MCP 搜索：阶段 4.5 程序验收完成

在已有知识库 `/agent-runs` 请求中显式增加 `search_backend="mcp"`：

```json
{"question":"如何恢复服务？","search_backend":"mcp","strategy":"vector","max_steps":4}
```

省略或使用 `search_backend="local"` 保持旧路径。MCP 模式支持 vector/hybrid，
与 `rerank=true` 同用返回 422；首版不改变 `/search`、`/answers` 或持久会话入口。
工具注册仍只有获批的知识搜索和既有本地计算器；MCP Server 仅提供 knowledge_search。

安装后端依赖即可获得固定版本 `mcp==2.3.0`。平台用当前 Python 启动固定模块
`app.mcp_tools.server`，每次搜索创建短命 stdio 进程；工作空间、知识库和策略由 Host
绑定，模型只能提交 query，不能选择命令、连接地址或其他知识库。无需新增迁移，schema
仍为 0008。新增 SDK 及其依赖不代表本项目已实现身份认证。

每次先发现工具并严格核对获批输入/输出 schema，再执行一次低层调用，不做自动重发。
SDK 默认 auto 先探测 server/discover，必要时回退到 initialize 协商；这是连接能力
协商，不是 tools/call 自动重发，不能与不静默改用本地搜索的业务边界混为一谈。
Host 对返回结果复验严格 JSON、范围、ready 状态与非空向量，以及文档/块身份、名称、
正文和字符范围；一项不符即整批拒绝，不把远端自称的原文直接加入证据池。
应用层最多 100000 UTF-8 字节、5 个命中和总计 12000 正文字；该字节检查发生在 SDK
解码之后，不是解码前资源限制。默认连接/发现/调用预算 30 秒，配置
`EAAP_MCP_TOOL_TIMEOUT_SECONDS` 取正数且不超过 180；取消后的进程清理另有有界宽限，
不宣称整个请求严格在 30 秒内结束。失败保留固定工具错误，不静默改用本地搜索。

MCP 发现与 readOnlyHint 都不是执行授权；平台白名单和具体操作的权限/确认是两层
边界。当前没有删除工具或用户授权流程，子进程是持有必要配置的受控本地程序，不是
安全沙箱或远端多租户服务。完整设计、验证范围、小实践和待逐题面试见
[阶段 4.5](docs/phase4-mcp-search.md)与 [ADR 0023](docs/adr/0023-allowlisted-local-mcp-search.md)。

阶段 4.5 最终完整后端回归 **1501 项通过、无跳过（482.40 秒，0:08:02）**，使用独立
eaap_test PostgreSQL 与模拟/受控模型；新增 MCP 140 项（95 项协议、45 项 API）。
协议专项 **95 项通过、无跳过（13.02 秒）**是全量子集，不能相加。验证区分公开固定
fixture 的真实 stdio、生产固定 app.mcp_tools.server 模块 + 真实 stdio + 测试库空知识库
链路，以及 MCP Client 替身的 Host 非空原文反例；空知识库链路不请求真实向量或生成
模型，不能将这些测试称为真实非空模型端到端验收。初版协议 85 项通过、1 项失败，
组合专项 180 项通过、1 项失败：早退异常断言未适配 SDK 嵌套 ExceptionGroup，已改为
严格核对叶子 ConnectionClosed 后重跑通过，失败轨迹保留，不改写为初次全通过。
另外新增故障归类测试暴露 SDK 超时/嵌套异常组的生产处理问题，已修复 Client 递归
解包并仅保留一致已知类别；未知或类别混合统一 mcp_service_error，不只是调整测试断言。
无需迁移，schema 仍为 0008。学习者独立理由正确，代码实践与三道面试题仍未完成。
本轮检查后按模块提交本地，GitHub 推送单独授权，不预填提交编号。

## 固定问答检查点：阶段 4.6 程序验收完成

知识库路径下新增 `POST /answer-workflows`（只保存 pending）、
`GET /answer-workflows/{workflow_id}`（读取当前状态）及
`POST /answer-workflows/{workflow_id}/execute`（显式同步执行）。
创建携带新的 request_id、question 和 vector/hybrid 策略；执行携带新的 request_id、
当前 expected_version，默认 recover_interrupted=false。旧接口不自动转入该流程。

固定本地检索 top 5，创建时冻结生成字符预算与 12000 的较小值，保存选入预算的完整
原文检查点。生成超时后读取当前状态，有合法检查点时复验归属、ready/向量与完整原文，
只继续生成；生成前和最终落库前再次核对。来源变化或检查点损坏清除检查点，标记
不可直接重试，需要新建；空检查点合法弃答，跳过生成。首版不启用 MCP、重排或答复偏好。
检查点冻结选中资料而非整个知识库版本，新资料加入不会自动重新召回；要用最新资料需新建。
execute（含恢复及旧回执重放）仍需基本向量配置可构造，缺失可在领取前503且不产生
持久failed；create/GET不依赖该提供方。复用检查点不发向量HTTP不等于完全无需配置。

执行前短事务领取请求编号、递增版本和 900 秒租约，外部调用在事务外；写回时核对同一
执行编号、版本和有效租约，未过期 running 拒绝竞争，过期后仍需新编号、当前版本与
recover_interrupted=true 显式接管。没有续租、后台自动恢复或取消旧网络请求。
最多五次尝试；同执行编号与完整同 payload 重放旧不可变回执，不再次执行，换 payload
返回 409。旧失败回执不等于当前状态，应通过 GET 核对。平台写回一致性不保证服务商
已经处理但本机未落库的崩溃窗口只产生一次费用。
数据库提交确认可能丢失，固定503后先GET，不以旧版本另写失败或盲目重新执行。

0009 新增 answer_workflows、answer_workflow_attempts，不改写历史；降级同事务锁两表，
任何数据（包括 pending）阻止删表。本机应用库已正常0008→0009，七张旧表计数一致，
新两表各0行，compare_metadata=[]。第二轮专项140 项通过、无跳过（124.51 秒），覆盖
当时128 工作流+12 迁移；后补6用例未纳入该次，不能记最新134工作流已全部通过。
最新纯子专项56项通过、78项deselected（3.71秒），后补六项专项6通过/128deselected
（16.80秒），最终完整回归**1646项通过、无跳过（633.72秒，0:10:33）**，不与专项相加。
新增145项=134工作流（56纯/78DB）+11迁移（3纯/8DB）；1501为阶段4.5历史。
首次完整1645通过/1error/无跳过（1486.14秒），旧回答用例fixture连接及清理均超时，
未进入业务断言；原样单项复跑1通过（5.17秒），业务代码未因此改动，最终重新全量通过。
不推断环境根因，不将pytest耗时当接口性能；详情与早次专项失败轨迹保留在阶段文档。
独立测试库和模拟模型不代表真实任务质量或成本节省比例，学习者代码实践未完成。
设计、请求、测试边界与八部分教学见[阶段 4.6](docs/phase4-answer-workflows.md)及
[ADR 0024](docs/adr/0024-checkpointed-answer-workflows.md)。

## GitHub 版本管理

完成且验证的模块按学习者恢复的约定进行本地提交，提交前检查范围、安全和格式并报告结果；GitHub 推送必须另行得到确认，不与本地提交绑定。2026-10-12 昨日积累的三条提交已授权正常推送，本地与远程核对为 a167235；该次授权不代表新模块自动推送。首次上传与后续流程见 [GitHub 工作约定](docs/github-workflow.md)。
