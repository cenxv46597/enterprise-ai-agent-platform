# 独立 PostgreSQL 集成 CI

2026-10-22接续[引文单块读取](../backend/document-chunk-read.md)：固定清单增加test_document_chunk_read_db.py的精确节点，当前4文件19节点，本地19项/29.84秒通过，cleanup_ok=True；入口安全133项/2.27秒通过，漏新节点拒绝判绿。两者pytest/final0、skip/xfail/violations均0。新用例在独立eaap_test中验证第24/25块、真实成员与范围JOIN、六列无向量投影、旧列表和数据未变；临时身份/schema清理后角色0、vector仍0.8.6。新版本未推送、未运行云端；下文18项远端证据只属于原版本。

2026-10-21新增升级场景的远端补证：[39591550657](https://github.com/cenxv46597/enterprise-ai-agent-platform/actions/runs/39591550657)在`e41be96`、push/attempt1全部成功，实际日志入口132项/0.66秒、固定SQL18项/5.53秒及清理通过；Ubuntu24.04/Python3.13.7安装/pip check与容器停止成功。该版本已明确授权推送并核对真实ref。后续前端分页仍只本地，不能继承本次推送授权或云端结果；下文未推送/远端17项为各自旧阶段。

接续范围已扩为[固定0009→0010升级检查](../backend/auth-upgrade-ci-validation.md)：原17项加1项正式Alembic旧资料保留验证，共18项。测试库vector扩展由管理入口检查/按需预置，临时身份权限和随机schema清理范围不扩大。本次18项尚未推送，以下39565175267和39566111253等17项远端结果只证明上一版本；当前结果见最新进度。

2026-10-21首次远端已通过：[GitHub运行39565175267](https://github.com/cenxv46597/enterprise-ai-agent-platform/actions/runs/39565175267)，对应89828d9、push、attempt1、completed/success。Ubuntu安装/pip check成功，日志实际入口119项/0.29秒、真实SQL17项/5.19秒，全通过、无skip/xfail/违规，临时身份/schema和服务容器清理成功；同SHA的前端/契约CI也成功。以下待授权/待远端等保留实现及教学时的历史，完整证据见[当日进度](../progress/progress-2026-10-21.md)顶部。

2026-10-21 接续[后端隔离契约 CI](backend-contract-ci.md)。用户要求继续开发与学习，导师选择补齐已列出的独立数据库任务，先实现已有工作流记录和就绪探测的真实 SQL 检查。

## 业务目标和本轮边界

改动列表过滤、排序、成员权限或保存逻辑时，模拟 Session 和 SQL 编译不能证明 PostgreSQL 实际执行正确。本任务固定运行[工作流记录14项](../../backend/tests/test_answer_workflow_listing_db.py)和[就绪探测3项](../../backend/tests/test_readiness_db.py)，检查真实查询、跨 Session 持久化、只读参数、超时和认证失败。

这17项使用真实数据库、合成业务数据和受控身份来源；不发送检索、生成或工作流执行请求。列表仅建立所需六张表，不验证 Alembic 升级、pgvector 检索、JWT/Cookie 或浏览器全流程。镜像包含 pgvector 并不代表扩展已验收。

## 方案比较

- 保留原有 Windows＋CMD 前端 CI 和契约 CI。把数据库加入原契约入口会混淆“禁止数据库”和“必须连接数据库”的失败原因，因此新增独立工作流。
- 新数据库任务采用 Ubuntu24.04 服务容器。GitHub 服务容器要求 Linux runner；Windows 原生 PostgreSQL 需要另行管理安装和扩展。云端步骤使用 Python 参数列表，本机操作继续 CMD。环境选择先按已说明的建议方案推进，随后本人通过异步选项明确确认采用Ubuntu临时服务容器；记录为本人选项确认，选型理由仍由导师解释。
- 复用现有 PG17/pgvector 镜像基线并固定已核对的摘要；CI新建 `eaap_test`，不使用日常 Compose、持久卷、本机 `.env` 或个人 GitHub secrets。
- [Linux候选约束](../../backend/constraints-postgres-ci.txt)沿用已有版本并移除 Windows 专属 `pywin32`。本次Ubuntu24.04/Python3.13.7远端安装和pip check已通过；该清单仍不是完整Linux锁文件或带哈希锁文件，隔离构建依赖未固定，也不扩大为其他平台/版本已验证。

## 文件和执行顺序

[backend-postgres-ci.yml](../../.github/workflows/backend-postgres-ci.yml)在 main 推送、面向 main 的 PR 和手动触发时运行。只读仓库权限、checkout不保留凭据、15分钟超时、同分支/PR取消旧运行。

执行顺序：服务容器健康检查 → Python3.13.7 → 约束安装 `.[dev]` → `pip check` → 入口安全检查 → 17项真实 PostgreSQL 检查。Linux禁用pip配置使用 `/dev/null`；原Windows任务继续 `NUL`。

服务管理员口令是工作流内明显标记的合成一次性容器输入，与任何现有账号无关。测试进程不使用它连接TCP，而是通过容器内固定socket创建本次随机临时角色，再以该角色经loopback连接随机映射端口。

## 专用入口的隔离和成功标准

[run_postgres_tests.py](../../backend/scripts/run_postgres_tests.py)复用契约入口的配置隔离及网络防护，另行放行本次受控 PostgreSQL 连接。

1. 测试收集前清除继承的EAAP/PYTEST配置，禁用dotenv、secret来源、pytest插件自动加载、conftest和外部pytest配置；libpq的PG变量也清除。不读取或打印已有值。
2. 管理命令清除容器内继承环境，固定Unix socket、5432、`eaap_test`及所选管理身份，禁用默认passfile。还核对实际库/身份和Docker端口映射。
3. 每次新建随机 `eaap_check_<uuid>`，非超级用户、无建库/建角色权、连接数4、口令有效1小时；生成口令只在内存使用，管理SQL传SCRAM verifier。角色碰撞时不创建或清理该既有身份。
4. SQLAlchemy与psycopg只允许本次loopback端口、角色和`eaap_test`，必须有显式口令、无query/service/hostaddr覆盖。底层连接禁用默认SSL/GSS及passfile来源。错误口令负例仍访问同一目标。
5. 外部HTTP、Python socket等继续阻断，违规即使被捕获也记录。此防护服务于固定受信测试，不是运行恶意代码的沙箱。
6. 固定精确17个测试节点。缺节点、同数替换、skip、xfail、XPASS、未全部通过或违规均不能成功；打印原始pytest与最终退出码。
7. finally仅禁用本次角色、终止其在`eaap_test`中的连接、清理它拥有且符合完整随机命名的schema，再删除角色并核对。不确认归属则停止扩大清理；清理失败使整体非零。强杀/断电仍可能无法完成finally，过期口令不会自动删除角色。

数据库用例各建自己的随机schema，测试结束清理；未调整日常数据库或账号。临时角色没有“整个集群绝不能访问其他库”的数据库级保证，本入口通过固定连接目标和实际库核验限定受信测试范围。

## 本机复现：CMD

先单独检查入口，不需要Docker或数据库：

```bat
.venv\Scripts\python.exe -I backend\scripts\run_postgres_tests.py --check-entry
```

确认原项目数据库容器运行、独立`eaap_test`可用后：

```bat
.venv\Scripts\python.exe -I backend\scripts\run_postgres_tests.py --docker-exe "<已核对的docker.exe完整路径>"
```

默认只管理原项目容器，固定本机5432和`eaap_test`，不自动启动/重建容器、不读取密码。CI分支要求显式提供完整容器ID和映射端口，不接受任意pytest参数；不要用普通pytest替代此入口。

原[桌面专项入口](../../backend/scripts/check_workflow_listing_db.py)及历史成绩保留，新入口用于此CI固定联合范围。

## 验证与学习记录

本轮结果以[2026-10-21记录](../progress/progress-2026-10-21.md)为准。入口安全测试使用合成哨兵与替身，不能算作新增业务数据库测试；本机Windows结果也不能代替Linux依赖安装和首次GitHub运行。

最终入口安全117项/1.38秒通过；原容器独立测试库17项/21.88秒通过；另外新建无卷临时容器，以CI管理身份/随机映射端口复核同17项，12.12秒通过。两次数据库结果均无skip/xfail/违规、临时角色/schema清理成功，临时容器也已核对并删除。三份workflow静态检查、候选版本与直接依赖检查通过。初次权限/测试夹具及整数端口误拒绝的失败经过保留在当日记录，不把最终通过扩大成迁移、模型或云端验收。

实现、测试和文档由导师完成；本模块小实践、自然面试讨论及本人参与程度后续逐项记录，不以确认继续或表示明白代替独立掌握。前模块创建页编号已开始讲解：本人对`if (!notice)`作用经纠正后理解，编号用途和从响应头到页面的传递主要由导师解释，无本人代码修改或独立完整复述。

随后本人按导师给定文件、参数组和内容，实际补充`{"dbname": "postgres"}`拒绝案例并运行`--check-entry`。截图显示**119项通过/1.41秒**，pytest_exit=final_exit=0，collected=passed=119、skip/xfail/违规均0；导师核对实际Git差异恰为该一行，两个连接入口参数化使117增加到119。此结果是本人执行的入口安全检查，不是重跑17项真实SQL或云端CI。记录为按提示完成代码补充和本机执行，未记独立设计数据库隔离或完整实现。本模块面试讨论接下来逐题进行，每次一个自然项目问题。

第1道业务场景题本人表示不清楚；导师讲解“创建任务后换Session读取”的持久化验证过程、漏提交可能导致后续查不到的故障，以及119入口/17SQL的范围区别。本人随后确认理解，未独立举例或复述。当前进入第2题：数据库集成测试中的大模型依赖处理，尚未作答；本模块讨论未全部收尾。

后续第2题本人明确不理解问题及多个术语，导师先解释数据库、大模型、依赖和集成测试，再用“创建任务只保存pending，执行才进入后续生成”说明本轮17项使用真实数据库并禁止模型调用的范围。网络、生成变化与费用会干扰数据库检查，模型质量另行验收。本人随后表示明白并询问下一步，未独立回答或复述。

本模块小实践及两道讨论据此按实际程度收尾：本人按提示补一行拒绝案例并亲自运行119项；业务测试设计和模型依赖处理均经导师完整讲解后确认理解，不记独立实现或已独立掌握，不重复补考。下一步建议先授权推送后核对首次Ubuntu数据库CI的安装与执行，尚未获本轮新推送授权；真实检索、模型及Cookie到期等待继续暂停。

随后本人询问“首次云端CI”及为何使用Ubuntu，导师区分本机Windows与GitHub临时运行机器、解释依赖安装和Linux服务容器。本人确认理解并明确授权执行推送与验证，同时要求以后每次执行结束后先讲清业务目的、做法、理由与实际结果，再进入提问；新术语先解释。本次远端结果以[当日进度](../progress/progress-2026-10-21.md)最新记录为准，以上“未授权/待运行”保留对应阶段历史。

## 官方依据

- [GitHub PostgreSQL服务容器与Linux要求](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers)
- [GitHub步骤shell，包括Python](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idstepsshell)
- [pgvector官方Docker说明](https://github.com/pgvector/pgvector#docker)
- [PostgreSQL17 libpq连接参数](https://www.postgresql.org/docs/17/libpq-connect.html#LIBPQ-PARAMKEYWORDS)
