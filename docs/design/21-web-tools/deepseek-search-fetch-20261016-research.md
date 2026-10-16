# DeepSeek独立Search / Fetch接口与接入建议

> 核验日期：**2026-10-16（Asia/Shanghai）**。
> 状态：前期调研记录已进入实施；2026-10-16已完成独立Search与两家Fetch接入、真实Key调用，见[当前指南](../../guides/web-tools.md)和[本轮验收](web-search-fetch-20261016-validation.md)。下文“建议／未实测”等描述保留调研时的证据边界，不代表最终实施状态。
> 这是时间快照。正式实施前复核价格、优惠、限流与网关支持；未确认具体上线日期，不把“上线前两周”换算成确定截止日。

## 1. 结论

**值得接入。Search优先使用独立API，Fetch作为可选后端验证，不直接替换本地抓取。** 保持模型侧`web_search(query)`、`web_fetch(url)`和现有`builtin:web`授权，不再新增一套模型工具。

官方明确面向Agent/RAG提供两个原始JSON API，使用现有DeepSeek API Key的Bearer鉴权，不需要传`model/messages/tools`：

| **能力** | **接口** | **返回** |
| --- | --- | --- |
| Search | `POST https://api.deepseek.com/v1/tools/search` | 排序后的网页来源、短/长摘要、可选正文及排序/来源信息 |
| Fetch | `POST https://api.deepseek.com/v1/tools/fetch` | `title`及Markdown `content` |

