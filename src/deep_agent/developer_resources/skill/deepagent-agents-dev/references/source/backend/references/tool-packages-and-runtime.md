# 工具包与运行时组装

## 目录

- 产品包与运行时 Toolset
- 当前包类别
- Agent 选择语义
- MCP 和 UI 动态包
- 附件、Sandbox、Skills
- 安全扩展规则

## 产品包与运行时 Toolset

DeepAgent `ToolPackage` 是 `/api/v1/tools` 和 Console 展示的产品/配置记录。它封装恰好一个 Pydantic AI Toolset 及其元数据：

```text
code, category, label, source, description
catalog status
stable tool directory
exactly one toolset
```

Toolset 是 Pydantic AI 准备并发送给模型的对象。类别是字段，不是独立的运行时对象。

不要将其与 Pydantic AI `AbstractCapability` 混淆，后者是面向模型请求、历史和事件的生命周期扩展。

## 当前包类别

```text
builtin:document     document parsing tools
builtin:sandbox      FunctionToolset[execute_python]
builtin:web          FunctionToolset[web_search, web_fetch]
builtin:ptc          FunctionToolset[load_file_data] + Hybrid CodeMode[run_code]
mcp:<name>           one MCP Server / MCPToolset
ui:builtin           one ExternalToolset[builtin__confirm, builtin__choice]
ui:<manual-package>  one ExternalToolset of valid package members
```

运行时系统工具不是 ToolPackage，也不存储在 `agents.capabilities` 中：

```text
runtime:skill             load_capability, list_skill_resources, conditional execute_skill_script
runtime:environment       get_current_time(timezone="UTC"), all business Agents
runtime:file              read_file, view_image, file publishing tools under their authorization rules
runtime:planning          planning tools
runtime:conversation-history  conversation lookup tools
runtime:tool-result       bounded tool-result reading
runtime:channel           channel message tools under channel context
runtime:mcp-tool-search   conditional search_tools
runtime:mcp-resource      conditional MCP Resource list/read tools
```

Skill 也不是 ToolPackage。Agent 通过 `agents.skills` 单独选择 Skill；Harness `Skills` 渐进加载 `SKILL.md`，而 DeepAgent 负责在线安装、授权、本地运行时文件和资源。

面向模型的稳定工具名独立于 Console 标签。不要仅为美化 UI 而使用重命名包装。

## Agent 选择语义

`agents.capabilities` 和 `agents.skills` 是显式的非 null 列表。新 Agent 的这两个字段都以 `[]` 开始；不会通过目录级默认值或启用标记继承任何资源。当当前 API 承诺进行校验时，创建/更新 Agent 必须拒绝未知、空或不可运行的包代码。历史遗留的失效引用应在运行时安全失败，并在后续规范化保存时移除，而不是导致所有 Agent 崩溃。

Provider 没有启停标记；模型保留独立的 `enabled`。普通 `/llm-models` 列表返回当前可选模型，管理页面可请求 `selectable_only=false` 查看不可用原因。Skill/MCP/UI 工具等资源不重新引入启停字段。Agent 默认 `model_id` 可空，运行需显式请求模型或 Agent 模型。

Agent 用 capabilities 选择整个 UI 包，启用后使用该包全部工具，不按 `ui_tool_selection` 过滤。历史数据库字段保留结构兼容但忽略明细，保存时清空；旧安装包忽略该字段，导出不携带明细，安装与对比只认包级关联。不要恢复逐工具挑选。

## 运行时组装

静态注册表负责文档解析、Sandbox、Web 和 PTC 包。时间工具默认进入所有业务 Agent，不依赖 capabilities；历史 builtin:general 在能力编码规范化时移除，安装旧包仍可使用时间工具，新导出不补旧引用。请求时的组装会添加数据库中配置的/内置的 MCP、实际生效的 UI 包、选中的 Skills，以及按条件加入的系统工具。Console 可合并展示分组，展示分组不改变装配、授权或 PTC 白名单。

```text
AgentSpec.capabilities
  -> ToolPackageRegistry.toolsets() for static packages
  -> resolve_mcp_toolsets() for selected MCP packages
  -> prepare_ui_toolset_for_spec() for selected whole UI packages
AgentSpec.skills
  -> associated/runtime-ready Skill directories
  -> Harness Skills(include=...)
Request/session state
  -> conditional system Toolsets
  -> pydantic_ai.Agent(toolsets=[...])
```

