# 文档导航

当前先读[项目整体指南](delivery/project-guide.md)、[运行和演示手册](delivery/run-and-demo.md)、
[交付记录](delivery/release-2026-10-23.md)。它们描述完成线和当前边界；每日记录用于追溯。

面试学习单独开对话时，使用[可复制提示词](handoff/interview-learning-prompt-2026-10-23.md)与
[面试专用交接](handoff/interview-learning-handoff-2026-10-23.md)，先建立整体感，再逐题讨论。

## 核心设计

- [架构和API数据设计](architecture/data-api-design-proposal.md)
- [文档上传与存储](adr/0001-document-storage-audit.md)
- [范围内向量检索](adr/0005-scoped-vector-retrieval.md)
- [证据回答与引用核验](adr/0006-evidence-grounded-answers.md)
- [混合检索](adr/0014-bounded-hybrid-retrieval.md)
- [身份与空间成员](adr/0026-user-workspace-access.md)

## 网页主线

- [上传](frontend/frontend-upload.md)、[切块](frontend/frontend-chunks.md)、[向量化](frontend/frontend-embeddings.md)
- [文档查找翻页](frontend/frontend-document-filename-pagination.md)、[状态筛选](frontend/frontend-document-status-filter.md)
- [向量/混合检索](frontend/frontend-hybrid-search.md)、[引用问答](frontend/frontend-answers.md)
- [问答候选上限](frontend/frontend-answer-candidate-limit.md)、[完整来源](frontend/frontend-answer-source.md)、[TXT导出](frontend/frontend-answer-export.md)
- [受控会话](frontend/frontend-conversations.md)、[条件补充](frontend/frontend-conversation-conditions.md)、[标准估算](frontend/frontend-conversation-billing.md)
- [固定工作流](frontend/frontend-workflows.md)、[恢复](frontend/frontend-workflow-recovery.md)、[只读历史](frontend/frontend-workflow-history.md)

## 验证与历史

- [首次六题真实RAG](evaluation/live-rag-smoke-2026-10-10.md)、[29块带干扰检索](evaluation/retrieval-eval-2026-10-10.md)：历史小规模证据，不能当当前网页验收。
- [前端CI](operations/frontend-ci.md)、[固定契约CI](operations/backend-contract-ci.md)、[独立PostgreSQL CI](operations/backend-postgres-ci.md)
- [10-23开发与学习进度](progress/progress-2026-10-23.md)、[日期交接](handoff/handoff-2026-10-22-summary.md)、[日期续接提示](handoff/new-chat-prompt-2026-10-22-summary.md)
- [旧导航原文](history/docs-navigation-before-consolidation-2026-10-23.txt)：保留所有旧模块线索；当前入口与代码优先。

六份受保护未提交文档保持原样，不恢复、不覆盖、不混入提交。凭据说明和私有配置不属于自动阅读范围。
