# DeepAgent MCP 工具检索与渐进披露设计

> 实施版本：v0.3.12；v0.3.14完成真实联调边界修复；Pydantic AI 2.26；本课题不新增迁移，当前仓库head为005。

## 1. 稳定边界

- Agent 仍通过 `capabilities` 整包选择 ToolPackage；检索不能扩大授权范围。
- ToolPackage、MCP Server、最终带前缀工具协议名、Header 和实际 Toolset 执行语义不变。
- 第一版不部署 Embedding、不调用额外模型选工具、不持久化索引，也不要求第三方 MCP 提供别名。
- 静态内置工具、Sandbox、UI Tool、Skill 和 MCP Resource 宿主工具继续直接披露。
- Output Tool 不属于本课题。

## 2. 运行链路

```text
Agent.capabilities
→ 解析已选择 ToolPackage
→ 当前身份、App、Space、enabled 过滤
→ 直接工具与 MCP Toolset
→ MCP 运行时 CombinedToolset
→ DeferredLoadingToolset
→ PreparedToolset 首轮预选
→ Pydantic AI ToolSearch
→ Agent Run
```

多个已授权 MCP 只在一次 Run 内临时组合，以得到全局 Top 3；这不是新的产品工具包，也不写数据库。

### 2.1 小型 MCP

候选工具不超过 3 个时全部直接披露，不向模型增加 `search_tools`。

### 2.2 大型 MCP

候选工具超过 3 个时：

- 当前问题正相关工具最多 3 个以完整 Schema 直接披露。
- 无正相关结果时允许 0 个，不用任意写入工具凑满 Top 3。
- 其他工具保持 `defer_loading=True`。
- 模型看到 `search_tools`，一次主动搜索最多返回 5 个工具。
- 命中后由 Pydantic AI 在下一次模型请求中加入原始完整 Schema，并仍由原 MCP Toolset 执行。

### 2.3 名称目录

动态 Instructions 只列延迟工具的稳定协议名，并按 MCP Server 分组；不重复已直接披露或已发现工具，
也不把目录塞进 `search_tools` 的 Tool Description。例如：

```text
以下 MCP 工具当前仅提供名称；需要完整参数时调用 search_tools：
- deepfos: deepfos_list_cube_dimensions, deepfos_resolve_members
- context7: context7_query_docs
```

## 3. 通用词法检索

v0.3.19 起，URL MCP 在管理员“检测并更新”时执行 `tools/list`，把不含 Header、Token、Cookie
和 MCP `_meta` 的定义保存到 `mcp_servers.inspection`。每次 Run 直接复用该快照构造纯内存文档：

```text
MCP Server 名称
最终带前缀协议名
工具描述
顶层参数名称
顶层参数描述
```

不使用管理员自定义别名、业务标签或历史聊天生成标签，因此普通第三方 MCP 可以直接接入。检索文档
仍按 Run 构造并释放；数据库只保存检测事实，不增加向量库或跨 Worker 检索索引。

当前 `lexical_v2`（v0.4.2）：

1. 独立模块把文档字段分为 `code/alias/attribute/text`，当前 Tool Search分别映射最终协议名与
   去Server前缀名称、MCP标准 `title`、顶层参数名、工具与参数描述；该内核以后可以复用，但
   v0.4.1只有 Tool Search接入。
2. Unicode NFKC、大小写、空白、Snake Case、Kebab Case、Camel Case及紧凑编码归一化。
3. 分级排序依次处理 Code、Alias、Attribute精确匹配，忽略连接符后的紧凑匹配，Code/Alias
   前缀与包含、Attribute包含、Text字段BM25，最后才做受限编辑距离纠错。
4. MCP标准 `title`含中文时支持全拼、首字母和拼音前缀；拼音只是汉字转写，不承担中英文翻译。
5. 查询、写入、删除、刷新、生成、上传等通用动作归一化；否定式“不要创建/刷新”不会被
   误判为写入意图。
6. 原始用户问题表达只读或明确禁止创建、上传、刷新等副作用时，该约束同时作用于首轮自动
   预选和本轮后续 `search_tools` 结果；模型自行改写搜索词不能绕过本轮约束。
7. 同分按协议名排序，保证不同 Worker 结果一致。
8. 查询中明确包含完整工具名或去掉 MCP Server 前缀后的协议名时，精确标识符优先并只返回
   明确命中的工具，不再用相邻词补齐 Top 5。
9. 不内置业务名词或中英文翻译词典。中文问题只有在Schema含中文、标准 `title`可做拼音转写，
   或模型直接使用名称目录中的协议名时才能命中英文工具；否则明确视为词法召回不足。
10. 模型主动搜索时一次最多使用3个互补查询：用户原始语言、简洁英文能力描述，以及可选的目录
    协议名。后端将它们作为同一次确定性词法检索输入并合并结果；协议名精确命中仍优先且不会补齐
    无关Top 5。这不影响首轮自动Top 3，也不等同于后端翻译。

