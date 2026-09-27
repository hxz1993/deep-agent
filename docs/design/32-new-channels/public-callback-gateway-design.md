# 公网平台回调接入与 Web Gateway 放行方案

> 2026-09-27 · 开发交接稿。只完成源码评估与方案，未修改网关、部署配置或上线接口。
> 核验基线：`web-gateway`，`master`，`cc2c26c513cf7c86e8df7f87f40949c2e7dea3d5`。本地工作区干净；尚未确认 Alpha 部署版本及外层 Ingress 配置。

## 1. 结论与范围

**可以实现。现有 Gateway 已有免登录路径机制，但规则写在代码里；不能直接靠新增一项现成配置完成本次接入。** 建议新增受控的“平台回调路由”配置，首个用途为企业微信自建应用。放行的是 DeepFOS 登录校验，回调接收端仍必须逐次验证企微签名并解密消息。

首版不建设动态注册服务、数据库注册表或管理页面。由开发约定固定回调接口，运维审核并配置路由；一个接口族服务多个渠道实例，不需要每新增机器人就改网关。未来其他第三方平台可沿用这一机制，但各自鉴权由对应接收端实现。

本方案不等于企微应用已经接入：DeepAgent 当前企微能力是 AIBot WebSocket；自建应用的 HTTP 回调接收、验签、去重和回复适配仍需开发。个人微信暂不开发，公众号因暂无账号暂停讨论；飞书另行评估。

## 2. 现有源码确认了什么

下表路径均相对 `deepfos-repos/deepfos_platform/web-gateway/`。源码为准，不将本地配置视为线上实际配置。

| **位置** | **已确认行为** | **本次影响** |
| --- | --- | --- |
| `src/main/java/com/proinnova/gateway/server/filter/handler/AuthHandler.java` | `ignoreUrls` 是硬编码数组；`ignoreAuthentication` 用 AntPathMatcher 匹配路径，不校验 HTTP 方法、路由 ID 或目标服务 | 已有扩展点，但不能把整个 DeepAgent 服务前缀放行 |
| `server/filter/ApiKeyAuthFilter.java`（以下 Java 路径省略共同前缀） | order=-150；调用同一 `ignoreAuthentication` | 与 Token 过滤器统一判定，不能只改一处 |
| `server/filter/TokenAuthFilter.java` | order=-100；免登录列表之外通常需要登录态；另有 OpenAPI 分支 | 企微不会发送平台 Cookie。现有 OpenAPI 分支不是企微回调协议，不能用伪造平台头代替适配 |
| `server/filter/SpaceAppFilter.java` | order=-10；带空间、应用头时可能查状态 | 回调不能继承调用方伪造的业务上下文 |
| `server/filter/EnterpriseUserFilter.java`、`SpaceUserFilter.java` | order=-9、-8；根据用户、企业、空间等请求头进入业务校验 | 回调身份应由服务端绑定，不能信任外部传入的 `user` 等头 |
| `server/filter/SecurityPolicyFilter.java` | order=-1；按配置及用户身份执行业务安全策略 | 明确区分平台业务授权和回调认证，不能靠“恰好没有 user 头”避免进入 |
| `server/filter/ForwardRequestFilter.java` | 最低优先级；根据 `element-name`、`element-type`、`folder-id`/`path` 查目标服务，改写请求 URI | 回调固定转发到约定接收端，必须排除此动态改写 |
| `server/filter/MonitorGatewayFilter.java` | 最高优先级；执行路径黑名单，并记录完整 Query | 保留边界防护与耗时统计；回调参数须在此处脱敏，后置过滤器处理来不及 |
| `server/filter/AccountRouteAccessFilter.java` | 账号路由有独立内部标记，支持 `account.route.additional-rules`；规则限定方法和精确路径 | 可借鉴做法，但它只适用于账号路由，不能拿来直接开放 DeepAgent 接口 |
| `src/main/resources/application.properties`、`application-lease.properties`、`application-local.properties` | 包含显式路由及服务发现配置；所读源码未发现 `/deepfos-server` 外层前缀改写定义 | 上线前必须确认 Ingress 到 Gateway 的真实路径，不能照抄浏览器 URL 作为放行规则 |

