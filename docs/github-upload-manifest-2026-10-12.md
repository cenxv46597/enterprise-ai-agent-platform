# 2026-10-12 昨日累计开发本地提交整理清单

状态：学习者已授权先提交到本地 Git；本轮不推送 GitHub。实际完成结果见文末记录与 Git 日志，不能把本地提交称为已经上传。

## 目标与范围

- 仓库：https://github.com/cenxv46597/enterprise-ai-agent-platform
- 后续推送候选目标：origin/main，须另行确认；本轮不执行 push，不强制推送或改写既有历史。
- 准备时本地与远程 main：9e6e2b60fedb25ce7f1579ff83f96179f36b726d。
- 原有候选 127 个文件：18 个修改、109 个新增。
- 加本清单后共 128 个候选文件：18 个修改、110 个新增。
- 覆盖昨日阶段 1.8–4.3：离线审核与辅助评审、生成快照与提示词比较、混合检索及重排、有界 Agent 与工具、持久会话、来源绑定计算和受控标准计费。
- 今天补充面试回答、提交规则与整理记录，泛化公开文档路径；提交检查仅清理一个测试文件末尾多余空行，不新增业务功能。

清单含中文学习记录、本科求职背景、设计选择与未完成实践状态；不是只有业务代码。
本轮授权将下列既有范围提交到本地，不等于允许立即公开到远程。后续推送前仍检查公开范围。

## 安全与真实性检查

- .env、上传资料、虚拟环境、pytest 临时目录与缓存不在候选中，也未被 Git 跟踪。
- .env.example 的敏感字段为空；数据库连接模板引用环境变量，没有写入真实口令。
- 秘钥形状、Token、私钥、带凭据地址及凭据赋值扫描的候选命中，核对为环境变量引用与受控测试占位值，未发现真实凭据。扫描不保证穷尽所有秘密。
- 没有输出、复制或写入真实 .env/密钥/Token/数据库口令。准备扫描未读取真实 .env；首次快照验证因目录权限误加载工作区入口，该入口可能按现有机制加载本机配置，导入断言失败，不能记为通过。随后验证确认入口来自不含 .env 的暂存快照；本轮未连接数据库或调用真实模型。
- 公开交接的本机用户名、私有附件路径及项目绝对路径已泛化，原始私有附件不修改、不上传。
- 既有历史曾出现并已轮换的旧数据库默认口令仍保留在历史；此前已记录旧值失效，不能宣称历史已清除。本轮不改写历史或重新轮换。
- 评测 JSON 可正常解析，内容为公开虚构练习语料或此前公开演示的结果；受控快照与模拟实验保留明确标识。
- 昨日新增一次真实单题模型辅助评审，三项 AI 建议通过、4982 token，不是人工质量分。真实六题提示词比较、人工语义质量和真实 Agent 成功率仍未验收。
- 昨日最终完整回归：1273 项通过、无跳过（523.88 秒），独立 PostgreSQL/pgvector 与受控模型；本轮不把历史结果写成今天重新执行。
- 本机应用库已正常升级到 0007，既有六表记录数不变；此处是昨日迁移记录，不代表在应用库跑测试。
- 准备时 git diff --check 通过，但该命令不检查未跟踪文件；后续 git diff --cached --check 发现并清理新增计费测试末尾多余空行。各组提交前检查暂存内容；远程在上传准备时无新提交，后续推送前仍重新核对。

## 本地提交分组与信息

```text
实现：完善问答评测与检索增强管线
实现：增加持久会话与受控智能体计费工作流
文档：同步项目进度并分离本地提交与远程推送
```

第一组 65 个文件：问答 API、检索与评测实现、共享 JSON HTTP 层、配置示例、相关脚本和测试、ADR 0010–0015、阶段 1/2 文档、公开评测报告与评测示例。

第二组 56 个文件：Agent/工具、会话、计费实现和 API、原生工具与规划 Provider、main、数据库模型、0005–0007 迁移、对应测试、ADR 0016–0021 与阶段 3/4 文档。

第三组 7 个文件：README.md、docs/architecture.md、docs/roadmap.md、docs/learning-progress.md、docs/github-workflow.md、docs/handoff-agent-tools.md 与本清单。

分组依据是真实代码依赖：当前 Agent 契约与会话、计费契约互相联动，不凭事后记忆重新拼造每个旧阶段版本。每组按明确文件清单暂存；两组实现导出暂存快照核对导入及测试，总体文档核对记录与格式，保留既有历史和实际提交时间。