自动预选优先使用聊天 API 收到的原始非空 `message`，并在它被投影为
`<context_references>...<user_message>...` 模型输入之前单独传入检索上下文；只有原始用户消息为空时，才使用结构化
`context_refs` 的 `type` 和 `label` 回退。非空用户问题即使没有正相关结果，也不使用页面名、
输入表等标签替代用户意图，允许首轮不预选并由模型调用 `search_tools`。
Deferred 恢复时回退有效历史中的最近用户任务。检索不使用 `payload`、`metadata`、附件正文
或工具结果。

模型主动搜索直接使用 `search_tools.queries`，与首轮预选复用同一检索器。工具说明要求模型在一次
调用中提供2～3个互补查询，避免先用中文失败、再多花一轮模型请求改成英文；运行时只采用前三条。

## 4. 文件条件过滤

v0.3.23 的系统工具 `read_file` 不进入 MCP语义检索：

- 当前请求和原始会话没有可读附件、Agent也没有可读Skill文件时，`PreparedToolset`隐藏该工具。
- 存在任一合法来源时直接披露同一个 `read_file`，由判别式 `resource`参数选择附件或Skill。
- 附件的会话归属、Skill的Agent选择与精确资源索引、MIME和大小仍在调用时校验。
- Checkpoint摘要不能作为附件存在、Skill授权或文件权限依据。

## 5. 发现状态与历史

- Tool Search Call/Result 使用 Pydantic AI 类型化消息并随原始历史保存。
- 同一 Run 中，已发现工具在后续模型步骤继续可见。
- 模型主动搜索排除已预选、已发现和同一 Run 已返回的工具；相同规范化查询在同一 Run 中只
  执行一次，避免重复请求消耗。模型修改查询词后仍可继续搜索。
- 新 Run 仍按新问题增加首轮 Top 3；Pydantic AI 可从有效历史恢复已发现工具。
- 如果压缩移除旧搜索交换，工具重新回到延迟状态，可再次搜索。
- 不增加自定义过期或清空状态；跨 Run 的已发现工具仍完全遵循 Pydantic AI 2.26 历史恢复语义。

历史与压缩的安全边界同时识别 Local 和 Provider Native Tool Search Call/Return；正式取消产生的
`ModelResponse.state="interrupted"` 不被误判为 Pending Deferred。Provider `CompactionPart` 如果仅在
`provider_details` 中保留不透明载荷，Checkpoint 保存会失败关闭，避免持久化不可恢复的空壳。当前仍不
启用 Provider Native Compaction，单次摘要算法不变。

## 6. API、Console 与观测

`GET /api/v1/tools` 增加只读字段：

```json
{
  "disclosure_mode": "direct | conditional | search",
  "disclosure_condition": "session_has_attachment | null",
  "preselect_limit": 3,
  "search_result_limit": 5
}
```

MCP 配置页继续使用检测快照中的工具数量，不为展示额外连接远程 MCP。快照同时保存原始名称、说明、
输入/返回 Schema、Annotations、Task Support 和 Server Instructions；公开列表只返回状态、时间、Hash
和数量，不返回完整 Schema。Agent 仍整包选择。

Console 展示“始终可用”“有附件时可用”“自动预选 Top 3 / 搜索 Top 5”。`search_tools` 在聊天中显示
为默认折叠的“正在查找可用工具”，不是业务 MCP 卡片。

`final.context.tool_discovery` 返回本次 Run 的候选数、直接/延迟数、预选和发现工具、搜索次数、全量与
实际披露 Schema Token 估算、节省量和检索器版本。日志只记录目录 Hash、数量、协议名、耗时和估算，
不记录用户问题、Schema、参数、Token、Cookie 或 Header。

## 7. 故障与安全

- 检索输入只来自当前已授权候选集，未选择 MCP 不会进入目录或结果。
- 未发现工具即使被模型猜中协议名也不能绕过 Deferred Loading 执行。
- Retriever 异常时恢复当前已授权 MCP 全量披露并记录诊断；不静默丢工具。
- 全量披露本身超窗时沿用现有明确超窗错误。
- 工具执行继续通过原 Toolset 的权限、Header、连接和错误处理。
- URL MCP 只在首次真实工具调用时 initialize 并按当前请求注入 Header，同一 Run 内复用连接；初始化、
  网络或 503 失败转换为脱敏 `ToolFailed`，本 Run 不重复等待。取消继续向外传播，不能被转成工具失败。
- 历史 MCP 只有数量而没有定义快照时不进入工具目录；配置 URL/Header/请求上下文后旧快照立即失效，
  重新检测成功前保持隐藏。检测同一配置失败时可以沿用上次成功快照。
- 工具检索不替代高风险审批。

## 8. 评估与后续

脱敏 Shadow 评估只存在于测试目录：输入问题、授权目录、必需/可选/禁止工具，输出 Top 3/Top 5
Recall、误选与 Schema Token 降幅。它不进入生产启动、不记录在线流量、不自动学习，也不改算法。

首版门槛：Required Recall@5 为 100%，Recall@3 不低于 90%，未授权召回和只读问题高风险误选为 0，
24 工具目录首轮 Schema Token 至少下降 70%。

后续仅在真实数据证明词法召回不足时评估：多语言 Embedding、Provider Native Server Search、超大目录
缓存和发现状态过期。它们不是 v0.3.12 的运行依赖。
