# ADR 0030：固定期限的 HttpOnly 网页登录 Cookie

状态：已采纳，2026-10-15。替代 ADR 0029 中仅页面内存保存 JWT 的网页登录方案；
React/Vite 开发代理与工作空间第一页范围不变。

## 原因与决定

学习者已实际验证登录、查看空间和刷新回登录页，随后提出关闭浏览器后仍希望保持登录一段时间。
比较 localStorage 保存 JWT 与 HttpOnly Cookie：前者改动较少但页面脚本可直接读取令牌；
选择后者，浏览器保存并携带凭据，前端只持有用户和到期时间。Cookie 不保存密码。
HttpOnly 减少脚本直接读取令牌的风险，仍需防范 XSS 代发请求和 CSRF。

沿用现有 30 分钟 JWT，不引入刷新令牌、服务端会话表或迁移。登录设置带固定 Expires/Max-Age、
HttpOnly、SameSite=Strict、Path=/ 的 host-only Cookie；生产启用 Secure，local/test 允许 HTTP。
生产必须显式配置 HTTPS `EAAP_WEB_ORIGIN`。恢复校验原签名、原期限和数据库用户，
返回原 expires_at，不签新令牌、不重新设置 Cookie。10:00 登录、10:20 重开，仍在 10:30 到期。
学习者正确选择 10:30；未独立说明理由，不记为独立完成会话设计或代码实践。

网页新增三个接口：POST `/api/v1/auth/web/login`、GET `/api/v1/auth/web/session`、
POST `/api/v1/auth/web/logout`。前两项返回 user 和秒级整数 expires_at，均不返回 JWT；
退出成功返回 204 并删除同名同 Path Cookie。旧 `/auth/login` 的 Bearer 契约和 CLI 保留。

## 身份与请求边界

普通业务请求带 Authorization 头时只验证该头，无效时不回退 Cookie；无该头才尝试 Cookie。
网页恢复接口只接受 Cookie，避免混用身份。成员角色继续每次查数据库，不放在 Cookie 决策。
网页登录/退出，以及所有使用 Cookie 身份的不安全 HTTP 方法，都验证精确 Origin 与
`X-EAAP-Web: 1`，缺失、null 或错配拒绝。SameSite 不代替该检查，不根据不可信 Host 推导允许来源。
Bearer 客户端仍可不带浏览器 Origin。API 响应设置 no-store，网页参数错误不回显请求密码。

前端启动先恢复，401 正常进入登录页，网络故障显示明确的恢复重试入口。
退出必须等后端确认；失败不宣称 Cookie 已删除。保留请求取消、操作序号和原到期清理，
迟到结果不能恢复已退出页面。取消等待不等于后端没有执行登录，页面提供显式恢复入口。

## 限制

退出影响当前浏览器配置共享的 Cookie；其他标签页在后续请求/刷新时获知失效，不承诺即时同步。
删除 Cookie 不撤销已复制 JWT 或其他设备的登录。浏览器清除/阻止 Cookie 后需要重新登录。
签名密钥若为本机临时值，后端重启使原凭据失效。本步没有自动续期、长达数天的登录或生产部署验收。
操作与验证见[网页登录恢复记录](../frontend/frontend-session.md)。
