# 阶段 1.11：固定证据的两版提示词实验

## 1. 当前目标

读取公开生成快照，用相同问题、资料、顺序、请求模型和显式参数分别执行两版系统提示词，保留可核对的输出。实验工具和单题模拟已有实现；本次按学习者选择收拢为六题回归验收，继续使用已有十六项规则。真实生成快照和六题真实提示词效果比较仍未完成。

## 2. 为什么需要

“新版看起来更好”需要有可复现的输入和逐题证据。修复一题时，新增指令也可能改变其他题目的答案，因此本轮覆盖固定六题，核对事实、必要条件、操作顺序和弃答。这个模块对应 AI 应用岗位中的实验设计、提示词工程、模型接口复用和评测故障处理能力。

## 3. 核心概念

- 一道题的两版输出构成一组配对，输入相同才便于归因。
- 只改变系统提示词；用户消息内的问题、资料和编号保持原始字符串。
- 执行成功、引用有效、语义正确分别记录。
- 一版超时表示缺少这版输出，不能用剩下的一版判断胜负。
- 一次两版实验无法排除模型波动，模型别名相同也不保证远端版本完全一致。

## 4. 设计与取舍

重新检索适合完整链路验收，但证据变化会影响提示词比较。重放快照适合控制变量，当前选择它，无需数据库或重新生成向量。使用已有技术栈，不新增服务。理由见 [ADR 0013](../adr/0013-fixed-evidence-prompt-comparison.md)。

基线使用快照中的提示词；候选在末尾增加事实、必要条件和操作顺序的自检要求。线上默认提示词保持现有版本，实验尚未提供替换依据。

六题清单复用已有审核规则，绑定固定题集、公开原文和规则摘要。验收需要六组完整输出，并逐项核对必要条件与事实；易读性改善不能抵消语义回退。超时、缺失快照或待审核保持未完成，不能默认通过。清单用于少量离线样例，不要求人工逐句审核全部企业文档。

## 5. 实现与数据流

固定语料及题集 → 快照与调用预算核对 → 建立两版请求 → 保存待执行状态 → 共用请求层重放 → 草稿契约及指定来源引文核对 → 分别记录执行结果 → 汇总完整配对数。

关键入口：

- `prepare_case` 重算输入摘要，检查资料来自固定公开原文。
- `build_trials` 复制原请求，只替换系统提示词。
- `complete_request` 保留原用户消息字符串，严格校验正文与客户端模型、输出预算。
- `validate_draft` 复用生产草稿模型，检查来源编号、逐字引文和重复引用。
- `run_comparison` 在每次尝试前后保存，失败继续、没有自动重试。
- `--all-cases` 选择固定题集的全部六题，与逐个 `--case-id` 选择互斥；十二次生成尝试须显式给出预算。
- 计划模式列出 `missing_case_ids` 和 `ready_for_generation`，缺失快照时仍能查看缺口；执行模式在调用模型前拒绝不完整输入。

快照没有数据库块身份，报告保留来源编号，未伪造旧审核模块要求的块 ID 或字符位置。语义审核为待审核，不自动选择赢家。用量缺失或格式错误时保存未知，不拿估算值填充。

收到响应但引文无效时，仍保留返回模型及已知用量，记录输出验证失败；有效配对不会因此增加。“收到响应”和“取得有效草稿”分别统计。

## 6. 如何运行和验证

默认来源是明确标注的[受控练习输入](../../examples/evaluation/generation-request-controlled.json)，由公开文档构造，不是历史真实请求。

另有[受控六题输入](../../examples/evaluation/generation-requests-controlled-six.json)与[回归清单](../../examples/evaluation/prompt-regression-checklist.json)。六题输入使用公开全文按 500 字符、50 字符重叠切出的五个文本块，每道题都提供这些资料；它用于练习批次流程，不是真实检索结果或新的历史请求快照。

统一专项 **222 项通过（2.37 秒）**，覆盖新实验模块、命令行、正文重放及原生成和评审 HTTP 契约。检查两个模拟报告时，用户消息及其余固定请求相同、系统提示词摘要不同，真实模型调用均为 0。当前 137 个待版本管理文件未发现本机现用 API 密钥或数据库口令的字面值，`.env` 未被跟踪。

