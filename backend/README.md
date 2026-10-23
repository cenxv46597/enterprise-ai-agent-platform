# Backend · FastAPI

后端负责资源范围、文档入库、证据回答、受控工具执行以及持久会话和固定工作流。
完整启动步骤见[快速开始](../docs/product/getting-started.md)，模块关系见[架构说明](../docs/product/architecture.md)。

## 代码导航

- `app/api/`：HTTP请求、响应契约与固定错误。
- `app/auth/`：身份、Cookie会话与工作空间成员边界。
- `app/knowledge/`：解析、切块、向量/混合检索、上下文预算和引用定位。
- `app/providers/`：向量、生成、工具决策与可选重排适配器。
- `app/agents/`、`app/conversations/`、`app/memory/`：有界工具决策、条件补充与持久状态。
- `app/workflows/`：固定问答流程、检查点与回执。
- `app/db/`、`alembic/`：持久化模型与迁移。
- `tests/`、`scripts/`：测试、隔离入口和显式评测工具。

## 固定契约验证

从仓库根目录执行：

```cmd
.venv\Scripts\python.exe -I backend\scripts\run_contract_tests.py
```

入口使用固定白名单，禁用环境文件并阻断网络/数据库；不是全部后端测试。
真实数据库测试使用独立`eaap_test`及已有安全入口，不使用日常业务库，不自动更改owner或数据库卷。
真实模型评测脚本与自动回归分开，需显式参数和预算；详见[验证说明](../docs/product/verification.md)。
