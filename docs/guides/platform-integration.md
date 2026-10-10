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
`BaseAPI` 原有的公共服务/`SYSTEM_ENV` 前缀规则在注册中心模式下保持不变，不在身份适配器中自行猜测前缀。

## DNS 环境地址发现

设置 `EXPORT_SERVICE_DISCOVERY_IMPLEMENTATION=dns` 后，不需要 Eureka/Nacos/K8s 地址。
该模式根据固定的 50 项服务映射读取 `EXPORT_DNS_*`，不查询注册中心、不缓存实例，也不创建刷新任务。
例如 `app-server` 读取 `EXPORT_DNS_APP`，`seepln-account` 读取 `EXPORT_DNS_ACCOUNT`。
服务名忽略大小写；每次调用重新读取环境变量，未知服务或缺失/空白地址会明确报错。
DNS 模式不加 `SYSTEM_ENV` 前缀。除历史 `*server` 简写外，已知映射中的纯逻辑 URL
（例如 `http://SEEPLN-ACCOUNT`）也会规范化；真实域名、端口、路径仍保留原有直连语义。

以下服务选项新增 DNS 别名。旧键优先，按第一个存在的键取值，不因值为空而回退。

| 选项 | 环境键，按优先级排序 |
| --- | --- |
| 网关 | `EXPORT_WEB_GATEWAY_SERVER`、`EXPORT_GATEWAY_SERVER`、`EXPORT_DNS_WEB_GATEWAY` |
| App | `EXPORT_APP_SERVER_NAME`、`EXPORT_DNS_APP` |
| System | `EXPORT_SYSTEM_SERVER_NAME`、`EXPORT_DNS_SYSTEM` |
| Space | `EXPORT_SPACE_SERVER_NAME`、`EXPORT_DNS_SPACE` |
| 文件 | `EXPORT_PLATFORM_FILE_SERVER_NAME`、`EXPORT_DNS_PLATFORM_FILE` |
| Account | `EXPORT_SERVER_NAME_USER`、`EXPORT_DNS_ACCOUNT` |
| 租户 | `EXPORT_OS_SPACE_TENANT_SERVER_NAME`、`EXPORT_DNS_OS_SPACE_TENANT` |

## Nacos 鉴权

`EXPORT_NACOS_AUTH_MODE` 支持三种模式，默认 `password`。仅配置 AK/SK 不会自动切换模式。

| 模式 | 凭据 |
| --- | --- |
| `password` | `EXPORT_NACOS_USER_NAME`、`EXPORT_NACOS_PASSWORD`；未配置时允许匿名请求 |
| `aksk` | `NACOS_ACCESS_KEY`、`NACOS_SECRET_KEY`，注意没有 `EXPORT_` 前缀 |
| `ram` | `EXPORT_NACOS_RAM_ROLE_NAME`；在 ECS/ACK 环境获取临时 AK/SK 和安全 Token |

`NacosAPI()` 默认读取 `OPTION.nacos`，发现/注册无需展开地址和鉴权参数。
仅配置中心等独立配置场景使用 `NacosAPI(options)`，复用现有 Nacos 配置对象，不修改全局配置。

Naming/Config 使用来源协议的 HMAC-SHA1/Base64 签名。静态 AK/SK 在构造时固定；
RAM 请求前检查到期时间并在到期前 900 秒刷新，使用 IMDSv2，取不到元数据 Token 时回退 IMDSv1。
密码和 RAM 在业务响应 403 后最多刷新并重试一次；静态 AK/SK 的 403 直接失败。
仍支持逗号分隔的多个 Nacos 地址及连接失败时的排名故障切换。

配置中心使用独立的 `EXPORT_NACOS_CONFIG_SERVER_ADDR`、`EXPORT_NACOS_CONFIG_USERNAME`、
`EXPORT_NACOS_CONFIG_PASSWORD`、`EXPORT_NACOS_CONFIG_NAMESPACE`、`EXPORT_NACOS_CONFIG_GROUP`，
与发现/注册共享上述鉴权模式及 AK/SK、RAM 角色设置。专属配置继续覆盖共享配置。
发现器、注册器、配置读取结束时关闭所属鉴权资源，HTTP 连接池仍由平台运行时统一管理。

新增回归覆盖 DNS HTTP 请求、三种鉴权协议、配置覆盖、发现/注册参数传递与关闭路径。
`tests/integration/test_platform_nacos_environment.py` 是可选的真实环境只读检查；未配置相应环境变量时
明确跳过，HTTP 401/403 仅证明可达，不证明鉴权成功。来源的 Celery/SDK 任务上下文用例不适用于本项目运行时。

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