依据：[中文指南](https://api-docs.deepseek.com/zh-cn/guides/search/)、[Search Reference](https://api-docs.deepseek.com/api/search/)、[Fetch Reference](https://api-docs.deepseek.com/api/fetch/)。Context7当前检索仍主要返回旧模型API资料，本轮以实际HTTP 200取得的官方新页面为准。

## 2. 政策：鉴权、价格、优惠和限制

| **项** | **截至核验日的公开规则** | **对DeepAgent的含义** |
| --- | --- | --- |
| 凭据 | 同DeepSeek API的Bearer Key；公开指南未列出额外白名单申请步骤 | 可复用现有DeepSeek Provider Key；文档支持不等于本地/Alpha所选Key、余额或转发网关已通过 |
| 计费 | 中文页Search、Fetch分别**5元/千次成功请求**；英文页分别**$0.75/千次** | 每次人民币0.005元，按各自账户实际币种结算，不把两种报价作为汇率换算；不是按模型Token或结果条数收费 |
| 成功口径 | HTTP200且有结果：Search数组非空，Fetch有正文 | 空结果、失败、超时不收费；仅HTTP200不能一概认定计费成功 |
| 结果量 | `count`不改变单次API价格 | 不意味返回越多越好：正文进入主模型后仍消耗主模型输入Token |
| 上线优惠 | 官方写明Claude Code内置Web Search或Codex `web_search`发起的搜索，在上线前两周免Search请求费，之后按标准价，开始计费另行通知 | 不能推广为DeepAgent独立调用免费，也不能据此认为聊天模型Token免费；本方案按标准费用估算 |
| 限流 | 每账号Search **5 QPS**、Fetch **5 QPS**，相互独立，与模型并发额度独立 | 换同账号Key不能扩容；并发Agent共享额度。Fetch还按目标域名限流，公开页未给出该域名档位 |
| Fetch访问 | 受目标站点可用性、访问策略及DeepSeek安全策略影响 | 不承诺绕过登录、付费墙、验证码或站点拦截；403/404不能都当作“原网页不存在” |

错误处理：400/401/402/403/404等按原义反馈；官方建议408/429/5xx等待后重试，其他4xx不修改请求不要重试。402先处理余额。重试须有总时间和次数预算并响应取消；响应丢失时不能保证供应商没成功、没计费，不称只读重试为“恰好一次”。

来源：[中文计费](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[英文计费](https://api-docs.deepseek.com/quick_start/pricing/)、[完整计费与限流](https://api-docs.deepseek.com/guides/search/)。本轮没有测账号余额或扣费记录。

## 3. 新接口和当前实现的区别

当前`web/deepseek.py`把查询放入Messages提示词，发送`web_search_20250305`工具定义；指定Flash/Pro及`max_uses=5`，再从真实搜索结果块提取来源和citation。产品没有直接把模型生成的回答冒充搜索结果，这条证据校验应保留。

```text
当前：主Agent → web_search → DeepSeek Messages模型 → 服务端搜索 → 模型返回 → 提取搜索结果
建议：主Agent → web_search → DeepSeek Search API → 结构化搜索结果
```

新方案减少的是**我们发起的辅助聊天模型调用**，不是移除主Agent自己的推理。无需再选择Flash/Pro；query、条数和是否含正文直接由程序控制，不再依赖辅助模型是否搜、搜几次及如何组织回答。

从产品逻辑推断，这是在把搜索/网页内容获取作为可独立消费的基础服务开放，利于其他模型、Agent和RAG复用，也便于按请求计费。这是根据接口形态作出的判断；官方没有据此披露内部搜索引擎、索引来源、摘要是否使用LLM或完整排序算法，不能宣称它是自建搜索、完全无模型参与或必然比当前更快更准。

没有找到旧Messages搜索明确下线的公告。建议迁移是基于链路更直接，不是声称旧接口已经不能用。

## 4. 参数与返回适配

### Search

首版建议后台固定以下请求，模型侧仍只传`query`：

```json
{
  "query": "Pydantic AI releases site:github.com after:2026-10-01",
  "count": 5,
  "need_content": false
}
```

| **字段** | **官方约束** | **首版建议** |
| --- | --- | --- |
| `query` | 1～128个Unicode字符，非全空白 | 工具说明/校验体现此上限；过长让模型改写，不静默截断关键词或过滤条件 |
| `count` | 1～50，默认10 | 先保持产品现有5条，后续按质量/Token测试调整 |
| `need_content` | 默认false；true时有机会带回正文，缺失仍可能为空 | 默认false，用长摘要筛选，再显式Fetch重要网页；避免每条都把正文塞回主模型 |
| `ori_question` | 可选，1～128字符，null等于省略 | 首版省略。若后续使用，只取允许外发的原始问题，不发送完整合同、页面上下文或历史 |
| `localization` | 可选IP地址，默认空 | 首版不传，不自动外发用户/服务器IP |

支持`site:`、`-site:`、精确短语、排除词、`lang:`、`after:`、`before:`等query语法；这不等于另外增加了独立`domains/date_range`参数。未知请求字段会被忽略，所以不能把“没报错”当成过滤已生效。

原生结果含`title/url/snippet/summary/content/site_name/site_icon/date/authority/rerank_score`。建议：

- 模型得到标题、URL、日期、长摘要；不同时堆叠同一条的短摘要、长摘要、全文。保留有效空值和实际结果数量，不造日期或正文。
- 来源、排序分数与供应商权威评级可保留在规范化结果/metadata；它们是供应商信号，不是事实准确率或业务置信度，不按分数擅自删除候选。
- `results: []`是成功的无匹配，应明确返回`result_count=0`和无结果说明；避免被当前通用`_tool_return`的空返回校验误报为接口失败。
- 复用当前`provider/query/content/result_count`包装及`sources`元信息、超大结果外置和续读；正文继续标识为不受信任外部内容。

### Fetch

```json
{"url": "https://example.com/article"}
```

URL最多8192字符，必须HTTP(S)、有host且不能带用户名密码。结果只承诺标题和Markdown正文，不据此假设能够执行JS、读取付费内容、识别图片或完整处理所有PDF。

适配后补回请求URL及明确的`provider=deepseek`来源，**不猜测最终重定向URL**，因为当前返回结构没有该字段。完整正文先进入既有ToolOutputLimits/文件外置链路，不为节省Token预先截断丢尾。

## 5. 推荐的产品接入方式

### 第一阶段：独立Search API

**2026-10-16用户确认的简化方向：**保持现有DeepSeek供应商项、关联Provider或独立Key两种凭据来源，不新增工具包、协议模式或模型选择。

- 测试和实际搜索统一使用独立Search API，Console移除Flash/Pro模型选择。
- 历史记录中的`model`在读取时忽略；不为清理字段主动改写数据库，下次保存配置时不再写入该字段。旧客户端仍带`model`时也只作兼容接收，不影响实际调用。
- 不保留旧Messages运行分支。升级会改变现有DeepSeek搜索的请求端点和计费路径，应在交付说明中明确。
- 官方地址可明确映射到`/v1/tools/search`；自定义网关必须实测此端点。不能从“兼容Chat/Messages”推断也支持新工具路径，不猜网关前缀，不静默绕过网关直连官方。
- 新接口失败不自动回退Messages，也不自动换另一家搜索；避免费用和数据外发目的地不明确。

重点改动：`web/deepseek.py`、`web/runtime.py`、`web/settings.py`、`schemas/web_search.py`、`api/v1/global_settings.py`以及Console全局搜索设置。使用现有HTTP客户端即可，**不依赖Pydantic AI先升级到2.57**。

### 第二阶段：Fetch可选后端

在同一Web设置区域提供抓取来源选择，默认继续本地。除DeepSeek外，Kimi、Tavily、Exa、You.com也提供独立正文获取接口，见下节。建议独立保存抓取配置，沿用供应商凭据复用或独立Key的交互，不把“切换搜索供应商”自动等同于“切换抓取供应商”。具体配置Schema及首批接入范围尚未确认，仍可复用现有设置表，不需预设DDL。

保留本地抓取的原因：不额外按次付费；当前已支持有限二进制PDF/图片、Markdown图片引用和公网目标校验。云Fetch可能改善特定站点的可达性和正文提取，但本轮没有对照证据证明全面更好。

注意实际依赖：当前`_probe_image_media_type`会调用本地抓取以识别无扩展名图片。新增云Fetch不能顺带改掉这个二进制探测；`web_image_view`仍走原有安全图片路径。

云Fetch也不能直接宣称等同于当前本地DNS/IP固定的SSRF保护：我们可先拒绝私网/回环/元数据地址及带凭据URL，但无法从只返回`title/content`的协议核验供应商每次重定向和解析的真实IP。应保留公网输入策略，按云服务边界评估，不把私有附件或签名下载地址发送给它。

### 现有搜索供应商的Fetch能力核验（2026-10-16）

以下是官方文档能力核验，**未逐家实测Key权限、余额、抓取质量或延迟**。接口使用各家的平台API Key，不需要指定聊天模型；同一Key的实际权限和计费仍以账户为准。

| 供应商 | 独立网页获取接口 | 正文格式与接入注意 |
| --- | --- | --- |
| DeepSeek | `POST https://api.deepseek.com/v1/tools/fetch` | 标题及Markdown正文；Bearer Key |
| Kimi | `POST https://api.moonshot.cn/v1/tools/fetch` | 标题及Markdown正文；官方明确独立REST，不是通过聊天提示词访问网页；不包含图片、视频等多媒体资源 |
| Tavily | `POST https://api.tavily.com/extract` | Markdown或纯文本；Bearer Key；分别保留成功结果和`failed_results`，获取原文时不要用相关性query把正文缩成片段 |
| Exa | `POST https://api.exa.ai/contents` | `text: true`取得正文，也可请求highlights/summary；原文抓取不以summary替代text；保留逐URL状态与缓存/实时抓取语义 |
| You.com | `POST https://ydc-index.io/v1/contents` | 可选Markdown、HTML和metadata；`X-API-Key`；不能误用Answer接口的`api.you.com`域名 |
| DuckDuckGo | 未确认官方通用URL正文抓取接口 | 当前项目使用的`duckduckgo_search_tool`仅承担搜索，继续保留本地Fetch选项，不虚构同名云Fetch能力 |

官方依据：[Kimi独立接口说明](https://www.kimi.com/academy/best-practices-for-web-search)、[Tavily Extract](https://docs.tavily.com/documentation/api-reference/endpoint/extract)、[Exa Contents](https://exa.ai/docs/reference/get-contents)、[You.com Contents](https://you.com/docs/api-reference/contents)。Context7已取得Exa和You.com资料；Kimi新域名检索未命中、Tavily请求失败，改用官方页面核对。

建议模型侧仍只有`web_fetch(url)`，不增加各家专用工具或供应商参数。返回统一保留请求URL、标题、正文及供应商/完整性状态；本地PDF、图片和媒体类型探测继续保留。云端抓取失败应明确报告，不默默跨供应商发起额外收费请求。

## 6. 实施前后的验证清单

1. **连通与费用**：同一个现有Key对两个官方端点做少量公开问题/网页调用，单列本地与Alpha网络；确认状态、来源、实际账单和网关路径。文档可访问不是API已通过。
2. **公平Search对照**：固定中文新闻、官方技术文档、时效/站点/语言过滤、精确实体、无结果与长查询；比较旧Messages和新API的独立标注Top5质量、延迟、请求数、摘要可核实性及主模型Token。没有测前不承诺速度倍数。
3. **Fetch对照**：官方文档、文章、表格/链接/图片引用、长页面、重定向、JS页及被拦站点；对照标题、关键数字、尾部完整性、本地/供应商错误语义。PDF/图片仍验证原有本地行为。
4. **错误和限流**：空结果、400/401/402/403/404/429/504、非JSON、超大结果、超时和取消；按账号合并调度，429有界退避。多Pod不能只按单Run/单Key宣称已满足账号QPS。
5. **真实Agent**：用同一个非DeepSeek主模型完成Search→Fetch→引用回答，验证工具定义稳定、SSE/History来源与耗时、超大结果续读、失败后正常解释；确认未额外发送辅助聊天请求。

对应现有回归：`test_deepseek_web_search.py`、`test_web_tools.py`、`test_web_search_settings.py`、`test_web_search_probe.py`、`test_web_search_failure_events.py`、`test_web_fetch_overflow.py`及Console设置用例。需要新增新协议夹具，不直接把旧协议测试改成通过。

**最终实施范围：Search已去模型选择并迁移独立API；Fetch仅支持DeepSeek／Kimi跟随，其余搜索服务商仅系统内置。最新实测发现Kimi Fetch正文为`markdown`，可包含图片链接占位；不包含多媒体字节不能解释为完全没有图片链接。**
