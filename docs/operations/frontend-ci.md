# 前端持续集成：Windows＋CMD

2026-10-23交付收尾：前端新增仓库内`python -I scripts/check.py`启动器，Node启动前清除继承业务配置，
Vite/Vitest禁用环境文件；临时配置归属校验后清理。测试后独立进程执行生产构建，避免继承测试环境。
工作流以固定Python安装步骤和`npm run check:test/check:build`复用入口。最终本地38文件1386项及类型/构建通过，
本次没有push或新云端CI结果；历史远端证据仍仅对应原SHA。细节见[交付记录](../delivery/release-2026-10-23.md)。

2026-10-22远端补证：本人明确授权后，16条既有提交正常快进至119b104；[本次Frontend CI 39791937232](https://github.com/cenxv46597/enterprise-ai-agent-platform/actions/runs/39791937232)在该SHA、push/attempt1已completed/success，日志35文件1228项/40.66秒、类型检查与构建189毫秒通过，依赖安装及收尾成功。同SHA另两项后端CI也成功，见[当日回执](../progress/progress-2026-10-22.md)。下文“仅本地/待运行”保留对应旧阶段，未来新push另授权，六份保护和真实任务暂停不变。

## 最新结果：已推送且首次远端运行通过

上一轮累计12条提交已按明确授权正常推送；远端main实际核对为3ca43dedeb5931af212a348a535361428a3c6f6e。
[首次运行39241917097](https://github.com/cenxv46597/enterprise-ai-agent-platform/actions/runs/39241917097)同一SHA，push触发、attempt1，completed/success；安装、测试、类型检查与构建均成功。
这补齐了远端全新安装和执行证据，但本机没有重新安装依赖，远端具体测试数量/耗时未另取日志，不凭本地964项推造。
教学小实践与三题已按下述程度收尾；没有配置合并门禁、后端CI或部署，也不证明真实数据库/模型业务通过。
本次交接总结只更新文档并本地提交，不重新运行或推送。完整范围见 [收尾交接](../handoff/handoff-2026-10-19-summary.md)。

## 目标和完成条件

把前端已有测试、类型检查和生产构建接入 GitHub Actions。团队提交改动后，远端统一运行检查，尽早发现回归，减少依赖开发者记得手动检查的情况。

实现阶段先完成配置、静态校验、本地检查和本地提交；随后按明确授权推送并核对首次远端成功，见顶部记录。不能只凭本地通过宣称远端成功；尚未配置必须通过才能合并的分支规则。

用户选择 A：Windows＋CMD，与当前本地环境一致；Linux 可在后续单独验证跨平台兼容性。本次选择确认不记为本人独立论证架构方案；配置由导师实现。

## 教学收尾与本次发布授权

三道面试讨论已完成：本人独立指出本机与CI环境不同、本机成功不代表CI成功；正确判断构建失败不满足本轮合并标准，但生产构建含义由导师补充讲解；正确指出依赖下载失败可能来自网络，不能直接判定业务代码错误。
小阅读实践先不理解 on，导师解释三个事件及缩进后，本人正确判断 feature/search 推送且未创建PR不触发该配置。属于讲解后判断，不记为独立编写CI或掌握全部设计。

发布前用户明确授权正常推送累计提交；当时远端main为c997f87，补记教学收尾后实际推至3ca43de，六份受保护文档排除。首次远端结果见顶部；教学收尾时只补文档和diff检查，没有重复运行本地前端测试。该次授权已执行完毕，未来push另行授权。

## 实现

配置位于 [frontend-ci.yml](../../.github/workflows/frontend-ci.yml)。推送到 main、面向 main 的拉取请求及手动操作可以触发检查。仅本地 commit 不触发远端任务。

- 单个 windows-2025 任务，CMD，命令工作目录 frontend，15 分钟超时。
- 固定 Node.js 24.18.0；checkout 和 setup-node 固定完整提交 SHA，注释标明版本。
- 按锁文件执行 `npm ci --no-audit --no-fund`，随后 `npm test`，最后 `npm run build`。
- build 已包含 `tsc --noEmit && vite build`，不重复添加脚本。每个 CMD 步骤只运行一个 npm 命令，失败时后续步骤按默认条件停止。
- 仓库权限仅 contents: read，checkout 不持久保存凭证，关闭自动包管理器缓存。同一 PR/分支的新运行取消过时运行；不同 PR/分支分别分组。

配置没有数据库、真实模型、部署或机密参数步骤，也没有新增产品依赖。前端测试主要使用模拟接口，不能证明真实登录、数据库持久化或真实生成链路通过。超时、安装网络故障也会导致 CI 失败，不能一概判断为业务代码错误。

## 本轮验证

actionlint 1.7.12 静态检查通过；临时下载官方 Windows 版本并核验发布页 SHA256，检查后移除临时工具，未安装到项目依赖。静态检查只证明配置符合该工具的检查规则，不执行远端工作流。

package.json 与锁文件根节点的名称、版本、engines、dependencies、devDependencies 一致。本地使用现有 node_modules，经 Node 直接执行 Vitest、TypeScript、Vite 入口，避免读取本机 npm 凭据配置；具体结果见 [当日进度](../progress/progress-2026-10-19.md)。仅该本地检查不能证明全新安装；随后首次远端 Windows runner 的 npm ci 及测试构建已经通过，见顶部证据。本机没有重新安装依赖。

## 官方依据

- [GitHub 托管运行器](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)：Windows 标签。
- [工作流语法](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)：触发条件、CMD、权限、并发和步骤运行条件。
- [checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1)、[setup-node v7.1.0](https://github.com/actions/setup-node/releases/tag/v7.1.0)：本次固定版本的发布记录。
- [Node.js 24.18.0 发布文件](https://nodejs.org/download/release/v24.18.0/SHASUMS256.txt)、[actionlint 1.7.12](https://github.com/rhysd/actionlint/releases/tag/v1.7.12)。
