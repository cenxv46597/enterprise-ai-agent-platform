# 企业知识与工作流智能体平台

面向求职演示的企业知识应用：资料入库、范围内检索、基于原文回答，以及受控多步任务。
技术栈为 React / TypeScript / Vite、FastAPI / Python、PostgreSQL / pgvector。

**当前完成线：核心演示版开发已收拢，进入整体理解、演示与待授权的真实验收。**
不再把长期企业化愿景拆成无穷小功能。当前版本是本地演示原型，不宣称生产交付或普遍回答正确。

## 从这里开始

- [项目整体指南：业务、架构和面试介绍](docs/delivery/project-guide.md)
- [运行、隔离验证与演示顺序（CMD）](docs/delivery/run-and-demo.md)
- [本次交付范围、验证和限制](docs/delivery/release-2026-10-23.md)
- [文档导航](docs/README.md)

## 一条完整主线

登录 → 创建或选择工作空间/知识库 → 上传解析 → 切块 → 显式预算向量化 →
整库检索或引用问答 → 查看完整来源与高亮 → TXT导出。

工作空间、知识库、文档均支持手动翻页；文档可按文件名和处理状态查找。
检索支持向量/混合方式；问答可选择最多3或5段候选。候选上限、模型上下文和引用数量分别有自己的含义。
模型输出需经过引用归属、连续原文与位置检查；证据不足正常弃答，技术失败单独报错。
即时问答仍仅保留在当前页面，TXT不是服务器历史。

已有受控Agent、持久会话/条件补充、标准估算、只读MCP和固定检查点工作流。
网页接入其中的会话与固定工作流能力，不等于所有后端能力均已网页化。
身份使用登录与工作空间成员边界；当前不是细粒度RBAC或每用户私有会话。

## 不调用模型的验证

```cmd
cd /d D:\workproject\enterprise_AI_agent_platform\frontend
..\.venv\Scripts\python.exe -I scripts\check.py all
```

```cmd
cd /d D:\workproject\enterprise_AI_agent_platform
.venv\Scripts\python.exe -I backend\scripts\run_contract_tests.py
```

当前本地全前端38文件1386项及类型/生产构建通过；固定后端契约274项通过。
前端使用模拟HTTP，契约入口阻断网络/数据库；这些数字不是后端全量、真实模型质量或当前网页端到端验收。
真实SQL有独立安全入口和独立eaap_test库，本次未重跑；具体历史证据在交付记录和原报告中。
CI配置已接仓库内隔离启动器，本次未推送、未取得新的云端结果。

## 仍待确认的边界

当前网页真实RAG/来源全链、下载实际落盘和策略效果/费用比较仍需专项验收。
真实模型调用、实际Cookie到期等待与新push继续按会话授权执行，不由“继续开发”自动开启。
OCR、文档版本、即时问答服务器历史、全量网页管理和公网生产部署属于明确列出的后续扩展。

主体实现与验证多由导师完成，学习者参与程度按实际实践和回答记录，不以仓库代码量夸大个人独立经历。
每日历史记录仍保留，但不再作为产品首页。原首页和原导航分别归档为
[历史首页原文](docs/history/README-before-consolidation-2026-10-23.txt)与
[历史导航原文](docs/history/docs-navigation-before-consolidation-2026-10-23.txt)，其中相对路径保留原文语境，勿按旧命令启动测试。
