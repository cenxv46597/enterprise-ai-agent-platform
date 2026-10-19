# 后端隔离契约测试 CI：Windows＋CMD

## 本轮目标与实现范围

在已有前端 CI 之后，为健康接口、Cookie 会话、HTTP 排错编号与引用问答错误响应建立独立的后端契约检查。团队修改后端时，自动发现这些公开行为的回归；本轮不连接真实数据库或模型，不做部署，也不把模拟通过称为完整业务验收。

实现由导师完成。用户选择本方向并允许把入口、CI、验证与文档整合交付；本模块的小实践与逐题面试讨论尚待，不预记为本人独立实现。

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

本轮没有前端代码改动，不重复前端测试；无服务启动、真实数据库/模型或网页联调。主体实现与验证由导师完成，用户小实践及2—3道逐题面试尚待，不预记完成。

只在本地提交；未来推送另行授权，首次远端后端 CI 仍待实际运行核对。已有前端 CI 39241917097 成功是之前阶段的独立证据。

## 官方依据

- [setup-python v7.0.0 发布记录](https://github.com/actions/setup-python/releases/tag/v7.0.0)：本次固定完整 SHA。
- [pytest 参数与环境变量](https://docs.pytest.org/en/stable/reference/reference.html)：显式配置、插件控制和用例执行。
- [pip 配置](https://pip.pypa.io/en/stable/topics/configuration/)：CI 设置 `PIP_CONFIG_FILE=NUL`，禁用配置文件加载。
- [pip 可重复安装层级](https://pip.pypa.io/en/stable/topics/repeatable-installs/)：版本约束与内容哈希的不同边界。