保持包与 Toolset 一一对应，不要把一个 UI 包拆成多个运行时 Toolset。标题生成、压缩、Provider 测试等内部辅助请求不因此获得时间工具；时间工具不进入 PTC。

## MCP 包

- 一个服务名称同时确定包标识 `mcp:<name>` 和模型工具前缀 `<name>_`。
- URL/内置 MCP 的运行时 Toolset 可以包含 Server Instructions。Resources/Prompts 不会自动成为 Function Tools。
- `/tools` 可以报告检查数量/状态，而不缓存完整 MCP schema。
- 远程检查应先完成，再将配置和快照在同一事务中提交。
- 通过 `/api/v1/mcp/{name}/rename` 重命名 URL MCP：原子更新 `mcp_servers.name` 和 Agent 的包引用。历史工具调用保留旧协议名；正在运行的 Run 保留已经构建的 Toolset。
- 内置项不能通过普通 API 重命名或删除。旧版 stdio 不能新建、编辑或运行。
- 资源代理和重连逻辑必须保留当前会话、用户和请求上下文，并保持错误隔离。

## UI 包

- `ui:builtin` 由代码维护，不存储在 `ui_tool_packages` 中。
- 手动包和成员分开存储；每个工具都必须具有 `package_code`。
- 空包可以作为配置草稿存在，但不能被选择或运行。
- 包内有工具或被 Agent 引用时，阻止删除该包。
- 按当前产品规则，当工具所属的包被引用时，阻止删除该工具。

## 附件

- 上传只存储引用，不会立即提取 Office 文本。
- `read_file` 是按条件加入的系统工具，不是选中的能力。当当前会话或原始会话具有可读附件，或 Agent 选择了可读的 Skill 资源时，它才会出现。
- 从完整的原始 Message Event、当前轮附件及可信工具上传产物构建允许访问的 `file_id` 集合，绝不能仅从最新 Compaction 锚点/证据构建，也不能因为任意 MCP JSON 声称有 File ID 就授予权限。
- 使用从 1 开始的 `offset/limit`、`has_more` 和 `next_offset`。附件和 Skill 来源共享文本/DOCX/XLSX 解析，以及一个 Run 范围内用于合并重复请求的 single-flight 缓存，因此分页不会重复下载或解析同一文件。
- read_file 读取普通文本（包括 Python、SQL、JS 等代码）、DOCX、XLSX、PPTX 文本／表格／备注及 PDF 文本层；PDF 保留物理页，不执行 OCR，不支持旧 DOC/XLS/PPT。读取代码不需要执行权限，仍必须有附件或 Skill 资源授权。
- `view_image(items=[...])` 为当前主视觉模型准备 PNG/JPEG/WebP/GIF 或 PDF 指定页，支持授权附件、已选 Skill 图片/PDF，以及有 Web 授权的公网 URL。图片/PDF 原页视觉核验与文字提取、专业解析按任务选择；模糊字段标记不确定。它替代旧 web_image_view 的新调用入口，保留旧历史读取，不进入 PTC。
- 当前 Run 的连续查看图片累积给模型，持久历史只存轻量来源；下一用户轮需要原图应重新查看。用户直接上传图片沿用已有历史加载行为。图片就绪不等于模型已理解内容；原文件与临时页面截图不混作正式上传产物。

## Sandbox 与 Skills