表中的 `server/filter/...` 完整前缀为 `src/main/java/com/proinnova/gateway/`。

现有两个鉴权过滤器直接 `new AuthHandler()`。新增配置策略时应改为共享可注入组件，避免两套规则或两次独立解释。

## 3. 公网链路与部署边界

```text
企微服务器
  → 企业主体符合要求的 HTTPS 域名
  → Ingress / 反向代理（TLS、请求体上限、限流）
  → Web Gateway（匹配受控回调路由，不要求平台登录）
  → DeepAgent 回调接收端（逐次验签、解密、校验接收方、去重）
  → 渠道处理 / Agent（服务端绑定执行身份）
  → 企微发送接口（按官方授权调用）
```

**只有网络可达还不够。** 本次实际配置中企微提示域名备案主体需要与企业相同或有关联，临时隧道域名没有通过。必须先确认准备使用的企业域名符合当前账号要求。Gateway 改造解决平台登录拦截，不能解决域名主体不符合要求。

公网 URL 需要由“外部域名＋约定路径”组成。公网地址不要求指向一个新开放端口；可以继续复用现有 HTTPS 入口，由网关内部转发。前提是外层代理没有在到达 Gateway 之前另做登录拦截。

### 三段路径必须一起确认

| **层次** | **示意，非已上线地址** | **负责方确认** |
| --- | --- | --- |
| 企微配置的完整 URL | `https://<企业域名>/deepfos-server/deep-agent-server1-0/api/v1/channel-callbacks/wecom-app/<实例ID>` | 平台运维：域名、证书、主体及公网可达性 |
| Gateway 实际匹配的路径 | 是否已去掉 `/deepfos-server`，当前未核实 | 网关开发＋运维：记录外层 rewrite 和路由命中 |
| DeepAgent 接收的路径 | `/api/v1/channel-callbacks/wecom-app/<实例ID>` | Agent 开发：实现及验证最终路径 |

以上路径为本方案建议，不是当前已有接口。实例 ID 只用于查找配置，不充当鉴权凭据。

### DeepAgent 接入点建议

当前组合部署由 `src/deep_agent/combined_runtime.py` 启动 API 和渠道进程，渠道监听 `127.0.0.1:8010`，内部 API 地址指向本机 API。`deploy/jenkins/README.md` 仍沿用 API 对外 Service/Ingress；`channels/gateway/app.py` 当前是带内部认证的健康、状态和测试接口，并无企微自建应用回调。

首版建议由现有 API 进程承接上述专用回调入口，再经内部认证交给渠道处理链路。这样通常不必为 8010 新建公网入口。接收、验签、可靠接纳与异步处理的进程分工需在实施时确定，不能只是增加一个任意地址反向代理。另一方案是为渠道进程新增内部 Service，由平台网关直连，但需要修改监听、服务与部署配置，改动更大。

## 4. Gateway 建议改动清单

### 4.1 受控路由配置，而非通用免鉴权开关

增加一个可注入的回调策略组件，读取经运维审核的配置。至少包含：

- 固定路由 ID、固定目标服务、允许的方法（首个接口为 GET/POST）。
- 固定回调路径模板及实例 ID 的匹配规则；不能开放整个服务的 `/**`。
- 启用状态、用途和责任方。环境可按既有配置发布方式管理，首版允许随部署生效。

匹配必须同时约束实际命中路由、目标服务、方法和路径。仅凭请求中某个 `public=true` 或自带请求头不能获得放行资格。配置错误拒绝生效；未知路径或方法维持原有登录校验，不能退化为整个服务免登录。

可以使用 Spring Cloud Gateway 显式路由和受控 metadata 存储策略；从 `GATEWAY_ROUTE_ATTR` 获取实际路由。服务发现路由不自动获得豁免。配置中的目标必须受管理员控制，不接受外部任意 URL，避免变成开放代理。