随后完整后端回归 **728 项通过、无跳过（157.58 秒）**，使用独立真实 PostgreSQL/pgvector 和模拟模型，验证共享请求层调整后原问答及数据库功能继续满足现有测试。

在仓库根目录规划一题两版，零网络调用：

```powershell
.\.venv\Scripts\python.exe backend/scripts/compare_prompts.py --plan
```

检查默认单题输入能否覆盖全题集；计划会展示缺失的五题，不产生请求：

```powershell
.\.venv\Scripts\python.exe backend/scripts/compare_prompts.py --plan --all-cases --max-calls 12
```

使用受控六题输入查看完整计划：

```powershell
.\.venv\Scripts\python.exe backend/scripts/compare_prompts.py --plan --all-cases --max-calls 12 --source-report examples/evaluation/generation-requests-controlled-six.json
```

本地模拟六题两版，输出仍须是新文件：

```powershell
.\.venv\Scripts\python.exe backend/scripts/compare_prompts.py --mock --all-cases --max-calls 12 --source-report examples/evaluation/generation-requests-controlled-six.json --report docs/evaluation/prompt-six-practice.json
```

完整模拟应有十二次本地尝试和六组输出，真实模型调用为 0，质量比例仍为 `null`。这些是运行后的核对目标，不是六题语义通过的判定。省略 `--max-calls 12` 会按默认上限 2 拒绝，工具不会为了全选而自动放大预算。

模拟成功路径，输出路径必须是新文件：

```powershell
.\.venv\Scripts\python.exe backend/scripts/compare_prompts.py --mock --report docs/evaluation/prompt-practice.json
```

模拟候选版超时：

```powershell
.\.venv\Scripts\python.exe backend/scripts/compare_prompts.py --mock --mock-timeout candidate --report docs/evaluation/prompt-timeout-practice.json
```

模拟输出由脚本指定，基线故意把“检查通过后”改为“检查完成后”，候选复制原文。这只演示引用真实与条件被改写可同时发生，不证明候选提示词能改善真实模型。

真实执行须先按[阶段 1.10](phase1-generation-snapshots.md)采集新公开快照。单题用 `--run --source-report docs/evaluation/generation-snapshot-local.json --max-calls 2 --report docs/evaluation/prompt-real-local.json`；全题集需在同一新快照文件中准备六题，并使用 `--all-cases --max-calls 12`。十二次预算只覆盖这一步的生成尝试；采集完整 RAG 输入还可能调用向量接口，需要单独核对。本轮未执行这些真实调用。受控输入禁止真实执行，模型配置与快照不一致会在调用前拒绝。

退出码：`0` 表示所选版本都取得有效草稿，语义仍可能错误；`1` 表示批次结束但存在执行失败；`2` 表示预检、配置或文件错误。当前不支持续跑，已有输出不能覆盖。用量不等于费用，费用未计价。

## 7. 学习与小实践

先阅读模拟报告中两版的 `answer` 和 `citations`：找到“通过”被改为“完成”的位置，解释为什么两版引用仍有效、程序仍保留 `quality_pass: null`。

再将故障演练的 `--mock-timeout candidate` 改为 `baseline`，用新输出文件运行。预测另一版是否继续、完整配对数是否变化，再核对结果。全部为本地模拟，不需要真实密钥或模型额度。

代码及测试由导师完成，不能据此记录学习者已经独立掌握实验设计。本轮应掌握：控制变量、配对分母、技术失败和未知语义成绩。

模块完成后集中进行三道面试练习：新版改善重启题却使令牌题漏条件时如何决定替换；为什么同一输入仍需考虑重复采样；只有一版成功时怎样报告比较结论。开发中不再反复拆分成细碎问答，未独立回答或实践的能力仍不记为掌握。

## 8. 下一步

六题真实配对及语义核对保留为首版待完成的验收项。开发主线进入完整的混合检索模块，比较词项匹配和向量召回，使用已有带干扰资料题集验证收益，随后再做重排。本轮没有新增数据库结构或真实调用。今天改动继续留在本地，等待学习者决定收尾并确认后再提交和推送。
