# 文档导航

文档按用途分类；继续开发先读最新交接和真实代码，历史阶段中的“当前”“下一步”只描述当时状态。

## 继续开发

1. [当前交接](handoff/handoff-current.md)：当前位置、验证边界和剩余事项。
2. [最新进度总结](progress/progress-2026-10-17.md)：已完成功能、本人参与和后续安排。
3. [新对话提示词](handoff/new-chat-prompt.md)：可复制模板，填入本机路径后使用。
4. [路线图](progress/roadmap.md)与[学习记录](progress/learning-progress.md)：整体方向和历史参与记录。

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

## 按用途查找

- [交接与提示词](handoff/)：当前接续入口；旧阶段交接放入历史归档。
- [进度与学习](progress/)：路线图、学习记录和按日期保存的总结。
- [架构与接口设计](architecture/)：整体架构、数据模型和接口设计方案。
- [后端模块](backend/)：phase1 至 phase4 的设计、实现、教学和验收说明。
- [前端模块](frontend/)：网页登录、资源浏览、上传、切块、向量化和候选检索。
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
- 本轮只调整组织和路径，不以文件移动更新测试成绩、模型质量或本人学习进度。
- 文档移动时同时更新入口、相对链接及提示词中的路径；ADR、评测产物和截图保持原目录。
- 历史段落可以保留，但当前事实以交接顶部、最新记录及实际代码为准。
- 原有未提交文档删改随文件一起保留，不能因整理目录恢复或混入新提交。