同一实例 ID 的 GET/POST 用于验证和收消息；一个路由模板覆盖已配置实例，由接收端拒绝不存在、停用或配置不完整的实例。因此每个机器人无需单独注册网关规则。

### 4.2 统一过滤链判定

在平台鉴权前生成服务端内部 exchange 标记，供后续过滤器读取；不要复用账号路由标记，也不要从客户端头直接生成。

| **组件** | **修改要求** |
| --- | --- |
| 新回调策略组件/分类过滤器 | 仅对审核配置匹配的路由标记；检测方法、路径和目标绑定 |
| `ApiKeyAuthFilter`、`TokenAuthFilter` | 对该标记跳过平台登录/API Key 校验；原业务请求逻辑不变 |
| `SpaceAppFilter`、`EnterpriseUserFilter`、`SpaceUserFilter`、`SecurityPolicyFilter` | 对回调不执行依赖平台用户头的业务校验；不得跳过网络边界防护 |
| `ForwardRequestFilter` | 标记回调直接保持固定目标，禁止元素头触发改路由 |
| `MonitorGatewayFilter` | 在最早日志位置脱敏回调 Query；保留路径黑名单与耗时统计 |
| 路由、限流和请求体配置 | 显式路由优先于宽泛服务发现；限制请求体、速率、连接与处理超时，返回匹配协议的响应 |

注意：Monitor 已是最高优先级，不能假设后来生成的标记能保护之前的日志。可在 Monitor 中调用同一无副作用策略判断，或统一调整最早分类与日志顺序；最终顺序须用完整过滤链测试确认。

回调入口移除外部携带的平台身份及内部控制头，至少核对 `user`、`token`、`X-Deepfos-Key`、`space`、`app`、`enterprise-code`，以及元素转发用的 `element-name`、`element-type`、`folder-id`、`path`。其他 OpenAPI/内部身份头按实际消费点一并梳理。接收端不能据此建立登录身份。代理来源头只信任已知上游代理。

不得修改企微验签所需参数、重复参数语义和原始消息体；特别检查 `+`、`%`、Unicode 及 URL 编码。拒绝有歧义的路径编码、重复解码或路径穿越形式，避免匹配路径与实际转发路径不同。GET 验证的原文响应不能被包装成平台统一 JSON、登录跳转或 HTML。

## 5. 回调接收端必须同时实现

免平台登录上线的前提是接收端认证已完成，不可先开放一个未验签接口。

| **请求** | **行为** |
| --- | --- |
| 配置验证 GET | 根据实例配置验证签名，解密 challenge，校验接收方，返回官方要求的明文响应 |
| 消息 POST | 验签、解密、检查接收方与实例绑定，检查消息结构，按协议处理时间戳与重放，再进入业务 |
| 无效请求 | 不触发 Agent，不调用业务工具，不用宽泛异常兜底成成功 |
| 重复投递 | 依据平台消息标识及实例去重；可靠接纳后快速响应；后台执行和回复不重复 |
| 长任务 | 将接收确认与模型执行分开，避免等待模型导致平台重试；发送回复使用企微官方接口及权限 |

Token、EncodingAESKey 用于回调校验/解密；应用 Secret 用于应用接口授权。不能把这些值写在公网 URL 或日志中。业务使用者与权限由配置和验证后的渠道身份绑定，不能把所有消息自然视为平台管理员。

去重与处理状态需考虑进程重启、多副本及投递重试；单进程内存集合不能支撑可靠接纳。具体复用现有存储还是增加收件记录，在渠道实施课题中确定，本交接文档不直接增加表。

后台应在创建配置时明确展示完整回调地址，并允许先保存未启用的配置；填写 Token/AESKey 后才开放有效验签。避免“企微需要地址才能保存、应用需要企微保存后才能显示地址”的流程死结。

## 6. 要不要做注册机制

