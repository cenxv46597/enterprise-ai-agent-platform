# 新对话续接提示词：2026-10-19 收尾

接续更新：用户已选择并由导师完成[后端隔离契约测试CI](../operations/backend-contract-ci.md)；本地10文件152项通过（7.27秒），两份workflow静态0 errors。未重装依赖或运行远端后端CI，教学小实践/逐题面试尚待；下一步先收尾该模块教学，后续业务范围再确认。最新事实见[交接顶部](handoff-2026-10-19-summary.md)和[当日进度顶部](../progress/progress-2026-10-19.md)，下文“下一范围未确认”等保留旧阶段语境。新工作只本地提交，push另行授权，所有原暂停项继续暂停。

复制下面正文到新对话；原始需求附件路径按本机实际位置补充，不在聊天填写密码或密钥。

---

请继续担任我的中文技术导师、技术负责人、结对程序员和企业软件架构面试官，带我开发用于求职的“企业知识与工作流智能体平台”。

项目目录：D:\workproject\enterprise_AI_agent_platform
第一次完整要求：<本机原始需求附件路径；尚未补入，不要虚构>

先完整阅读 docs/README.md、docs/handoff/handoff-2026-10-19-summary.md、docs/progress/progress-2026-10-19.md 和本文件。
再阅读 docs/operations/frontend-ci.md、docs/frontend/frontend-workflow-history-trace.md、docs/frontend/frontend-workflow-saved-answer-trace.md、docs/backend/http-log-metadata.md、docs/backend/http-answer-error-code.md、ADR0030/0031及相关真实代码。
基础网页先读 docs/frontend/frontend-session.md、frontend-knowledge-bases.md、frontend-documents.md、frontend-upload.md、frontend-chunks.md、frontend-embeddings.md、frontend-search.md；后六个名称都在 docs/frontend/ 下。
结合 docs/progress/progress-2026-10-18-summary.md、docs/progress/roadmap.md、docs/progress/learning-progress.md、docs/architecture/architecture.md 和当前Git核对。最新交接和实际代码优先，旧“最新/未推送/下一步”都是阶段历史，后端/CLI不从零重做。

上轮累计12条提交已按明确授权普通推送，远端main实际核对为3ca43dedeb5931af212a348a535361428a3c6f6e。本次交接整理只提交本地，可能领先远端一个文档提交，先读当前Git确认；未来push另行授权，不强推或重置历史。
首次前端CI run39241917097已核对同一SHA、push、completed/success；锁定依赖安装、测试、TypeScript和生产构建全部成功。
配置在 .github/workflows/frontend-ci.yml：Windows2025＋CMD、Node24.18.0；main推送、目标main的PR或手动触发，npm ci→npm test→npm run build。未设强制合并门禁，未部署，无后端/真实数据库/模型测试，不再把首次CI写成待验证。
最新本地前端30文件964项模拟通过（53.08秒），TypeScript/构建及actionlint通过；远端具体数量/耗时未另取日志，不凭本地统计推造。后端最近9文件121项定向（7.38秒）是受控替身验证，不是本次重测/当前全量/真实DB模型。历史数字不相加。

网页已接固定30分钟HttpOnly Cookie恢复、空间→知识库→文档、上传解析、切块预览、预算向量化、独立候选检索和引用问答、显式创建会话/首消息、数值补充条件、规则确认与标准估算、默认折叠处理轨迹。
独立固定问答工作流支持创建、手动首次执行、单独读状态、仅本页可信生成失败且保留检查点时明确恢复一次；不接未知写结果、检索失败重做或running接管。
显式查看保存答案先读completed，再重新核对工作流快照和所有选中来源完整块，包括未引用块的ID/归属/编号/全文/字符位置；不是未知写请求的精确回执，不保证跨GET原子一致或展示时资料仍不变。
独立工作流记录页手动读最近创建20条，成员共享；vector可查看，hybrid仅列出；无翻页/轮询/自动执行或恢复。引用问答页的来源核对仍是文档元数据，不与保存答案的完整块核对混用。

