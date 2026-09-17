# 平台接入基础：服务地址、发现与账号资料

## 服务地址不等于可直接访问的域名

平台部署中 `EXPORT_SYSTEM_SERVER_NAME=http://system-server` 是正常配置。
它表示逻辑服务名，调用方先通过 Eureka 等已配置的服务发现获取实例地址，再发 HTTP 请求；
不能交给普通 DNS 解析，也不应要求运维为适配 DeepAgent 去掉 `http://`。

共享入口为 `deepfos_platform.discovery.target.normalize_service_target`，由原有
`BaseAPI` 和 DeepAgent 三条身份适配链路共用。它沿用原客户端的窄匹配规则：
完整匹配 `http(s)://([A-Za-z-]+server)`（忽略大小写）时提取服务名；不按子串猜测服务。

| 配置示例 | 处理方式 |
| --- | --- |
| `system-server` | 按服务名发现实例 |
| `http://system-server`、`https://system-server` | 先归一化成 `system-server`，再发现实例 |
| `https://test.example/deepfos-server/system-server` | 身份适配器直接请求，保留网关路径 |
| `http://127.0.0.1:8080`、`http://system-server:8080` | 身份适配器按显式地址直连 |
| `https://system-server.example` | 身份适配器按真实域名直连 |

逻辑服务 URL 不附加端口或路径（包括尾斜杠）；这些不属于上述历史简写规则。
简写中的 HTTP(S) 不强制实例传输协议，实际实例地址与协议由服务发现返回。
这不是 HTTP 失败后的隐式重试：发现失败直接按调用方规则报告不可用，不再尝试逻辑主机 DNS。
普通外部 URL 失败也不转入 Eureka。

服务发现方式由 `EXPORT_SERVICE_DISCOVERY_IMPLEMENTATION` 决定。Eureka 使用
`EXPORT_EUREKA_DEFAULTZONE` / `EXPORT_EUREKA_SERVER_ADDR` 获取注册实例；本地 Profile
可以使用 `local`，通过 `EXPORT_WEB_GATEWAY_SERVER` / `EXPORT_GATEWAY_SERVER` 拼接网关地址。
因此本机通过网关成功，不等于部署容器的 Eureka 或实例网络已验收。
`BaseAPI` 原有的公共服务/`SYSTEM_ENV` 前缀规则保持不变，不在身份适配器中自行猜测前缀。

## 三条账号链路

| 功能 | 服务配置 | 路由与含义 |
| --- | --- | --- |
| 会话页批量回显用户名 | `EXPORT_SYSTEM_SERVER_NAME` → `OPTION.server.system` | `POST /user/get-user-detail-list`；请求体为 User ID 数组，System 转查 Account，跨空间 |
| 左下角当前用户 | `EXPORT_SERVER_NAME_USER` → `OPTION.server.account` | `GET /api/user/full-detail`；读取当前登录账号 |
| Console 管理员校验 | 同上 | `GET /oauth/checkEnterpriseAdmin`；验证管理员权限 |

“System 入口”和“Account 用户来源”不矛盾。会话用户名查询不需要 App、Space，不伪造目标用户身份；
只转发当前管理员允许的认证头，兼容 UUID 和字符串形式的历史数字 ID。
显示名优先用户名，其次昵称，最后真实 ID。权限缓存按调用者及凭证隔离。
资料缺失与服务不可用分别记为 `missing`、`unavailable`；401/403 不能伪装成用户不存在。

## 2026-09-17 回归与排障依据

Test 会话回显 ID 的原因是身份适配器把所有带协议的地址当作直连 URL，绕过原有
`BaseAPI` 的服务名归一化。日志表现为 `target_origin=http://system-server`、
`phase=request`、`error_type=gaierror`。此前从 Space 改到 System 只更换查询入口，
没有消除这个地址解析问题。保留运维配置，在代码层统一识别规则。

日志中的 `discovery_type` 只代表配置模式，不能单独证明该请求经过注册中心；
应结合归一化的 `profile_service`、最终 `target_origin`、错误阶段和 HTTP 状态判断。
日志不记录 Token、Cookie、完整响应正文或用户资料。

回归入口：

- `tests/unit/test_platform_service_targets.py`：服务名兼容、真实地址保留、当前用户/管理员适配及失败不走 DNS。
- `tests/unit/test_diagnostic_users.py`：批量用户名、数字 ID、认证、回调、日志与降级。
- `tests/integration/test_platform_user_discovery.py`：本地 HTTP 注册中心返回 Eureka XML，真实实例选择和 HTTP 用户查询；只用测试账号。
- 本地 8000 使用 Test Profile 和 `--read-only-preview` 验证真实会话 API；显式设置
  `EXPORT_SYSTEM_SERVER_NAME=http://system-server`，经过本地 `local` 发现适配到 Test 网关。
  2026-09-17 首批 5 个账号中 4 个解析成功、1 个上游缺失，当前登录用户正常；不修改共享库或触发迁移。
- 本次相关回归共 83 项通过，浏览器实测会话用户列已显示用户名；测试浏览器及临时快照已清理。

上线仍需在部署实例复核 Eureka 解析后的真实地址及会话 API。更完整的回显、预算和权限约束见
[会话诊断](diagnostic-sessions.md)，管理员门禁见 [Console 管理权限](console-admin-access.md)。
