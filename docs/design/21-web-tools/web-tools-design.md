# DeepAgent Web 工具设计

## 产品与授权

新增默认不关联的内置工具包：

```text
builtin:web
├── web_search(query)
├── web_fetch(url)
└── web_image_view(url, force_download=false)  # 仅图片输入模型
```

Agent只有显式关联`builtin:web`后才获得网络工具。Search的全局设置只决定它如何执行，不能替代
Agent授权；Fetch无配置项。Internal HTTP和MCP继续是独立边界，稳定业务系统优先建设MCP。

## 数据

当前Alembic `001`生产基线包含通用、版本化配置表：

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

### Web Fetch与图片读取

复用Pydantic AI 2.35.1本地WebFetch和SSRF-safe downloader：仅HTTP(S)，DNS解析后连接固定IP，
每次重定向重新解析校验，禁止回环、私网、链路本地与云元数据；DeepAgent额外禁止URL内嵌凭据，
不添加Cookie、Authorization或平台Header。下载上限5 MiB，文本上限50K字符，超时30秒；二进制
只接受PDF和常见图片。直接抓取二进制图片时继续返回Pydantic AI原生`BinaryContent`，并继续受现有
ToolOutputLimits规则约束。

Pydantic AI 2.35.1的WebFetch把HTML交给`markdownify`时固定跳过`img/script/style`，没有公开
`strip`配置。DeepAgent不修改`site-packages`，只在本仓保留一层HTML转换薄适配：继续复用上游
`safe_download`、SSRF、重定向复检、内容类型和长度限制；显式丢弃`script/style`正文，把`img`
保留为原位置的`![alt](绝对HTTP(S) URL)`。相对URL和协议相对URL按最终页面地址规范化，
`javascript:`、`data:`和带凭据地址不进入Markdown。

`web_image_view`是图片输入模型的外层普通工具。它先执行公网URL/SSRF校验，再返回原生
`ImageUrl`：`force_download=false`允许Provider直接消费URL，Provider不支持时由Pydantic AI适配；
`force_download=true`要求Pydantic AI在组装下一次模型请求时安全下载并转成Provider视觉参数。
工具结果本身不含图片字节。无扩展名URL用既有有界WebFetch路径探测MIME；非图片、私网、回环、
云元数据和凭据URL明确拒绝。

系统不自动发送页面前三张或任意预选图片。模型先从Markdown中的位置、alt与正文关系判断需要查看
哪些图片，再调用一次或并行调用多个`web_image_view`。

部署时应为Web Fetch提供可控的公网出口。无代理时直接沿用Pydantic AI的系统DNS与固定IP连接；
显式HTTP CONNECT代理下，通过公共DNS获得待校验和固定的公网IP，并把原始主机名传递给TLS SNI，
兼容Clash类fake-IP环境。兼容钩子在进程内保持安装，但只在存在显式HTTP隧道代理时生效，因此既覆盖
`web_fetch`执行期，也覆盖工具返回后Pydantic AI为`ImageUrl(force_download=true)`组装下一次模型请求
时发生的延迟下载。公共解析不可用时回退原解析并继续执行全部SSRF校验；不得改回普通URL直连、
关闭证书校验或绕过重定向复检。

## Code Mode、安全与审计

固定Provider模式下，`web_search`和`web_fetch`标记为Hybrid函数；只有Agent同时关联
`builtin:web`与`builtin:code-mode`时才可由Monty编排。当前模型原生搜索仍由Provider执行。
`web_image_view`不标记Hybrid，不进入Code Mode/Monty，只保留为视觉模型可直接调用的外层工具。
Monty不获得Socket、Shell、环境变量或任意Header；`execute_python`也不开放联网。

普通Function Tool沿用现有Tool Call/Return SSE、History和Event；Provider原生搜索沿用Pydantic AI
Native Tool Search Part。工具结果Metadata携带执行模式、Provider与来源；Event Store不保存API Key、
Header、Cookie或原始鉴权数据。抓取和搜索正文一律视为不可信数据，不能覆盖系统指令。

当前`agent.run()`的`web_image_view`结果保持`ImageUrl`，供框架组装紧随其后的模型视觉请求；Event
Store只保存URL、MIME、`force_download`和SSRF审计，不保存Base64或文件。下一次独立Run加载历史时，
旧图片结果投影为轻量URL说明，避免每轮重复发送图片。SSE、Conversation History和轨迹使用同一
安全引用；Console仅按URL懒加载无Referrer预览，失败时保留原图链接，不进入平台附件存储。

## 错误语义

| 情况 | 结果 |
| --- | --- |
| Agent未关联`builtin:web` | 完全不注入Search/Fetch |
| 当前模型不支持原生搜索 | 配置错误，明确提示更换模型或搜索方式 |
| 固定Provider缺Key/模型 | 保存或运行前校验失败 |
| Provider鉴权/限流/超时/空结果 | 对应脱敏`ToolFailed`，不降级 |
| Fetch非法协议、凭据或私网 | `ModelRetry`拒绝该URL |
| Fetch网络/上游/响应类型/大小失败 | 脱敏`ToolFailed`，允许模型解释失败，不返回部分未验证内容 |
| 文本模型调用图片 | 不注入`web_image_view`，不存在伪成功 |
| Image View非图片、凭据或私网 | `ModelRetry`拒绝该URL，不产生视觉Part |
| 历史图片结果 | 只恢复轻量URL说明，需要重看时重新调用工具 |

## 测试矩阵

- 001空库、旧012结构安全改标、SQLite/MySQL物理结构校验、配置备份恢复。
- 默认native、六Provider字段校验、Key掩码/保留/切换清理、DeepSeek/Kimi会话Provider引用、草稿连通性测试。
- 未授权无工具、native适配/不支持错误、固定Provider无降级、Search/Fetch/Image Schema。
- Fetch协议、凭据、回环、私网、元数据、DNS与重定向复检、类型/大小/超时配置；HTML图片位置、
  alt、相对/协议相对URL和脚本/样式过滤。
- Image View两种传递模式、无扩展名、非图片、凭据/私网；仅视觉模型注入且不进入Code Mode。
- ImageUrl不触发Artifact，Event/History/SSE/轨迹无Base64、后续Run不重复图片；Console懒加载与失败回退。
- Console两模式、六Provider条件字段、保存/取消/测试、运行设置与窄屏。
- DeepSeek native与固定Provider、DuckDuckGo、公开Fetch真实测试；无Key供应商走Mock全链路。
