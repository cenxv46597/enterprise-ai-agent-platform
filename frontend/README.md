# Frontend · React + TypeScript

网页提供资料管理、向量/混合检索、带引用问答，以及已有受控会话和固定工作流入口。
完整安装及服务配置见[快速开始](../docs/product/getting-started.md)。

## 启动开发服务器

```cmd
npm ci --no-audit --no-fund
npm run dev
```

默认监听`127.0.0.1:5173`，`/api`代理到`127.0.0.1:8000`。使用已注册账号登录，不在JavaScript存储JWT。
创建资源、上传、切块、向量化、生成和工作流动作均需明确提交，不自动重试未知写入。

## 隔离验证与构建

在本目录执行：

```cmd
..\.venv\Scripts\python.exe -I scripts\check.py all
```

模式：`all`为完整测试及生产构建，`test`为全部模拟测试，`build`为类型检查和生产构建，
`resources`为资源创建/分页专项。启动器在Node启动前清除继承业务配置，禁用环境文件，
测试和生产构建使用独立进程；原`npm test/build`保留，隔离复现优先使用上述入口。

`src/api.ts`处理HTTP和响应校验，`App.tsx`协调页面与请求状态，其他组件按业务页面组织。
测试与组件相邻。页面当前选择和已提交快照分开，来源局部失败不会覆盖原已确认答案。
即时问答刷新仍丢失；TXT导出只触发浏览器下载，不等于服务器历史或保存成功。
