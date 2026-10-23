# 参与开发

欢迎围绕可复现的问题和明确业务场景提出改进。先阅读[项目首页](README.md)、[架构说明](docs/product/architecture.md)和[验证说明](docs/product/verification.md)。

## 提交前

1. 描述用户场景、预期行为、当前行为和最小复现，不把整个长期路线合并成一个改动。
2. 沿用已有模块边界；改变HTTP或数据契约时检查旧调用兼容、权限范围和错误处理。
3. 运行与改动相关的隔离测试，注明范围和结果。没有运行的检查明确标注，不把模拟结果称为真实模型验收。
4. PR说明写清问题、最终行为、验证和限制；避免无关格式化、依赖升级和重复生成文件。

```cmd
cd frontend
..\.venv\Scripts\python.exe -I scripts\check.py all
cd ..
.venv\Scripts\python.exe -I backend\scripts\run_contract_tests.py
```

不要在测试或CI中自动运行付费模型任务。数据库测试只使用独立测试库与安全入口，不更改日常库、owner关系或已有数据卷。
问题反馈只提供脱敏摘要和必要排错编号，不上传`.env`、真实账号密码、Token、密钥或企业正文。

项目采用AI辅助开发与结对学习，参与方式和历史记录见[开发档案](docs/development/README.md)。
