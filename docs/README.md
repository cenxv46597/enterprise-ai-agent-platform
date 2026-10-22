# 文档导航

2026-10-22最新小步：[状态筛选真实浏览器验收](frontend/frontend-workflow-history-status-browser-validation.md)完成。扩展既有临时工具与257项安全检查，真实页面核对失败20＋5、完成10/运行5/待执行5、无匹配、条件保留/清旧和503重读；85条快照未变、无模型调用，覆盖/清理成功、服务停止。实现与验收均导师完成，本轮实践/两题待先讲解后开展，旧模块不重复；仅本地，未来push另授权。见[10-22顶部](progress/progress-2026-10-22.md)，下文未验收等保留旧阶段。

2026-10-22最新教学收尾：[历史状态筛选](frontend/frontend-workflow-history-status-filter.md)一个提示后阅读实践与两道讨论已完成。本人对切换回首页经例子讲解后理解；第1题指出成本，完整性及条件区别由导师补充；第2题讲解后准确复述status可选/省略保留原行为。主体实现和执行仍导师完成，不记独立完整设计。实现dfb144f与教学仅本地，下一范围未确认，未来push另授权；详见[10-22进度顶部](progress/progress-2026-10-22.md)，下文待教学保留原阶段。

2026-10-22最新小步：[工作流历史按状态筛选](frontend/frontend-workflow-history-status-filter.md)已按本人确认实现：后端先筛选再分页，前端切换回首页/清旧/手动读取，翻页与重读保留条件。契约242、入口安全132、真实SQL固定18、前端30文件1117及最终类型/构建通过；本轮无真实网页筛选或模型验收。本人实践/两题待先讲解后开展，旧模块不重复。新内容仅本地，未来push另授权；详见[10-22进度](progress/progress-2026-10-22.md)。

2026-10-21最新教学收尾：[真实浏览器只读验收](frontend/frontend-workflow-history-browser-validation.md)一个提示后阅读实践与两道讨论完成。本人经讲解明确本页重读，能说明整体连接验证价值并复述独立库/虚构记录/只读/禁模型做法；自动化测试范围由导师纠正，主体实现及验收仍为导师完成。实现4d6f06f及学习记录仅本地，下一范围未确认，不重复已结束实践/题；详见[当日进度顶部](progress/progress-2026-10-21.md)。下文待教学保留原阶段。

2026-10-21最新小步：[历史翻页真实浏览器只读验收](frontend/frontend-workflow-history-browser-validation.md)完成。新增可复现临时入口；真实页面通过40条空尾页、45条尾5条及一次503后手动恢复，85条任务未改变、无模型调用、身份/区域清理成功；安全入口161项通过。仅本地未推送，本轮实践/讨论待先讲解后开展；上一分页模块已收尾不重复。详见[当日进度顶部](progress/progress-2026-10-21.md)。

2026-10-21最新学习收尾：[工作流历史手动翻页](frontend/frontend-workflow-history-pagination.md)一个小阅读实践与两道讨论完成：本人能推算第三页offset40，说明按需读取价值并指出动态列表不能保证跨页不重不漏；速度泛化经导师纠正，具体位置变化机制先由导师讲解。主体实现0299b91及教学仅本地、未推送，下一开发/验收范围另定；详见[当日进度顶部](progress/progress-2026-10-21.md)。下文实践尚待保留原阶段。

2026-10-21当前环节：[工作流历史手动翻页](frontend/frontend-workflow-history-pagination.md)已按本人选择实现，每页20、前后页/本页重读/回最新，清旧详情和编号并保留失败目标页；本地前端30文件1054项/25.76秒及类型/构建通过。本人小阅读实践与面试尚待先讲解后开展，新版本尚未推送。

本轮获授权的三条升级测试/实践/教学提交已上传到e41be96，实际远端一致；同SHA三套CI全部成功，数据库[39591550657](https://github.com/cenxv46597/enterprise-ai-agent-platform/actions/runs/39591550657)实际入口132项、SQL18项及清理成功。详细范围与教学停点见[当日进度顶部](progress/progress-2026-10-21.md)，下文待推送等保留旧阶段，新开发未来push仍另行授权。

2026-10-21最新收尾：[固定升级回归](backend/auth-upgrade-ci-validation.md)的一个提示后实践与两道讨论已按实际参与程度完成：本人亲自运行18项通过，能说明升级比较流程及小样本覆盖边界，主体设计实现由导师完成。当前只本地，下一步可另行授权上传并核对最新18项云端结果；详细学习停点见[当日进度顶部](progress/progress-2026-10-21.md)，旧待办保留对应阶段。

2026-10-21继续开发：[固定0009→0010升级回归](backend/auth-upgrade-ci-validation.md)已实现，数据库固定清单17→18，验证旧资料保留及成员不自动赋权；最新本地结果/教学停点见[当日进度顶部](progress/progress-2026-10-21.md)。此新版本只本地验证，未来推送另行授权，下面首次远端17项成功保留原范围。

2026-10-21远端验收补记：[首次Ubuntu数据库CI39565175267](https://github.com/cenxv46597/enterprise-ai-agent-platform/actions/runs/39565175267)在89828d9成功，实际入口119项、真实SQL17项及清理均通过，同SHA前端/后端契约CI也成功。详见[最新进度](progress/progress-2026-10-21.md)顶部；下面旧待推送/待运行状态保留历史。本人实践与两道讨论已按提示后修改、导师讲解后理解收尾，今后执行后先讲解再提问。

2026-10-21最新接续见[当日进度](progress/progress-2026-10-21.md)：已按授权推送到`612650c`，同提交前后端CI均成功；现新增[独立PostgreSQL集成CI](operations/backend-postgres-ci.md)，固定17项真实SQL，首次新远端运行待未来授权推送。下文旧推送状态保留历史，实际Git与最新记录优先。创建页编号教学已开始，参与程度按当日记录，不记独立掌握。

当前接续以 [2026-10-19收尾交接](handoff/handoff-2026-10-19-summary.md) 和 [新对话提示词](handoff/new-chat-prompt-2026-10-19-summary.md) 为准。
最新接续见[2026-10-20进度](progress/progress-2026-10-20.md)：[后端隔离契约测试 CI](operations/backend-contract-ci.md)实现及教学已按实际程度收尾；[创建页两类只读GET排错编号](frontend/frontend-workflow-read-trace.md)已实现，本地前端30文件1013项模拟及类型/构建通过，教学与本人实践另待，不据此宣称真实网页验收。
前步：[Windows＋CMD 前端 CI](operations/frontend-ci.md) 已完成教学及首次远端运行；累计12条提交已推送，远端3ca43de，CI运行39241917097成功。后续交接及新工作只本地提交，未来推送另行授权。

文档按用途分类；继续开发先读最新交接和真实代码，历史阶段中的“当前”“下一步”只描述当时状态。

## 继续开发

1. [最新收尾交接（2026-10-19）](handoff/handoff-2026-10-19-summary.md)：全局当前位置、验证边界、学习停点和后续事项；[逐步交接](handoff/handoff-2026-10-19.md)保留历史过程。
2. [当日进度（2026-10-19）](progress/progress-2026-10-19.md)：日志基础、问答错误码、历史读取/保存答案编号的实现、验证与学习停点。
3. [上一日完整汇总（2026-10-18）](progress/progress-2026-10-18-summary.md)：六个模块、分组证据与后续安排；其中未推送状态已由最新交接更新。
4. [新对话提示词（2026-10-19收尾）](handoff/new-chat-prompt-2026-10-19-summary.md)：可直接复制，本机项目路径已填；原需求附件路径需按实际位置补充。
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
