# 测试与验证范围

测试覆盖程序行为，真实语义质量需要另外使用标注问题和实际模型评估，两者分别记录。

## 可复现的隔离检查

从前端目录执行（Python路径按本机虚拟环境调整）：

```cmd
..\.venv\Scripts\python.exe -I scripts\check.py all
```

启动器在Node启动前清理继承的业务配置和预加载选项，Vite/Vitest禁用环境文件；
使用归属明确的临时配置目录并在结束后清理。测试与生产构建分进程，防止测试环境影响生产产物。
`resources`只运行资源创建/分页专项，`test`、`build`可分别执行；这不是运行不可信代码的安全沙箱。

从仓库根目录执行后端固定契约：

```cmd
.venv\Scripts\python.exe -I backend\scripts\run_contract_tests.py
```

该入口禁用私有配置，并阻断网络/数据库访问。真实SQL另有独立`eaap_test`安全入口，
不应把日常库地址提供给pytest，也不要用普通全后端pytest绕过已有保护。

## 最近的可核对基线

提交`81b758b`的三项push CI均为completed/success：

- [Frontend CI](https://github.com/cenxv46597/enterprise-ai-agent-platform/actions/runs/39963950060)
- [Backend contract CI](https://github.com/cenxv46597/enterprise-ai-agent-platform/actions/runs/39963949962)
- [Backend PostgreSQL CI](https://github.com/cenxv46597/enterprise-ai-agent-platform/actions/runs/39963950036)

这里核对的是对应提交的运行状态；具体用例范围以工作流白名单和日志为准，不把三项CI称为全后端回归。
该版本本地完整前端38文件1386项、类型/构建通过，资源专项30项包含于全套；固定后端契约274项通过。
后端契约和PostgreSQL CI只覆盖各自固定范围，不是所有业务测试；测试数量不表示回答准确率。

## 历史真实证据

- [六题RAG联调](../evaluation/live-rag-smoke-2026-10-10.md)：小规模真实向量/生成链路，不能推广为企业资料普遍质量。
- [29块带干扰检索评测](../evaluation/retrieval-eval-2026-10-10.md)：限定资料和题集的检索观察。
- [文件名查找与分页浏览器验收](../frontend/frontend-document-filename-browser-validation.md)：独立测试库和合成资料，无模型。

当前网页真实问答/来源全链、TXT实际落盘、策略质量与费用比较仍待专项验收。
没有把模拟HTTP、历史模型评测或下载触发提示替代这些证据；公网生产部署和大规模性能也未在当前范围内验收。
