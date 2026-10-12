# 阶段 4.5：自建只读 MCP 知识搜索

更新日期：2026-10-12。实现已接入，程序验收完成；真实非空向量/模型端到端质量、学习者
小实践和三道逐题面试尚未完成。不把阶段 4.4 的 1361 项历史完整回归当本轮成绩。

## 1. 目标

自己实现本地只读 MCP Server，让平台通过官方 SDK Client 调用知识搜索，再复用
既有 Agent 循环、证据池和引用校验。先打通一个有边界的完整能力，不同时加入删除、
任意 SQL、远端服务、多租户授权或通用工作流引擎。

单次知识库 `/agent-runs` 可显式选择 MCP；已有本地路径继续作为默认值。会话状态、
持久偏好和确定性标准计费不因此自动改用 MCP。

## 2. 原因

原有知识搜索由 ToolRegistry 调用同一进程中的 Python 对象。若接入独立工具程序，
需要定义发现、参数、请求、响应与生命周期，而不是让模型自己执行程序。
MCP 统一通信接口，但不会替 Host 决定业务能力、身份权限或证据可信度。

问题场景：服务器升级后公布 delete_document，是否自动交给 Agent？学习者选择 B，
独立指出删除不能随便使用，需要用户批准。导师补充：平台能力白名单与具体操作的
身份/资源权限/用户确认是两层保护；先有能力准入，再讨论一次操作能否执行。
本模块没有删除工具或审批机制，不能把设计回答称为功能已实现。

## 3. 概念

- Host 是当前平台，决定工具范围、模型定义、知识库归属和结果能否进入证据池。
- Client 负责连接、发现与调用。本模块使用官方 Python SDK，不手写 JSON-RPC。
- Server 提供工具。本模块自建固定本地程序，只提供 knowledge_search。
- 工具发现只告诉 Client 服务器有什么，不等于平台已批准使用。readOnlyHint 是
  注解提示，不是授权或强制只读证明。
- stdio 用本地进程的标准输入/输出通信，stdout 专供协议；它不是隔离危险程序的沙箱。
- structuredContent 是线协议数据。格式合法、工具调用成功，不等于来源真实或答案正确。