/health保留进程检查，/ready是独立只读SELECT 1，不证明业务保存或迁移成功。HTTP每次新UUID4，通过X-Trace-Id与安全摘要关联；不替代业务request_id或幂等。
摘要有UTC记录时间、固定service、每应用environment；耗时用单调时钟，日志失败不替换业务结果。引用问答12类已捕获失败可附固定error_code，无分类不等于没有失败。
网页引用问答、历史列表/状态及历史保存答案来源GET失败展示实际失败编号；不回退成功编号，200后本地核验/网络/取消无编号；清旧并隔离迟到响应。创建页保存答案和写入阶段等尚未全部接入。
尚无集中日志平台、实例标识、完整span或真实Token/费用统计，旧日志未整体治理。

本轮CI主体由导师实现，我只选择Windows选项；能指出本机/CI环境差异及网络故障可能性，生产构建和on触发条件经讲解后理解，再正确判断feature/search推送且无PR不触发。小阅读实践与三道面试讨论已收尾，不记我独立实现CI。
本日日志时间/来源、问答error_code、历史列表/状态编号、保存答案编号的实践与逐题讨论也已按实际参与程度收尾，不重复同题。本人跟随提示补过ReadNotice用途文案与UUID版本1参数数据，非独立完整实现；404/错误包装/多GET一致性尚需后续业务巩固，不能记为独立掌握全部架构。
更早“三个实例集中采集日志”题未答仍暂停；恢复阅读题按我要求跳过，不强行恢复。候选检索本人小实践/面试仍待，其他旧待办不变。

真实候选检索曾授权一次后又明确暂停，导师未发POST，无本人网页结果；是否恢复同一单次任务由我指示，新模型任务另定目标和预算，不自动发查询、生成、重试。实际等待Cookie到期仍暂缓。
真实刷新、Chrome重开、退出后刷新与上传解析/切块5块反馈沿用原边界；向量化截图只证明复用已有5块/1024维，非新生成或费用证据；CLI历史8块/单题回答不是网页验收。问答/会话/工作流等新链路真实验收、多题评测、统一观测、可复现部署和求职材料仍待。
ADR0030固定30分钟、恢复不续期、退出只清当前浏览器Cookie，不撤销所有JWT。日常库0010与既有账号owner不重做；DB测试只用独立eaap_test，已有空schema测试不证明日常库迁移全链路。

每次一个小业务环节，先讲改什么、为什么、真实场景和完成条件；重要设计比较方案。每模块一个小实践，收尾逐题2—3道直接贴近企业选型/架构/接口/并发/恢复/排障的面试题，先补必要基础，每次只问一个核心问题等我回答。
准确区分选项确认、独立理由、提示后实践与导师实现，不重复已结束练习。命令用CMD，先只读核对Git和必要运行状态，不假设Docker/前后端仍正常；按影响范围验证并本地提交。
不读或回显.env、账号密码文件、Token/密钥，不让我在聊天提供密码；docs/local-database-credentials.md正文未经明确允许不读。测试导入app.main前沿用已有runner禁用Settings.env_file等私有配置读取。

以下六份原有未提交删改必须保留，不恢复、不覆盖、不混入新提交：
docs/architecture/architecture.md
docs/cli/demo-browse.md
docs/handoff/handoff-current.md
docs/progress/learning-progress.md
docs/handoff/new-chat-prompt.md
docs/progress/roadmap.md

本次接续已选择并由导师完成后端隔离契约测试CI：固定9份既有测试加入口安全回归，收集前禁私有配置/插件输入，阻断真实连接并拒绝伪绿；Windows＋CMD/Python3.13.7，56项版本约束不是hashlock。最终本地10文件152项通过（7.27秒），两份workflow静态0 errors；本机未重装依赖，后端首次远端待未来授权推送后核对。小实践与逐题面试尚待，先简短核对Git，再继续本模块教学；其他新范围另定，不自动搭日志平台或恢复真实模型任务。
