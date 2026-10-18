# ADR0031：服务端生成HTTP排错编号

日期：2026-10-18。状态：已采纳。

## 背景

现有日志分散于业务模块，尚无统一HTTP定位信息。会话/工作流请求体内的 `request_id`
已经承担操作识别和幂等语义，不能把它当成每次HTTP请求的唯一排错编号。

## 决策

学习者选择方案A：为进入应用的每次HTTP请求生成新UUID4，忽略入站 `X-Trace-Id`，
通过响应头 `X-Trace-Id`、请求状态及一条安全访问摘要关联本次HTTP调用。
方案B是校验后沿用调用方UUID，便于跨服务关联，但本轮不引入该信任边界。
业务 `request_id`、请求体、幂等重放、数据库结构与模型执行策略保持原语义。

摘要仅含event、trace_id、受限method、应用route模板、status_code、elapsed_ms及outcome。
不读取正文，不记录真实路径/查询参数、用户/资源ID、请求头、Cookie、Token或原始异常。
无法匹配路由用固定标记，未知方法记OTHER。独立logger输出一行JSON到stderr，配置幂等，
不修改root日志器；输出失败不改变业务响应，但可能丢失摘要，不保证日志持久化或送达。

使用纯ASGI中间件，在现有私有响应缓存中间件外侧、框架ServerErrorMiddleware内侧。
未捕获异常的500另注册handler补同一编号及no-store，保留默认通用纯文本正文。
响应开始后出错保留已观察到的状态，不补发500；outcome区分正常完成、异常、中断和不完整响应。

## 结果与限制

编号用于排错，不用于鉴权、幂等判断、证明模型执行或计算费用。
completed只表示应用观察到HTTP交换完成，不是工作流业务completed；状态码也不证明客户端收齐内容。
耗时覆盖本次ASGI调用，可能含流式响应和背景任务；不是浏览器等待时间或Agent运行统计。

这不是完整的分布式追踪，无span、外部平台、跨运行聚合或日志数据库。
前端后续按业务分步接入，目前仅[引用问答HTTP失败提示](../frontend/frontend-answer-trace.md)展示合法编号。
不保证给尚未进入应用的协议错误/服务器拒绝补编号。既有业务日志和Uvicorn异常traceback未整体治理。
启动命令关闭Uvicorn默认access日志，避免其重复记录真实URL；此选项不关闭异常traceback。

验证和学习边界见[模块记录](../backend/http-request-trace.md)。ADR0030继续有效，HTTP编号不续期Cookie。
