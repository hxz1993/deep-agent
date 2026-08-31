# DeepAgent Web 工具设计

## 产品与授权

新增默认不关联的内置工具包：

```text
builtin:web
├── web_search(query)
└── web_fetch(url)
```

Agent只有显式关联`builtin:web`后才获得网络工具。Search的全局设置只决定它如何执行，不能替代
Agent授权；Fetch无配置项。Internal HTTP和MCP继续是独立边界，稳定业务系统优先建设MCP。

## 数据

Alembic `012`增加通用、版本化配置表：

```text
global_settings(key PK, value TEXT JSON, version SMALLINT, created_at, updated_at)
```

唯一初始化项是`web_search`、结构版本1、`{"mode":"native"}`。不建Provider子表，也不为六个
供应商生成多行。配置备份包含该表；会话表不受影响。

配置值只有两种形态：

```json
{"mode":"native"}
```

```json
{"mode":"provider","provider":"deepseek","credential_source":"api_key","api_key":"...","model":"deepseek-v4-flash"}
```

```json
{"mode":"provider","provider":"deepseek","credential_source":"llm_provider","llm_provider_id":"...","model":"deepseek-v4-flash"}
```

`model`只用于DeepSeek/Kimi；这两类模型供应商可选择复用一个同类型会话Provider的凭据，或单独
配置搜索API Key。复用时JSON只保存`llm_provider_id`，运行时从受信数据库记录读取Key，不复制密钥。
DuckDuckGo没有Key；其他固定Provider只有Key。切换Provider时不复用旧Provider密钥。读接口只返回
`api_key_set`与掩码；同Provider未重填独立Key时保留原值。

## 管理接口与Console

- `GET /api/v1/global-settings/web-search`
- `PUT /api/v1/global-settings/web-search`
- `POST /api/v1/global-settings/web-search/test`

均沿用Console企业管理员门禁。测试接口直接测试未保存草稿，只返回成功或脱敏短错误，不保存配置，
也不返回结果正文。

Console路径为“配置中心 → 全局配置”。同一页按上下顺序展示“Web Search”和“运行设置”；后者
继续展示既有环境变量事实，不显示只读标签。Web Search平时只回显当前方式，点击编辑后选择“当前
模型”或“指定Provider”。Provider与模型同排，凭据位于下一排；所有选择器使用Console统一的
自定义下拉。DeepSeek/Kimi额外显示“沿用会话Provider / 单独配置API Key”二选一。

## 运行路由

### 当前模型

Pydantic AI `WebSearch(native=True, local=False)`进入本次请求的Capabilities；模型装配在请求级
切换到对应原生适配：DeepSeek/OpenAI使用Responses，Anthropic/Google保持原生模型，OpenRouter
使用其模型适配。Moonshot/Kimi、Alibaba、Z.AI和普通OpenAI-Compatible没有本仓原生适配时明确
失败，不切DuckDuckGo。

### 指定Provider

模型始终看到`web_search(query: string)`。服务端按唯一配置精确路由：

- DuckDuckGo、Tavily：Pydantic AI Common Tools，普通结果上限5、30秒。
- Exa、You.com：Harness工具，普通结果上限5、30秒。
- DeepSeek：Responses原生WebSearch包成辅助搜索，120秒。
- Kimi：官方`$web_search`工具循环包成辅助搜索，最多4轮、120秒。

输出归一化为Provider、Query、内容、结果数、来源URL和“不可信外部内容”标记。鉴权、限流、超时、
空结果和Provider失败各自关闭，不执行隐式降级。

### Web Fetch

复用Pydantic AI 2.35.1本地WebFetch和SSRF-safe downloader：仅HTTP(S)，DNS解析后连接固定IP，
每次重定向重新解析校验，禁止回环、私网、链路本地与云元数据；DeepAgent额外禁止URL内嵌凭据，
不添加Cookie、Authorization或平台Header。下载上限5 MiB，文本上限50K字符，超时30秒；二进制
只接受PDF和常见图片。

部署时应为Web Fetch提供可控的公网直连出口。Pydantic的安全下载器会先解析域名并固定目标IP，
同时底层HTTP客户端可能继承系统代理；如果代理不支持这种固定IP加原始SNI的TLS连接，会出现证书
校验失败。此时应在DeepAgent进程级配置精确的`NO_PROXY`或调整出口代理，不得改回普通URL直连、
关闭证书校验或绕过SSRF复检。

## Code Mode、安全与审计

固定Provider模式下，`web_search`和`web_fetch`标记为Hybrid函数；只有Agent同时关联
`builtin:web`与`builtin:code-mode`时才可由Monty编排。当前模型原生搜索仍由Provider执行。
Monty不获得Socket、Shell、环境变量或任意Header；`execute_python`也不开放联网。

普通Function Tool沿用现有Tool Call/Return SSE、History和Event；Provider原生搜索沿用Pydantic AI
Native Tool Search Part。工具结果Metadata携带执行模式、Provider与来源；Event Store不保存API Key、
Header、Cookie或原始鉴权数据。抓取和搜索正文一律视为不可信数据，不能覆盖系统指令。

## 错误语义

| 情况 | 结果 |
| --- | --- |
| Agent未关联`builtin:web` | 完全不注入Search/Fetch |
| 当前模型不支持原生搜索 | 配置错误，明确提示更换模型或搜索方式 |
| 固定Provider缺Key/模型 | 保存或运行前校验失败 |
| Provider鉴权/限流/超时/空结果 | 对应脱敏`ToolFailed`，不降级 |
| Fetch非法协议、凭据或私网 | `ModelRetry`拒绝该URL |
| Fetch上游/响应类型/大小失败 | 有界失败结果，不返回部分未验证内容 |

## 测试矩阵

- 012空库、011→012→011、SQLite及MySQL/PostgreSQL DDL、配置备份恢复。
- 默认native、六Provider字段校验、Key掩码/保留/切换清理、DeepSeek/Kimi会话Provider引用、草稿连通性测试。
- 未授权无工具、native适配/不支持错误、固定Provider无降级、Search/Fetch Schema。
- Fetch协议、凭据、回环、私网、元数据、DNS与重定向复检、类型/大小/超时配置。
- Console两模式、六Provider条件字段、保存/取消/测试、运行设置与窄屏。
- DeepSeek native与固定Provider、DuckDuckGo、公开Fetch真实测试；无Key供应商走Mock全链路。