- 将 `execute_python` 保留在独立的 `builtin:sandbox` 包中；Agent 必须显式关联该包。
- 将 run_code 保留在独立的 builtin:ptc 包中，兼容旧 builtin:code-mode 别名；Agent 必须显式关联。PTC 可调用已授权 MCP，以及当前已授权的 execute_python / execute_skill_script；load_file_data 仅在代码内使用。search_tools 保持外层原生调用，不扩大工具授权。
- 完整PTC使用规则与既有示例由ptc/toolset.py中的run_code说明拥有；search_tools只加“发现后可原生调用、发现不要求PTC”的短提示，新通知仅列新增名称，保留发现去重。不要把删重复说明扩大为删除历史通知、计划或其它上下文。
- 原生调用优先；只对无需模型理解的有界处理中间数据或不透明文件转送使用PTC。保留禁止探测／猜别名、授权边界、禁止盲重试、顺序写入、失败不等于回滚、文件载荷保密及禁宿主访问约束；规则存在不等于真实模型每次均遵守，应按真实调用记录验收。
- Code Mode 目录别名是仅在 `run_code` 内可用的 Python SDK 函数；直接从外层调用工具，需要单独暴露的原生 Tool Schema。仅为现有证据白名单保留带类型的嵌套返回值；剥离不可信的 MCP 返回 schema，使 Monty 收到规范的通用 JSON，而不是猜测出的结构。
- load_file_data不是普通模型工具。仅在run_code内接受授权会话file_id或经现有安全边界读取的公开HTTP(S) URL，二者选一；不是任意网络能力。Base64模式返回Run内不透明token，仅在MCP调用边界解析。禁止宿主路径、任意文件ID、Shell和宽泛内部工具访问，载荷值不检查、打印或返回。
- 仅在外层 `run_code` 结果上保留嵌套调用审计，同时执行逐值脱敏和总大小限制；不要为每个嵌套 MCP 调用创建一个 Session Event 或 Conversation Item。
- 本地 `subprocess-dev` 是开发后端，不是生产环境的强隔离机制。
- 内置 Skills 位于 `deep_agent/builtin_skills/`。上传的 Skills 是数据库管理的在线包，原始 ZIP 由平台文件服务保存；每个 Pod 仅保留一份当前的 `runtime-skills/<code>/` 副本。
- 当前已安装Harness的`Skills` 负责 `load_capability` 的渐进披露。DeepAgent 负责 Agent 选择、包检查、平台文件恢复、资源授权、诊断和 Console CRUD。
- 同哈希替换仅在平台原始文件可下载后才返回 `noop`。若源文件缺失，则重新上传、更新 File ID、恢复当前运行时目录，API 返回 `repaired`。
- Skill 脚本会被索引并可通过 read_file 读取；load_capability 提供说明与资源目录，代码按需读取，不自动把整包源码塞入上下文。当 Agent 选择了带脚本的 Skill 且关联了 builtin:sandbox 时才披露 execute_skill_script；与 execute_python 使用同一 Python 执行后端和文件授权/上传流程。
- Skill 保留入口原路径及同包 Python 模块，工作目录是独立 execution-root，脚本使用 __file__ 定位 skill 内资源。非代码资源通过 resource_paths 明确携带。Skill 以 inputs.json / outputs/result.json 交换 JSON；普通 Python 沿用 inputs 变量和 result 结果约定，两者不强行统一。
- 两个执行工具均支持 include_context=false。显式开启才注入 DEEP_AGENT_CONTEXT JSON，同 Run 共用资料查询缓存；PTC 调相同入口。它含非凭据的当前用户资料和 app/space/language，不含 Cookie、Token、Key，也不构成客户接口鉴权。具体字段及失败降级见 [脚本与上下文](../../../04-skill-python-files.md)。
- 输入文件用 deepagent_files.input_path，产物用 publish_path 显式登记才上传；返回顶层 files 的真实 File ID。PTC 汇总可信上传产物，外层可继续 read_file / view_image。未登记文件不会自动上传，后续步骤失败不抹掉此前已成功上传的文件事实。

### 电子表格助手应用专用 Skill

- 应用配置归 `deeptable-assistant` 集成。管理员用 `/api/v1/integrations/deeptable-assistant/app-configs/{app_id}/skills` 管理专用包，路径里的 Skill 编码使用 ZIP 原编码。通用 Skill 和不同应用可以同名，同一应用内重复安装返回 409。
- 每轮先核对会话的 App/Space、调用者访问权与集成关联 Agent，再追加对应提示词和专用包。模型通过目录给出的 `app--<App ID>--<包编码>` 加载说明，通过 `app:<App ID>:<包编码>` 读取资源或执行脚本。两种编码都与数据库存储键分开。
- 管理、运行、配置备份都以 `scope_ref` 判断归属。新旧存储键共存时保留旧行和缓存；普通 Agent ZIP 只含通用 Skill。

## 安全扩展规则

1. 仅当一个包代码代表一个内聚的产品选择单元时，才添加该代码。
2. 恰好包装一个 Toolset，并暴露稳定的模型工具名。
3. 展示元数据与协议名分开添加。
4. 测试显式列表/空 Agent 语义，以及精确的最终模型工具定义。
5. 测试外部工具的 SSE/历史/Deferred 行为。
6. 在上下文预算推理中计入 Tool Definition Schema 的 Token 成本。
7. 使用 `PrepareTools`/过滤后的 Toolset 控制请求级可见性；不要按请求修改全局注册表。
8. 原生Output Tool使用Agent独立output_config，由每个Run读取当前配置并在内存校验，保持在ToolPackage和UI Deferred之外；详见[结构化输出](structured-output.md)。
