# 文档导航

文档按用途分类；继续开发先读最新交接和真实代码，历史阶段中的“当前”“下一步”只描述当时状态。

## 继续开发

1. [最新交接（2026-10-19）](handoff/handoff-2026-10-19.md)：当前位置、验证边界和剩余事项。
2. [当日进度（2026-10-19）](progress/progress-2026-10-19.md)：日志基础、问答错误码、历史读取/保存答案编号的实现、验证与学习停点。
3. [上一日完整汇总（2026-10-18）](progress/progress-2026-10-18-summary.md)：六个模块、分组证据与后续安排；其中未推送状态已由最新交接更新。
4. [新对话提示词（2026-10-19）](handoff/new-chat-prompt-2026-10-19.md)：可复制模板，填入本机路径后使用。
5. [路线图](progress/roadmap.md)与[学习记录](progress/learning-progress.md)：整体方向和历史参与记录。

原 `handoff-current.md`、`new-chat-prompt.md` 及上述路线/学习文档保留原有未提交删改；
本次新增日期文件承接最新事实，不覆盖这些历史文件。2026-10-18 总结时跳过恢复阅读题，
不记为已作答；真实检索及实际等待登录到期仍暂缓。

2026-10-17 后续网页小步见[独立引用问答页](frontend/frontend-answers.md)，
该模块的实现、验证与教学状态单独记录；此前真实检索仍暂缓。
随后小步见[显式创建会话与首条消息](frontend/frontend-conversations.md)，
与引用问答分别记录参与程度和验证范围。
接续小步见[逐项补充条件](frontend/frontend-conversation-conditions.md)：
手动逐项保存，输入齐备后暂停，显式执行仍待后续。
随后见[规则适用确认与显式标准估算](frontend/frontend-conversation-billing.md)：
用户确认后手动执行，核对保存结果与来源后展示小计及合计。
再接[首条消息公开处理轨迹](frontend/frontend-conversation-trace.md)：
默认折叠查看已有步骤和统计，展开不发送请求、不重跑工具。
随后见[固定问答工作流首步](frontend/frontend-workflows.md)：
独立页创建、手动首次执行与读取元数据；失败恢复另分小步。
接续见[保留检查点的生成失败恢复](frontend/frontend-workflow-recovery.md)：
核对本页失败回执与当前状态，明确允许新调用后恢复一次，不接管执行中或结果未知的任务。
再接[显式查看已保存答案](frontend/frontend-workflow-saved-answer.md)：
先读completed状态，再单独GET保存结果并核对全部来源原文块，不重执行或补造未知请求回执。
随后见[独立工作流记录页](frontend/frontend-workflow-history.md)：
手动读取当前知识库最近20条创建记录，点选vector任务查看状态与保存答案；历史页只读，不执行或恢复。
接续[独立数据库集成验证](backend/workflow-history-db-validation.md)：
启动原数据库容器，在独立eaap_test随机schema验证真实SQL与持久化，不读取既有密码、不调用模型。
再接[应用数据库就绪检查](backend/database-readiness.md)：保留 `/health`，新增 `/ready`，
用独立只读连接区分进程响应和数据库连通性；不据此宣称迁移或完整业务已验收。
随后[HTTP请求排错编号](backend/http-request-trace.md)：响应头与安全访问摘要关联一次HTTP调用，
不替代会话/工作流的业务request_id，设计见[ADR0031](adr/0031-server-http-trace.md)。
再接[引用问答失败的排错编号](frontend/frontend-answer-trace.md)：仅展示实际失败响应的合法编号，
来源GET与生成POST各自归属，不沿用成功请求的编号，不补造或自动重试。
随后[HTTP日志时间与运行来源](backend/http-log-metadata.md)：UTC记录时间、固定服务与每应用环境，
耗时仍用单调时钟；不代表已有集中采集平台或同环境实例定位能力。
再接[引用问答业务错误码](backend/http-answer-error-code.md)：已知错误按请求标记固定分类，
与排错编号同条摘要输出；不复制异常原文，不把无分类当成没有失败。
随后[工作流记录页读取排错编号](frontend/frontend-workflow-history-trace.md)：列表与状态GET失败在所属提示下显示合法编号，
清理旧编号、隔离迟到响应；历史页仍只读，保存答案及写入阶段的编号展示另分小步。
再接[历史页保存答案核验编号](frontend/frontend-workflow-saved-answer-trace.md)：逐个保留工作流/文档/块失败GET的编号，
整份来源仍全部核验，不把成功响应编号用于本地核验失败，不接写入阶段或创建页展示。

## 按用途查找

- [交接与提示词](handoff/)：当前接续入口；旧阶段交接放入历史归档。
- [进度与学习](progress/)：路线图、学习记录和按日期保存的总结。
- [架构与接口设计](architecture/)：整体架构、数据模型和接口设计方案。
- [后端模块](backend/)：phase1 至 phase4 的设计、实现、教学和验收说明。
- [前端模块](frontend/)：网页登录、资料入库、检索、引用回答、会话、计费、轨迹及有限工作流恢复。
- [CLI 演示](cli/)：终端浏览、上传、切块、向量化、检索及引用回答。
- [运维与协作](operations/)：GitHub 工作约定和本机故障排查。
- [历史归档](archive/)：旧阶段交接与历史上传清单，保留原日期和结论。
- [架构决策 ADR](adr/)：按编号记录设计取舍；当前网页登录方案见 [ADR0030](adr/0030-persistent-web-cookie-session.md)。
- [评测记录](evaluation/)：实际报告及配套 JSON；报告数据保持原样。
- [截图](screenshots/)：文档引用的图片资源。

[本地数据库凭据管理说明](local-database-credentials.md)因自动审批限制暂留原位置，
本次未读取正文或迁移；它与实际私有配置文件的处理权限需另行明确。

## 阅读与维护约定

- 新文档放入对应用途目录，通过相对链接关联；不要再把模块说明堆到 docs 根目录。
- 目录整理只调整组织和路径；后续业务实现及学习状态以对应日期与模块记录为准。
- 文档移动时同时更新入口、相对链接及提示词中的路径；ADR、评测产物和截图保持原目录。
- 历史段落可以保留，但当前事实以交接顶部、最新记录及实际代码为准。
- 原有未提交文档删改随文件一起保留，不能因整理目录恢复或混入新提交。