## 原始候选文件总清单

M 表示准备时修改现有文件，A 表示准备时新增文件；这是原始 128 文件范围，不是提交后的剩余状态。仅按明确分组提交，不使用无差别 git add -A。

```text
M	.env.example
A	backend/alembic/versions/20261011_0005_conversations.py
A	backend/alembic/versions/20261011_0006_conversation_execution.py
A	backend/alembic/versions/20261011_0007_standard_billing.py
A	backend/app/agents/__init__.py
A	backend/app/agents/arithmetic.py
A	backend/app/agents/calculation_answers.py
A	backend/app/agents/calculation_contracts.py
A	backend/app/agents/calculator.py
A	backend/app/agents/clarification.py
A	backend/app/agents/contracts.py
A	backend/app/agents/engine.py
A	backend/app/agents/sourced_calculator.py
A	backend/app/agents/sourced_contracts.py
A	backend/app/agents/tools.py
A	backend/app/api/agent_runs.py
M	backend/app/api/answers.py
A	backend/app/api/conversations.py
A	backend/app/api/reranking.py
M	backend/app/api/search.py
A	backend/app/billing/__init__.py
A	backend/app/billing/contracts.py
A	backend/app/billing/preparation.py
A	backend/app/billing/rules.py
A	backend/app/billing/workflow.py
A	backend/app/conversations/__init__.py
A	backend/app/conversations/contracts.py
A	backend/app/conversations/counts.py
A	backend/app/conversations/service.py
M	backend/app/core/config.py
M	backend/app/db/models.py
A	backend/app/evaluation/answers.py
A	backend/app/evaluation/prompt_comparison.py
M	backend/app/knowledge/answers.py
A	backend/app/knowledge/hybrid.py
M	backend/app/knowledge/retrieval.py
A	backend/app/knowledge/search_pipeline.py
M	backend/app/main.py
A	backend/app/providers/compatible_chat.py
M	backend/app/providers/compatible_generation.py
A	backend/app/providers/compatible_planner.py
A	backend/app/providers/compatible_reranking.py
A	backend/app/providers/compatible_review.py
A	backend/app/providers/compatible_tools.py
A	backend/app/providers/generation_snapshot.py
A	backend/app/providers/native_planner.py
A	backend/app/providers/reranking.py
A	backend/app/providers/review.py
A	backend/scripts/compare_prompts.py
A	backend/scripts/evaluate_answers.py
M	backend/scripts/live_rag_smoke.py
A	backend/scripts/review_answers.py
A	backend/tests/test_agent_api.py
A	backend/tests/test_agent_engine.py
A	backend/tests/test_answer_evaluation_cli.py
A	backend/tests/test_answer_evaluation.py
M	backend/tests/test_answers.py
A	backend/tests/test_billing_conversation.py
A	backend/tests/test_billing_workflow.py
A	backend/tests/test_calculator_agent.py
A	backend/tests/test_calculator.py
A	backend/tests/test_chat_request_replay.py
A	backend/tests/test_clarification_agent.py
A	backend/tests/test_conversation_api.py
A	backend/tests/test_conversation_counts.py
A	backend/tests/test_conversation_execution.py
A	backend/tests/test_conversation_migration.py
M	backend/tests/test_generation_http_provider.py
A	backend/tests/test_generation_snapshot.py
A	backend/tests/test_hybrid_retrieval.py
A	backend/tests/test_hybrid.py
M	backend/tests/test_live_rag_smoke.py
A	backend/tests/test_model_review_cli.py
A	backend/tests/test_native_planner.py
A	backend/tests/test_planner_provider.py
A	backend/tests/test_prompt_comparison_cli.py
A	backend/tests/test_prompt_comparison.py
A	backend/tests/test_prompt_regression_cli.py
A	backend/tests/test_reranking_pipeline.py
A	backend/tests/test_reranking_provider.py
A	backend/tests/test_review_http_provider.py
A	backend/tests/test_sourced_calculator.py
A	docs/adr/0010-offline-answer-review.md
A	docs/adr/0011-offline-model-review.md
A	docs/adr/0012-generation-request-snapshots.md
A	docs/adr/0013-fixed-evidence-prompt-comparison.md
A	docs/adr/0014-bounded-hybrid-retrieval.md
A	docs/adr/0015-optional-candidate-reranking.md
A	docs/adr/0016-bounded-knowledge-agent.md
A	docs/adr/0017-native-tool-calling.md
A	docs/adr/0018-evidence-bound-calculator.md
A	docs/adr/0019-persistent-conversation-clarification.md
A	docs/adr/0020-source-referenced-chained-calculations.md
A	docs/adr/0021-controlled-standard-billing.md
M	docs/architecture.md
A	docs/evaluation/answer-review-2026-10-11.json
A	docs/evaluation/answer-review-2026-10-11.md
A	docs/evaluation/hybrid-ranking-replay-2026-10-11.json
A	docs/evaluation/model-review-2026-10-11.md
A	docs/evaluation/model-review-first-case-2026-10-11.json
A	docs/evaluation/model-review-first-case-2026-10-11.md
A	docs/evaluation/prompt-comparison-mock-2026-10-11.json
A	docs/evaluation/prompt-comparison-mock-2026-10-11.md
A	docs/evaluation/prompt-comparison-mock-timeout-2026-10-11.json
A	docs/evaluation/prompt-comparison-six-mock-2026-10-11.json
A	docs/evaluation/prompt-comparison-six-mock-2026-10-11.md
M	docs/github-workflow.md
A	docs/handoff-agent-tools.md
M	docs/learning-progress.md
A	docs/phase1-answer-evaluation.md
A	docs/phase1-generation-snapshots.md
A	docs/phase1-model-review.md
A	docs/phase1-prompt-comparison.md
A	docs/phase2-candidate-reranking.md
A	docs/phase2-hybrid-retrieval.md
A	docs/phase3-calculator.md
A	docs/phase3-knowledge-agent.md
A	docs/phase3-native-tools.md
A	docs/phase4-conversation-state.md
A	docs/phase4-sourced-calculations.md
A	docs/phase4-standard-billing.md
M	docs/roadmap.md
A	examples/evaluation/answer-review-rubric.json
A	examples/evaluation/generation-request-controlled.json
A	examples/evaluation/generation-requests-controlled-six.json
A	examples/evaluation/prompt-regression-checklist.json
M	README.md
A	docs/github-upload-manifest-2026-10-12.md
```

