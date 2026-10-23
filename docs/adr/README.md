# 架构决策索引

决策按实现阶段保留；旧ADR中的身份缺失、网页未接入等描述只代表当时状态。当前产品行为先看
[架构说明](../product/architecture.md)与实际代码。

## 数据与知识问答

- [原文件存储与对账](0001-document-storage-audit.md)
- [确定性切块](0002-deterministic-text-chunks.md)、[块持久化复用](0003-idempotent-chunk-persistence.md)
- [向量生成与存储](0004-embedding-generation-and-storage.md)、[范围内检索](0005-scoped-vector-retrieval.md)
- [证据回答与弃答](0006-evidence-grounded-answers.md)
- [混合检索](0014-bounded-hybrid-retrieval.md)、[可选重排](0015-optional-candidate-reranking.md)
- [文字层PDF](0025-text-layer-pdf-ingestion.md)、[文件名查找分页](0032-document-filename-list-pagination.md)

## Agent与执行状态

- [有界知识Agent](0016-bounded-knowledge-agent.md)、[原生工具调用](0017-native-tool-calling.md)
- [受限计算](0018-evidence-bound-calculator.md)、[来源绑定计算](0020-source-referenced-chained-calculations.md)
- [持久会话与条件补充](0019-persistent-conversation-clarification.md)、[标准估算](0021-controlled-standard-billing.md)
- [显式答复偏好](0022-explicit-answer-preferences.md)、[白名单MCP](0023-allowlisted-local-mcp-search.md)
- [检查点工作流](0024-checkpointed-answer-workflows.md)

## 身份与观测

- [用户和空间成员](0026-user-workspace-access.md)、[文档状态选择](0027-scoped-document-status-selection.md)
- [Agent运行统计](0028-agent-run-metrics.md)、[Cookie会话](0030-persistent-web-cookie-session.md)
- [HTTP排错编号](0031-server-http-trace.md)

其他编号保留专项评测和历史过程，按文件标题选读；凭据说明不属于自动阅读或问题反馈范围。