**现在做配置登记，不做服务运行时自行注册。** 首次需要网关开发提供通用、受控的回调策略；之后新增经评审的平台回调可以通过配置发布完成，不要求每次再向硬编码数组加路径。

只有出现大量业务服务独立发布、频繁新增接口的实际需求，才考虑控制面注册。届时至少需要：服务身份认证、可注册服务和路径边界、审批/审计、版本发布与撤销、目标白名单、冲突检测；注册者不能声明任意现有业务接口免登录。控制面不公开给第三方平台，也不能代替接收端验签。

首版不需要新增数据库、注册 UI 或让企微先调用鉴权接口。企微仍按原有方式直接 GET/POST 同一个已配置 URL。

## 7. 实施分工、发布与验收

| **责任方** | **交付项** |
| --- | --- |
| 平台网关开发 | 受控回调策略、统一过滤链豁免、身份头隔离、固定转发、日志脱敏、自动化回归 |
| 运维 | 合规企业域名与证书；确认外层代理没有登录拦截；核对三段路径；配置路由、限流与请求体上限 |
| Agent 开发 | 自建应用回调、验签解密、可靠接纳/去重、身份绑定、发送回复、配置展示 |
| 联调人员 | 企业后台保存验证、真实收发消息、异常与回滚验证 |

发布顺序：先部署并内部验证接收端，回调保持停用；再发布 Gateway 代码和受控配置；确认公网路径后启用一个测试实例；最后在企微完成验证与收发。回滚时关闭该回调策略/实例，保留正常平台登录路由与已接纳任务的处理记录。

必须使用完整 Gateway HTTP 过滤链与受控下游验证，不能只单测匹配函数：

- 正确 GET/POST 在无 Cookie 情况下到达唯一目标；错误签名、未知实例不能调用 Agent。
- 同服务其他 API、Console、管理接口仍受原有鉴权保护；错误方法、其他目标及路径变体不能借用豁免。
- 伪造 `user`、空间、API Key、元素转发头不能改变权限或目标服务；固定路由覆盖服务发现顺序正确。
- 查询编码、加密正文、验证响应原样正确；不会跳转登录或包装平台 JSON。
- 重复消息、超时重投、处理失败、重启及多副本条件下无重复副作用；接纳失败不假报成功。
- 限流、请求体上限、无效大包生效；日志没有凭据、完整回调参数或解密正文。
- 原平台登录、API Key、账号路由、普通元素转发和聊天 SSE 行为回归不变。
- 真实企业域名完成企微验证和消息收发；本地测试成功不能代替此项。

### 上线前待确认

1. Alpha 当前部署的 Gateway SHA、外层 Ingress/代理是否另有认证。
2. 企业域名主体是否满足该账号要求，以及外部前缀实际在哪一层去除。
3. DeepAgent 服务发现名称、API 监听及 Service 端口与本地说明是否一致。
4. 应用可见范围、执行身份、发送接口所需可信 IP 等账号侧条件。
5. 由谁发布并撤销回调配置，哪些环境允许启用。

这些是部署与账号核验项，不改变“源码允许实现”的结论；当前未完成公网实测，不承诺仅改一个白名单即可上线。

## 8. 依据与关联资料

- [Gateway 代码索引](../../../../../deepfos-repos/deepfos_platform/web-gateway_代码索引.md)：本次已回看实际源码，不只依赖索引。
- [课题 22：现有企微 AIBot 与 Channel Gateway](../22-wecom-channel/README.md)。
- [课题 31：凭据加密存储](../31-credential-encryption/README.md)。
- [Spring Cloud Gateway 路由 metadata 官方源码文档](https://github.com/spring-cloud/spring-cloud-gateway/blob/main/docs/modules/ROOT/pages/spring-cloud-gateway-server-webflux/route-metadata-configuration.adoc)：配置建议参考公开机制；实施以仓库锁定的 4.2.3 行为验证，不要求升级 SDK。
- [AstrBot 统一 Webhook 文档](https://docs.astrbot.app/use/unified-webhook.html)：作为配置流程参考，不引入 AstrBot 运行时。