官方 SDK 和概念参考：[Python SDK](https://github.com/modelcontextprotocol/python-sdk)、
[MCP 工具规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)、
[stdio 传输说明](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。
项目固定 mcp==2.3.0，官方后续版本与项目当前能力不能混为一谈。

## 4. 设计

### 工具准入：自动注册与显式白名单

方案 A 是发现即注册，新能力方便上线，但服务器更新就会扩大 Agent 可执行范围，
还容易把远端描述当成模型指令。方案 B 是发现后只适配后端批准的工具及固定契约，
新增能力需要明确开发和验收；当前采用 B。

每次搜索都刷新发现，要求完整、无分页、最多 32 个名称不重复的工具。只批准
knowledge_search，严格比对 inputSchema/outputSchema；其他工具既不进入注册表，也
不变成模型工具描述。缺失搜索、重复名称或契约变化明确失败，不尝试自动适应新 schema。
严格相等降低自动兼容性，换来当前一个工具可以清楚说明的行为；升级契约需要同步改两端。

### 传输：本地 stdio 与远端 HTTP

本地 stdio 复用现有 Python 环境，不必先部署服务或设计远端认证；代价是每次创建进程
的启动开销以及本机配置可被该受控程序读取。远端 HTTP 适合共享部署和多客户端，
但要补网络、TLS、认证、资源权限和服务生命周期。本机学习原型先选择固定 stdio。

短命进程与复用进程也有取舍：前者简单、不共享跨 event loop 的 Session，结束即清理；
后者节约启动成本，但要处理并发、连接失效与退出。当前不引入会话池，不宣称延迟更优。
命令固定为当前 Python 执行 app.mcp_tools.server，不能由模型或请求选择任意命令。
范围和 strategy 由 Host 启动参数绑定，模型仍只填写 query。

### 返回证据与故障

输入复用 SearchArguments。输出 SearchEnvelope 带固定 schema_version=1、工作空间、
知识库、strategy 与最多 5 个命中；每个命中包含真实文档/块身份、名称、序号、正文、
字符范围以及合法排名/有界距离，拒绝额外字段、重复块和非有限数。排名仅有下界，
没有声明最大排名，不能说排名具有数值上限。

Client 只接受完成且成功的结构化结果，以及和该结构化结果一致的单个 JSON 文本块，
不消费图片、附加链接、自由文本指令或补充输入要求。使用低层 session.call_tool 一次
调用，避免高层自动补充/重发；Agent 的后续有界不同查询是另一次显式动作，不是重试。
SDK 默认 auto 先发送 server/discover，必要时 fallback initialize 完成协商；这不是
tools/call 自动重发，也不是故障时改用本地知识搜索。

Host 在调用后重新检查当前数据库：范围一致、文档 ready、向量非空、完整原文字段
逐项相等。任何一个命中不符就整批拒绝，避免先保存可信部分再混入坏结果。
外部调用在 Host 数据库事务之外，回验采用短事务；子进程工作线程自己创建并关闭
数据库 Session，不共享 Host 的 Session。

最多 100000 UTF-8 字节、5 hit、合计 12000 正文字符是应用层预算，字节检查发生在
SDK 解码之后，不是恶意大包解码前防护或操作系统资源限制。Child 是受控本地程序，
持有必要数据库/向量配置，并非安全沙箱；不宜把此模式用于任意第三方脚本。

默认连接/发现/调用总预算 30 秒，EAAP_MCP_TOOL_TIMEOUT_SECONDS 为正数且不超过
180。SDK 的取消/进程清理有额外有界宽限，因此不能说完整请求必定在预算秒数内结束。
固定错误包括 mcp_timeout、mcp_discovery_invalid、mcp_result_invalid、
mcp_evidence_invalid、mcp_service_error；不泄露原始异常、连接配置或子进程 stderr。
技术故障不是成功弃答，也不静默 fallback 为本地搜索。

## 5. 实现

- backend/app/mcp_tools/contracts.py：严格输入/输出契约、预算与固定错误。
- backend/app/mcp_tools/server.py：官方 SDK Server，固定工具发现/分派，复用既有搜索。
- backend/app/mcp_tools/client.py：固定进程、发现白名单、单次低层调用与有界退出。
- backend/app/mcp_tools/tool.py：Host 整批原文回验与 ToolRegistry 适配器。
- backend/app/api/agent_runs.py：search_backend=local/mcp，缺省 local。
- backend/app/core/config.py：MCP 操作预算；backend/pyproject.toml 固定 mcp==2.3.0。

请求示例：

```json
{"question":"如何恢复服务？","search_backend":"mcp","strategy":"hybrid","max_steps":4}
```

在原知识库 `/agent-runs` 使用该字段。模型仍看到既有 knowledge_search 和本地
calculator 定义，不因选择 MCP 扩大能力。MCP + rerank=true 返回 422，应选择 local
或关闭 rerank。旧 `/search`、`/answers`、持久会话入口不增加这个开关。

安装正常后端依赖即可使用 SDK，不需要另行运行常驻 MCP 服务。本轮没有数据库迁移，
schema 保持 0008。SDK 及 PyJWT 等依赖不等于本项目已有身份认证或用户权限。

## 6. 验证

验证分层，不把不同层的替身组合泛称真实模型端到端：

1. 纯契约与替身检查严格输入/输出、发现筛选、字节/字符预算、不自动重发、默认兼容。
2. 官方 SDK 内存协议检查真实消息处理与非法工具拒绝，但不代表操作系统进程链路。
3. 真实 stdio 子进程用公开固定资料验证初始化、发现、调用、无响应超时、提前退出和
   进程结束；不读取本机模型配置，不调用真实模型或数据库。
4. 独立 PostgreSQL 测试 Host 原文/范围回验、ready/向量约束、调用期间数据库变化、
   整批拒绝和 API 故障；MCP Client 与模型为替身，不是完整生产 child 检索联调。
5. 生产固定 app.mcp_tools.server 模块 + 真实 stdio + 独立 PostgreSQL 空知识库链路，
   由 test_actual_production_stdio_and_database_empty_search_without_model_call 验证。
   空结果不请求真实向量或规划/生成模型，不能替代非空真实模型整链语义验收。
6. 完整后端回归检查既有会话、偏好、算术、RAG 与迁移没有被破坏。收集数量不是通过
   数量，专项是全量子集时不能相加。

新增测试为 backend/tests/test_mcp_protocol.py、test_mcp_api.py 和真实进程公开 fixture
mcp_stdio_fixture.py。最终协议专项 **95 项通过、无跳过（13.02 秒）**；最终完整后端
**1501 项通过、无跳过（482.40 秒，0:08:02）**，独立 eaap_test PostgreSQL 与模拟/
受控模型，新增 MCP 140 项（95 协议、45 API）。协议专项是全量子集，不能相加；
历史阶段 4.4 完整 1361 项仍保留，不改成本轮成绩。

早次协议 85 项通过、1 项失败（12.41 秒），组合专项 180 项通过、1 项失败：早退异常
断言未适配 SDK 用嵌套 ExceptionGroup 包装 ConnectionClosed，已改为严格检查全部
叶子为该固定错误后重跑通过。保留失败轨迹，不把修正断言说成初次全部通过。
之后新增九个 public_error_code 用例的定向纯协议运行为 **88 项通过、4 项失败、
3 项 deselected（3.80 秒）**；三项真实 stdio 仅本次定向排除，并非最终跳过。四项
失败暴露生产 Client 对 SDK 请求超时、嵌套异常组的归类问题。已修 public_error_code
递归展开叶子：全部超时叶子映射 mcp_timeout，同一已知安全故障码才保留，未知或
类别混合统一 mcp_service_error；没有输出原始异常或放宽为任意异常通过。随后 95 项
专项和 1501 项完整回归均通过。本轮既修测试的早退异常断言，也修生产故障映射，
不能只描述为测试调整，定向成绩与最终全量不相加。
真实非空向量+MCP child+真实规划/生成的任务成功率、忠实性和成本都未验收；本轮
真实模型调用为零。个人代码实践和逐题面试仍未完成，导师测试不计个人实践成绩。

## 7. 学习总结、小实践与面试

已独立解释：破坏性工具需要明确许可，不能因为服务器公布就交给 Agent。
后续学习者独立指出：A 公司查询得到 B 公司知识库片段应拒绝，已识别跨知识库来源
错误。尚待实践验证格式通过与来源拒绝并存、正文篡改和整批拒绝；能力白名单与一次
操作权限、进程预算与清理时间仍需逐题讨论。导师实现和测试不计为个人代码实践。

小实践优先纯协议测试，不依赖数据库或真实模型：打开 test_mcp_protocol.py 中
test_discovery_does_not_register_unapproved_delete_despite_readonly_hint，将模拟的未批准
工具名从 delete_document 改为 delete_all_documents，其他批准定义保持不变。亲自运行：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_mcp_protocol.py::test_discovery_does_not_register_unapproved_delete_despite_readonly_hint -q -p no:cacheprovider
```

预期 1 passed 表示陌生工具即使自称只读也不会自动注册，不代表已执行删除、已实现
授权审批或 MCP 改善了真实质量。不要修改生产白名单让测试通过；先说清断言证明什么。
该实践尚未由学习者修改或运行，预期不是实际成绩；只在实际反馈后更新个人记录。

来源核验的小实践（当前接续）：在 test_mcp_api.py 的
test_chunk_from_other_knowledge_base_cannot_be_laundered_into_host_scope 中，将最后
一行替换为下面三行，其余构造保持不变：

```python
result = envelope(context, identifiers)
validated = SearchEnvelope.model_validate_json(result.model_dump_json())
assert_rejected_evidence(context, validated)
```

前两行应通过格式校验，最后一行仍验证 Host 返回 mcp_evidence_invalid、证据池为空且
未调用答案生成。此测试使用独立 PostgreSQL 与 Client/模型替身，不是实际远端协议
或身份授权测试。使用 README 的独立测试库配置方式，只选择该用例运行；无测试库
配置出现 skip 不算通过。学习者尚未修改或运行，不预记成绩，也不需要改生产实现。

面试题准备三道，每次只讨论一道：

1. 为什么远端公布 delete_document 或标注 readOnlyHint 不能代替权限？白名单与用户
   对某一次操作的确认分别保护什么？
2. MCP 结果的 JSON schema 已通过，为什么还要查数据库核对来源？一个 UUID 格式
   正确但属于其他知识库的块，怎样避免进入答案证据池？
3. 当前为何选短命本地 stdio 而非远端服务或连接池？操作超时后为什么还要单独处理
   子进程退出？增加多用户后哪些边界必须重新设计？

## 8. 下一步

本轮程序验收完成，检查范围、安全与格式后按模块本地提交，实际结果另核对，
不预填提交号；推送 GitHub 必须单独授权。用户实践、面试和真实模型验收分别记录。

继续补更完整但受限的工作流、身份与资源权限、PDF/资源列表、部署与前端，以及已保留
的真实 RAG/Agent 质量验收。若需要 MCP 接入会话、候选重排、远端服务或连接池，必须
明确新需求与边界，再扩展契约；不因只读 MCP 一条链路完成就宣布企业平台交付完成。
