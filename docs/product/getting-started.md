# 本地快速开始

本项目目前提供本地部署原型，没有托管在线体验地址。以下命令使用Windows CMD；已有环境直接跳到启动步骤，
不要覆盖私有配置、重新初始化数据库卷或重做owner关系。

## 环境要求

- Python 3.13（当前CI使用3.13.7）。
- Node.js 24（当前CI使用24.18.0；其他可用版本范围见[package.json](../../frontend/package.json)）。
- Docker Compose，以及可用的PostgreSQL/pgvector数据库。
- 需要执行向量化/检索/生成时，再配置对应模型服务；浏览资源和上传解析不需要生成模型。

## 1. 获取项目和依赖

```cmd
git clone https://github.com/cenxv46597/enterprise-ai-agent-platform.git
cd enterprise-ai-agent-platform
python -m venv .venv
.venv\Scripts\python.exe -m pip install --constraint backend\constraints-ci.txt -e .\backend[dev]
cd frontend
npm ci --no-audit --no-fund
cd ..
```

## 2. 配置本机环境

```cmd
if not exist .env copy .env.example .env
```

在本机编辑器中根据模板填写数据库连接、随机数据库口令、至少32字符的JWT签名密钥；
配置向量和生成模型时填写各自的HTTPS接口、模型名称与本机密钥。模板不包含可登录的默认账号或密码。
两种模型分别配置，不要把生成模型名当作向量模型名；一个知识库保持一致的向量模型和维度。
密钥只保存在本机，不放入命令参数、Issue、截图或Git。

Compose和应用数据库配置需使用一致口令。已有数据卷不会因为配置文件变化而自动更换数据库口令；
不要用删除卷来修复已有环境。不要运行会展开显示口令的Compose配置输出。

## 3. 初始化全新环境

仅在确认是全新环境时执行：

```cmd
docker compose -f compose.yaml up -d --wait db
.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
.venv\Scripts\python.exe -m app.auth.register_local --username demo_user
```

注册CLI要求本机local环境和已完成的迁移，只通过终端隐藏输入密码；不会自动取得旧空间访问权。
复用已有账号的环境不需要再次注册。当前注册和成员管理另有后端接口，网页先使用已注册账号登录。

## 4. 启动服务

在仓库根目录打开一个CMD窗口：

```cmd
.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
```

再打开一个CMD窗口进入前端：

```cmd
cd frontend
npm run dev
```

- 网页：`http://127.0.0.1:5173`
- API文档：`http://127.0.0.1:8000/docs`
- 进程健康：`http://127.0.0.1:8000/health`
- 数据库就绪：`http://127.0.0.1:8000/ready`

`/health`不表示数据库或模型可用；`/ready`只检查数据库连接，不等于业务和模型都已验收。

## 5. 完成第一条业务流程

登录后创建工作空间和知识库，上传[公开虚构示例](../delivery/demo-policy.md)，依次完成切块、显式预算向量化。
只有可检索文档才能参与知识问答。输入问题、选择检索方式后手动提交，查看答案和引文，按需打开完整来源并导出TXT。
向量化、问题检索和回答生成可能产生服务商费用，不自动重试；停止等待不保证后端停止。
不准备模型服务时，可先使用[隔离验证](verification.md)理解程序行为，不能把模拟输出作为真实模型效果。

更多操作场景和界面证据见[产品演示](showcase.md)，部署边界见[架构说明](architecture.md)。
