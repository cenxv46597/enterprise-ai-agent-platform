# 后端隔离契约测试 CI：Windows＋CMD

2026-10-22远端补证：[Backend contract CI 39791937307](https://github.com/cenxv46597/enterprise-ai-agent-platform/actions/runs/39791937307)在已授权推送的119b104、push/attempt1已completed/success，Windows安装/pip check及收尾成功，日志257项/5.73秒、collected=passed257、skip/xfail/violations0、pytest/final0。当前12文件版本已有真实云端证据；下文未推送为历史，详见[当日回执](../progress/progress-2026-10-22.md)，后续push另授权。

2026-10-22接续[引文单块读取](../backend/document-chunk-read.md)：固定白名单新增test_document_chunk_read.py，当前12文件257项本地通过（10.98秒），pytest/final0、skip/xfail/violations均0。新增15项检查登录/成员边界、四层ID与六列投影、旧列表兼容；不连接数据库。workflow配置未变，新版本仅本地，远端历史成绩不替代本次运行。

2026-10-22接续[工作流状态筛选](../frontend/frontend-workflow-history-status-filter.md)：将既有test_answer_workflow_listing.py加入固定白名单，使本次列表规则纳入持续检查；当前11文件242项本地隔离通过（27.02秒），无skip/xfail/外层违规，pytest/final0。workflow未变，新版本尚未推送，不能将本地结果写成新远端成功；下文10文件152项为原版本证据。

2026-10-21远端补记：已按用户授权推送，`612650c`的[首次后端CI39528614169](https://github.com/cenxv46597/enterprise-ai-agent-platform/actions/runs/39528614169)全部步骤成功，首次安装与隔离套件执行已有远端证据；下文“仅本地/首次待运行”为实现轮历史。随后新增的[独立PostgreSQL CI](backend-postgres-ci.md)另行验证，不改变本入口禁止数据库的职责；新工作未来推送仍需授权。

## 本轮目标与实现范围

在已有前端 CI 之后，为健康接口、Cookie 会话、HTTP 排错编号与引用问答错误响应建立独立的后端契约检查。团队修改后端时，自动发现这些公开行为的回归；本轮不连接真实数据库或模型，不做部署，也不把模拟通过称为完整业务验收。

实现由导师完成。用户选择本方向并允许把入口、CI、验证与文档整合交付；本模块阅读实践及讨论已按下文实际参与程度收尾，不记为本人独立实现或完整掌握。

## 方案取舍

- 直接执行后端全量 pytest 会混入数据库集成用例；本步采用固定白名单，新增测试需要先审核其隔离条件。独立 PostgreSQL CI 后续另做。
- 仅在 fixture 中清理配置太晚：`test_health.py` 收集时就导入 `app.main` 并创建应用。专用入口在收集之前禁用私有配置来源，再交给 pytest。
- 测试里的替身保留真实路由、HTTP 状态与响应契约；入口再阻断意外外部连接。拒绝行为要计数，即使被业务异常处理吞掉，整次检查也不能变绿。
- 沿用 Windows＋CMD，固定 CPython 3.13.7 / x64；先形成一套与本机相同版本的证据，不声称覆盖其他操作系统或项目声明的所有 Python 版本。

## 文件与执行路径

- [工作流](../../.github/workflows/backend-contract-ci.yml)：main 推送、面向 main 的 PR、手动触发；只读仓库权限、15 分钟超时，同 PR/分支取消旧运行，checkout 不持久保存凭证。
- [专用入口](../../backend/scripts/run_contract_tests.py)：固定测试白名单、配置隔离及连接防护。任意目录调用均按脚本位置定位源码，不转发任意 pytest 参数。
- [入口安全测试](../../backend/tests/test_contract_runner.py)：验证入口隔离及失败判定，和业务契约一起执行。
- [版本约束](../../backend/constraints-ci.txt)：从当前环境的后端 runtime/dev 传递闭包取得 56 个精确版本；安装仍由 `backend/pyproject.toml` 的 `.[dev]` 驱动。

CI 顺序为：配置 Python → 安装约束后的后端及开发依赖 → `pip check` → 专用入口。依赖安装会访问公共包源，套件执行阶段不应访问外部服务。没有数据库容器、供应商凭据、自动重试模型或部署步骤。

版本约束不是带哈希锁文件，不能证明下载内容固定；默认隔离构建所需的 `setuptools>=75` 也未由这份约束固定。当前依赖快照只用于 Windows / Python 3.13.7，不能扩大为全部 Python>=3.11 均已验证。更新依赖时需重新核对并验证。

## 本地使用（CMD）

从项目根目录使用已有虚拟环境：

```bat
.venv\Scripts\python.exe -I backend\scripts\run_contract_tests.py
```

无需启动 Docker、数据库、API 或网页服务。不要用普通 `pytest` 替代该命令来复现此隔离套件，也不要直接调用数据库专用 runner。连接防护服务于这份受信测试清单，是回归防护，不是运行不可信代码的系统沙箱。

## 验证与教学状态

本轮固定 9 份既有契约测试加入口安全测试，最终 **10 文件、152 项通过（7.27 秒）**；原始 `pytest_exit=0`，全部 collected=passed，无 skip/xfail，外层 violations=0，最终 exit=0。不与历史 121 项或前端 964 项相加。

从无关目录启动，注入合成无效 EAAP 配置、pytest 参数/插件变量，并放置故意无效的 `pytest.ini` 与会抛错的 `conftest.py`，最终仍按固定清单运行。入口自身另用合成文件验证 dotenv/secret 来源未读取；安全用例的受控连接尝试在内层防护中被阻止，不是实际数据库或模型调用。

前两次受限运行均为149通过、3个临时目录 setup 错误（11.62秒及8.20秒），无业务断言失败：先遇到旧 pytest 临时目录 ACL，改为每次独有目录后，新私有目录仍对受限执行身份不可访问。最终经自动审批允许本地测试执行后，同一套件全部通过；未修改旧临时目录权限，也未读取或清理既有目录。入口只清理此次新建的 `eaap-contract-*` 目录。

两份 workflow 经 actionlint 1.7.12 静态检查，0 errors；临时官方工具经发布校验文件验证 SHA256。56 项约束与已安装 metadata 一致且满足 pyproject 直接依赖；Python AST 与本轮本地文档链接检查通过。没有在本机重新安装依赖，不能由此宣称新环境安装成功。

实现轮没有前端代码改动，不重复前端测试；无服务启动、真实数据库/模型或网页联调。主体实现与验证由导师完成，后续教学参与见下文，不以表示明白替代独立解释。

只在本地提交；未来推送另行授权，首次远端后端 CI 仍待实际运行核对。已有前端 CI 39241917097 成功是之前阶段的独立证据。

## 2026-10-20 教学收尾与参与程度

用户反馈先前只汇报结果、未充分讲解就提问，且问题预设了解内部实现，像考试。导师调整为先讲业务目的、文件职责，再沿真实代码讲命令入口、收集前配置隔离、`/ready` 替身与真实路由、违规记录和最终退出码。用户随后表示明白；不记为本人已独立完整复述。

- 阅读实践：本人独立指出防止 `.env` 私有信息泄露；收集时导入应用、执行 Settings 早于 fixture 的具体顺序由导师补充，无本人代码修改。
- 数据库任务取舍：本人选择保留独立数据库集成任务，明确原因不清楚、猜测仍为泄露风险；契约与真实 SQL/事务验证的职责、运行条件及分任务排障理由由导师讲解。
- CI 内容介绍：本人先回答依赖关系；`pip check` 只是其中一步、接口契约与入口安全检查的完整范围由导师补充。
- 实现阶段说明：本人回答入口判断 CI 步骤；导师澄清工作流先安排步骤，入口核验测试并返回结果，以及本地152通过/静态检查通过、尚未远端运行的证据边界。未取得本人独立完整项目介绍。

原“连接异常被捕获但503断言通过”细节题本人未答，导师直接解释；不额外算独立作答或重复补考。本模块按实际程度收尾，后续结合业务巩固，不反复追问同题。

后续面试先用自然的项目介绍问题，询问业务、技术栈、方案选择、实现与实际结果，再根据回答追问；不预设面试官已熟悉内部文件和防护细节。必要基础先讲，每次只问一个核心问题。

本次收尾仅文档检查和本地提交，没有重跑152项、安装依赖、运行CI或启动服务；原暂停任务与六份文档保护不变。

## 官方依据

- [setup-python v7.0.0 发布记录](https://github.com/actions/setup-python/releases/tag/v7.0.0)：本次固定完整 SHA。
- [pytest 参数与环境变量](https://docs.pytest.org/en/stable/reference/reference.html)：显式配置、插件控制和用例执行。
- [pip 配置](https://pip.pypa.io/en/stable/topics/configuration/)：CI 设置 `PIP_CONFIG_FILE=NUL`，禁用配置文件加载。
- [pip 可重复安装层级](https://pip.pypa.io/en/stable/topics/repeatable-installs/)：版本约束与内容哈希的不同边界。