## 内容指纹与执行保护

上传准备时原有 127 个候选（不含本清单自身）的内容指纹：

```text
b453aba6359a708f562321f4bf57d773cd32c0ed1d978e571c7740505262747e
```

指纹算法：候选按 PowerShell Sort-Object path 排序，对每个文件计算 SHA-256 小写十六进制；
拼接“状态 + TAB + 相对路径 + TAB + 文件哈希 + LF”，再对 UTF-8 整体计算 SHA-256。
该指纹是准备时的历史快照，不代表后续格式清理和 Git 规则更新后的最终内容，也不是安全证明；不包含忽略的本机秘密。

格式清理和总体文档同步后，同样 127 个原始候选（仍排除本清单自身）的内容指纹为：

```text
6b36845b632de5226a956a1dde1100e94f91643c85154e26d1ee51f946351c95
```

该指纹使用上述同一算法和准备时的 M/A 标记；各次提交的最终内容另由 Git 提交树保存。

本轮按三组依次暂存，核对文件范围、暂存树和格式检查，验证后执行本地提交；不推送、不覆盖工作区或改写历史。上传准备后的变更仅为已说明的格式清理与本地提交文档更新。
后续推送前重新检查远程 main 和公开范围；认证问题由学习者在本机处理，不在聊天收集令牌。

## 已核实的本地提交与验证

- 第一组：110390a66e7466c9957f3bf8be0439d189618705，65 个文件。
  独立快照确认导入路径并收集 818 项测试；选跑 388 项纯程序/模拟协议测试，全部通过
  （8.53 秒），不是数据库全量或真实模型质量验收。
- 第二组：18407e7c123ff69f5b7ab199f1ab946e6edb42d5，56 个文件。
  独立快照确认导入路径并收集 1273 项测试；所选专项 338 项通过、2 项跳过（3.09 秒）。
  复查确认跳过的是 test_calculator_agent.py 中两项需要独立测试库的实际 API 用例；
  本轮未设置该地址，未运行数据库全量，不能称为无跳过的完整验收。
- 第三组为本清单及其他 6 份总体文档，提交信息为“文档：同步项目进度并分离本地提交与远程推送”。
  本清单随该文档提交保存，最终提交 ID 与工作区状态在操作后读取 Git 确认，不在文件中自指提交哈希。
- 昨日 1273 项通过、无跳过（523.88 秒）的完整回归保持原历史记录。本轮不重写该指标，
  未新增业务功能、真实模型请求、数据库迁移或远程推送。
