# 前端持续集成：Windows＋CMD

## 目标和完成条件

把前端已有测试、类型检查和生产构建接入 GitHub Actions。团队提交改动后，远端统一运行检查，尽早发现回归，减少依赖开发者记得手动检查的情况。

本轮先完成配置、静态校验、本地检查和本地提交。首次 GitHub 运行须等另行授权推送后核对，不能把本地通过记为远端成功，也尚未配置必须通过才能合并的分支规则。

用户选择 A：Windows＋CMD，与当前本地环境一致；Linux 可在后续单独验证跨平台兼容性。本次选择确认不记为本人独立论证架构方案；配置由导师实现，小实践和面试讨论待进行。

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

package.json 与锁文件根节点的名称、版本、engines、dependencies、devDependencies 一致。本地使用现有 node_modules，经 Node 直接执行 Vitest、TypeScript、Vite 入口，避免读取本机 npm 凭据配置；具体结果见 [当日进度](../progress/progress-2026-10-19.md)。这不等于全新环境的 npm ci 已通过。本轮未重新安装依赖，首次远端安装、Windows runner 实际执行仍待验证。

## 官方依据

- [GitHub 托管运行器](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)：Windows 标签。
- [工作流语法](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)：触发条件、CMD、权限、并发和步骤运行条件。
- [checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1)、[setup-node v7.1.0](https://github.com/actions/setup-node/releases/tag/v7.1.0)：本次固定版本的发布记录。
- [Node.js 24.18.0 发布文件](https://nodejs.org/download/release/v24.18.0/SHASUMS256.txt)、[actionlint 1.7.12](https://github.com/rhysd/actionlint/releases/tag/v1.7.12)。
