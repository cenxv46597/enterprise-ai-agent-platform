# 本地运行、验证与演示手册（CMD）

这是操作说明，不是自动执行授权。复用现有配置和数据；不要覆盖已有私有配置、重建数据库卷或重新指定owner。
账号、口令和模型密钥只在本机输入，不发送到聊天、不放入命令参数或Git。

## 现有开发环境

后端虚拟环境、依赖、数据库、迁移和私有配置已存在时，只需在两个CMD窗口启动进程。
先确认8000/5173没有已有实例，避免启动重复进程；数据库是否正常另看`/ready`。

```cmd
cd /d D:\workproject\enterprise_AI_agent_platform
.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
```

```cmd
cd /d D:\workproject\enterprise_AI_agent_platform\frontend
npm run dev
```

网页使用`http://127.0.0.1:5173`，健康检查`http://127.0.0.1:8000/health`，数据库就绪检查`/ready`，API说明`/docs`。
这些开发命令会按正常应用规则读取本机配置；下面测试入口才是禁用私有环境文件的入口。
不要把开发服务开放到公网。公网部署还需要HTTPS、入口防护、备份恢复等单独工程。

新机器需安装项目要求的Python与Node版本、锁定的前后端依赖，并由操作者根据配置模板建立私有配置、
数据库及正常迁移。模板不含可登录密码。已有机器不重复初始化；本次没有执行安装、迁移或账号变更。
新账号可使用已有后端注册接口或交互式注册CLI，见`python -m app.auth.register_local --help`；
网页使用已有已注册账号登录。资源创建会把当前用户加入新空间，不自动取得旧空间访问权。

## 全新机器的准备顺序（由操作者执行）

使用Python 3.13与项目`package.json`允许的Node版本，先安装依赖：

```cmd
cd /d D:\workproject\enterprise_AI_agent_platform
python -m venv .venv
.venv\Scripts\python.exe -m pip install --constraint backend\constraints-ci.txt -e .\backend[dev]
cd frontend
npm ci --no-audit --no-fund
```

由操作者在本机编辑器根据`.env.example`建立私有配置；只在原本不存在时新建，配置数据库连接、
随机数据库口令和至少32字符的签名密钥，模型密钥可以等需要真实模型时再配置。
不要把真实值作为命令参数、截图、日志或聊天内容。不要运行会展开并显示口令的Compose配置输出。
数据库口令与连接配置必须一致，已有数据卷的口令不会随配置文件自动改变。

仅对确认是全新环境的机器，依次准备数据库与迁移：

```cmd
cd /d D:\workproject\enterprise_AI_agent_platform
docker compose -f compose.yaml up -d --wait db
.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
.venv\Scripts\python.exe -m app.auth.register_local --username demo_user
```

注册CLI只接受用户名参数，密码在本机终端隐藏输入；它检查本机local数据库和迁移，不自动修改owner。
然后按上面的两个窗口启动服务，登录后从网页创建空间和知识库即可。这里是复现步骤，
不是本轮已经在新机器运行过的证据；生产环境不能照搬开发服务设置。

## 不调用模型的可复现检查

```cmd
cd /d D:\workproject\enterprise_AI_agent_platform\frontend
..\.venv\Scripts\python.exe -I scripts\check.py all
```

仓库内启动器在Node启动前去掉继承的业务配置及Node预加载选项；Vite/Vitest设置`envDir:false`。
独有临时配置目录在结束后清理。`all`运行全前端模拟测试、类型检查和构建；`resources`只跑资源创建/翻页专项，
`test`、`build`可分别执行。安装了可用Python时，`npm run check`等价于`all`。
该入口用于已知测试的环境隔离，不是执行不可信代码的安全沙箱。

```cmd
cd /d D:\workproject\enterprise_AI_agent_platform
.venv\Scripts\python.exe -I backend\scripts\run_contract_tests.py
```

后端入口只运行固定契约白名单，并阻断网络/数据库访问；不是全后端回归。
真实SQL另用已有`run_postgres_tests.py`安全入口、独立`eaap_test`，先核对运行目标；本次没有重跑真实SQL。
不要用旧历史首页中的普通全量pytest或读取`.env`的片段替代当前入口。

## 演示顺序

1. 说明业务目标与架构，用已注册账号登录，创建一个工作空间和知识库；展示分页或文档文件名/状态查找。
2. 上传公开虚构的TXT/Markdown资料，展示parsed；手动切块，解释文本块和字符位置。
3. 使用已准备的ready资料展示检索与引用问答。若需要现场向量化/检索/生成，应先明确模型调用预算并取得授权。
   当前会话仍暂停真实模型任务，可改为讲代码和历史限定范围的验收证据，不能伪装成现场成功。
4. 对已有已确认答案查看完整来源和高亮，说明原文存在与答案正确的区别；导出TXT后到下载列表核对落盘。
5. 时间允许再介绍受控会话的条件补充/标准估算，以及固定工作流的检查点、回执和只读历史。
   不把这些演示描述成任意Agent自主执行或通用可视工作流。

可用[虚构差旅制度](demo-policy.md)准备演示：询问住宿上限时应同时检查450元、发票和提前书面批准的例外；
询问提交材料时应核对三项材料与10个工作日；询问“银行几天到账”应识别原文未规定，
不得把审批通过当作到账。这些是待验收的预期事实，不是本轮真实模型测试结果。

遇到未知故障先记录当前页面、操作与合法排错编号，通过服务端摘要排查；不输出原始私有错误或密钥。
停止等待不是撤销，重试前判断上次可能已完成的写入/模型调用。正常弃答不是故障，也不证明库内没有资料。
