# DeepAgent 随附控制台

## 目录

- 职责归属与静态架构
- 请求上下文与路由
- 工作区与 SSE
- 配置中心
- 上下文检查与压缩
- 验证清单

## 职责归属与静态架构

`console/` 是唯一正式随附的 UI。FastAPI 在 `/console/` 提供该界面；`/` 重定向到 `/console/#/workspace`。没有明确需求时，不得重新引入已经删除的旧 `/admin/`、`/settings.html` 和 `/models.html` 页面。

保持当前纯 HTML/CSS/JavaScript ES 模块、无需构建步骤的架构，除非用户明确批准迁移框架或构建方式。

当前重要区域：

```text
console/index.html
console/main.js
console/api.js
console/context.js
console/chat.js
console/protocol.js
console/hitl.js
console/workspace.js
console/configuration.js
console/configuration/
console/diagnostics.js
console/*.css
vendor/
```

使用本地第三方资源。不要为图标、Markdown、内容净化或高亮添加生产环境 CDN 依赖。

## 请求上下文与路由

保持身份与测试上下文分离：

```text
identity: trusted user + token/cookie, read-only
test context: App, with Space derived and Language defaulted by backend rules
```

- 从 `document.baseURI` 解析 API 根地址，让本地根路径与平台多级服务前缀使用同一套代码。
- 使用统一请求辅助函数和同源凭据；不要分散编写原始 `fetch` URL 拼接逻辑。
- 绝不能通过 LocalStorage 或表单修改用户、Token、Cookie、Authorization、Space 或后端 URL。
- 仅持久化已获准保存的测试上下文。切App／Agent／会话时清除不兼容的视图，取消旧History／只读观察请求并忽略迟到结果；已发起的POST SSE按既有后台消费策略收至终态，不能为切页直接Abort而中断服务端执行。
- 使用 `/api/v1/request-context` 获取生效值。对于数据库未持久化的请求头，不要声称历史会话会记住它们。

## 工作区与 SSE

工作区层级：

```text
Agent selection -> sessions for that Agent -> selected conversation -> optional detail inspector
```

- Agent只在会话栏顶部的现有下拉切换，不恢复已删除的独立Agent列表模式或重复操作入口。
- 切换 Agent 时，必须先清除不兼容的会话、流、用量和上下文状态，再重新加载。
- 保留会话搜索、创建、测试、重命名、删除、Conversation 历史恢复、重试、附件、模型覆盖、取消、UI Deferred 及上下文引用标签。
- 检查面板必须基于真实会话与上下文数据。不要编造请求 ID、耗时、日志或成功率。
- 为事件缓冲区设置容量上限。

当前 SSE 事件类别包括：

| 事件 | 用途 |
| --- | --- |
| `session` | 生效的会话与 Agent 作用域 |
| `thinking`, `text` | 不持久化的实时 Assistant 增量，按 `item_id` 更新 |
| `tool_call`, `tool_result` | 不持久化的实时工具生命周期 |
| `session_event_appended/updated` | 用于轨迹、标题和压缩的 Event Store 镜像 |
| `context_compaction_progress` | 不持久化的自动与手动压缩进度 |
| `final` | 状态、输出、用量、Deferred 与上下文观测信息 |
| `error`, `cancelled` | 服务端运行错误／取消终态，不能与客户端单纯断网混为一谈 |

集中处理 SSE 分块、多行、CRLF 换行和尾部残留数据的解析。按本地调用上下文合并重复工具帧。不要将
无效重试调用暴露为可渲染事件。当前 Run 应持续由实时 Delta 事件驱动，并在正常
完成后保留；切换、刷新、重连或重新打开时读取 `/history`。History Item 和实时 SSE 应驱动同一个
共享的展示动作层，而不是两套独立的 DOM 投影。`session_title` 不进入轨迹；
轨迹事实来自 `session_event_appended/updated` 和进行中的实时增量。

对于 `run_code`，使用专用的 `PTC` 轨迹徽标和 Code 详情页签，从持久化的 Tool Call 中读取
参数，使用本地 Python 语法高亮，并支持复制。嵌套调用保留在外层 Tool 条目内。
对于 User 输入摘要，分别展示附件和上下文引用，不要合并成一个区块。

原生输出复用structured-output.js和markdown.js的JSON／分组、折叠、换行、复制及下载控件，保留null／false／0和看似JSON的字符串，不另做截断规则。外层结果卡承载结果名称，内部分组弱化边框；失败候选不进入成功回复。输出轨迹详情仅Summary / Output / Raw，不展示Schema／当前配置，失败候选保留真实源参数，成功显示已保存原生值。

UI确认的Call与Return可能跨Run。按已持久来源Event／Part和同Turn的唯一Deferred关联，真实Part无widget时看原Assistant的pending事实；不按调用ID全局合并，不用当前配置补造来源。持久事实替换直播行保留选择、状态、顺序与计数，来源歧义等事实校准。没有真实执行计时的人工等待显示Duration unknown；跨Run返回时间差不是执行耗时。详细规则见[Deferred与历史](ui-tools-sse-history.md#console跨run配对)。

部分响应失败先保存实际消息／状态，再发布持久事实镜像与错误；迟到事实即使在终态或下一Run后到达，也只替换同一源助手直播行，不能移到列表末尾或覆盖新轮次。错误行来自真实execution/error，不重复制造助手／异常事实。修改模块后同步缓存引用的依赖闭包，并通过真实刷新确认加载新版，不能仅凭源码测试判断缓存生效。

## 配置中心

「场景 → 电子表格助手」的已配置列表与添加应用共用后端授权范围。外层列表有独立企业列，企业筛选从有权且已保存的记录去重。切页和返回复用列表，保存和删除更新对应行，主动刷新可重新读取平台权限和配置。添加弹窗左侧按企业筛选，右侧搜索应用和空间；一次打开只发一次候选列表请求，筛选和搜索在已返回数据内完成。选择后打开应用详情，详情、写入和专用 Skill 操作仍由后端校验。保留 `/api/v1/integrations` 与既有编码。

当前导航以 `console/index.html` 和 `configuration-template.html` 为准。Agent、场景、渠道、模型、工具、MCP、Skill 和诊断各有独立职责。安装 Agent 后在「场景」关联对应助手，管理接口继续使用 `/api/v1/integrations`。

规则：

- 写入后重新读取服务端权威记录。区分保存本身失败与保存成功后刷新失败。
- 保留 401/403/409/422/503 及 `referenced_by` 冲突的后端状态与详情。
- 数据库连接信息和环境运行设置保持只读；全局配置常规页的网络搜索可通过专用弹框编辑，不把整个全局配置都视为只读。`/api/v1/database-info` 不得返回凭据、查询参数或完整本地路径。
- 运行设置只列真正可能调优的 Agent 行为参数。数据库连接仅保留独立摘要；平台连接、进程启动、内部网关及本地开发变量不在运行设置展示。新增环境变量先判断运维价值，避免按代码清单自动扩展页面。
- 模型目录仅从数据库读取，由管理员刷新；保存模型时不得访问 models.dev。
- 内置工具包定义保持只读。Agent 按 ToolPackage 代码关联整个 UI 包，包内工具全部参与；历史 `ui_tool_selection` 不参与运行、安装或对比，保存清空旧明细。
- UI Tool 采用工具包树与所选工具包的工具表格展示。内置工具包与工具只读；手动创建工具必须归入手动工具包。
- Agent 的 `capabilities` 和 `skills` 必须是显式列表。新建表单以 `[]` 为初始值；不要根据目录默认值或资源级启用状态推断资源。
- Provider、Skill、MCP、UI 工具包或工具没有独立启停层；模型有独立 `enabled`。普通选择器使用后端可选模型列表，管理页面可查看停用模型及原因。
- GPT 订阅是本机实验 Provider，订阅总开关 `DEEP_AGENT_ENABLE_SUBSCRIPTION_PROVIDER` 默认关闭；旧 `DEEP_AGENT_ENABLE_CODEX_PROVIDER` 兼容读取，新变量优先。关闭时拒绝授权、新建与调用，仍可查看和断开已有记录。新建弹框先选 API／订阅，订阅只显示 GPT；SDK 生成授权链接，新标签登录后将完整 localhost 回调地址贴回同一弹框验证，保存时才持久化凭据。普通编辑不能写 `credential_json`，重授权未保存前保持旧凭据，断开后淘汰运行实例。不要读取 Codex CLI 登录文件、把 token 返回浏览器或写进日志。当前 OAuth 与历史 API Key 一样明文保存在数据库；普通配置导出清除 OAuth 凭据和授权状态，物理数据库备份包含凭据。
- 订阅模型复用 OpenAI 模型目录，但目录仅供参考，不代表订阅账号可调用。模型用途目前只开放 `chat`；选择器使用服务端 `selectable`／`unavailable_reason`，不能只根据 `api_key_set` 判断可用性。SDK 实例按 Provider 和事件循环复用，凭据刷新定向写回且用 `auth_revision` 防止旧刷新覆盖断开或重授权；首期只允许本机单运行所有者，不支持多 worker 或 Gateway 与 API 并行使用同一订阅。真实账号授权后的流式、工具、历史、Usage 联调另行验收。
- URL MCP 的常规编辑保持名称只读。使用独立的重命名操作；builtin 和历史 stdio 不提供重命名。
- UI 和后端均须禁止删除 builtin MCP。历史 stdio 仅保留读取与删除兼容能力。

## 渠道设置与会话诊断

企微应用入口、Bot/App Secret、查询开关、缓存重置和身份边界见 [渠道与企微应用](wecom-channels-and-applications.md)。Console属于后端仓库，独立元素前端skill不负责修改这些页面。

会话列表当前为会话、Agent、用户、状态、最近活动五列；来源筛选已移除。会话类型用标题图标表示，渠道图标在Agent单元格；增宽会话列时保留其它列的合理固定宽度。ID与最近提问之间的分隔点原位变复制按钮，hover/focus显示，固定占位避免文字跳动，触屏保留可操作入口。

时间选择器整合在一个框里：点击内容选预设，右侧预设用Information查看范围，自定义用设置图标打开日期/应用浮层，不再把时间与时区常驻第二行。状态菜单直接展示现有有限选项。搜索输入关闭浏览器autocomplete，但保留页面内用户选项/筛选状态；不是关闭所有应用缓存。

渠道详情用户选择器固定右侧，群过滤在其左侧仅群聊显示；切换保留用户条件，活跃用户可联动过滤。候选来自数据库独立查询，不能用当前表格页或排行兜作全集。平台用户支持原生User ID/Username，继续游标直至查完所需范围。失去认证清除身份资料和候选，迟到请求不得覆盖新条件。

会话删除等待服务端成功才从列表移除；in-flight去重防止重复DELETE与重复扣分页。静态模块改动同步更新所有受影响父导入和HTML入口缓存标识，避免同模块以两个query版本实例化。

## 上下文检查与压缩

- 从后端 `context-status`/SSE 读取 Token 估算、可用输入预算、触发阈值与目标、用量、最新 Compaction Event 及可用性。不要在 JavaScript 中实现估算器或输出预测。
- 随附 Console 没有手动压缩快捷入口。没有明确产品需求时，不要重新引入已移除的侧边栏按钮或隐藏
  命令入口；仍可直接调用后端端点进行
  诊断。
- 仅在后端报告无会话、存在活动运行或压缩、存在待处理 Deferred，或上下文窗口未知时禁用。
- 自动与手动压缩使用同一种临时实时标记展示。将其锚定到触发压缩的 Run，并
  根据 `final.context.compaction.compaction_event_saved` 关闭；如果没有实时锚点，则隐藏，
  不要追加到页面底部。
- 压缩成功的 `final` 不得立即重新加载 `/history`：在当前实时
  页面保留已关闭的标记，等下一次明确切换、刷新、重连或重新打开时，再从 History 替换页面。只有
  未收到终止事件便结束的流，才可以使用 History 进行异常恢复。
- 重新打开的普通 `/history` 绝不包含 Compaction。轨迹读取 `/events`，按实际序号展示 Compaction
  Event，并使用同一个 Event ID 更新生命周期。
- 展示 Context Reference 标签、类型及原始 JSON 详情检查面板，但不要将运维 Console 变成业务上下文编辑器。

## 错误与缓存处理

- 保留当前 no-store 与静态资源版本行为。过期的 ES 模块依赖图可能呈现为类似后端不可用的故障。
- 对 Markdown 保持内容净化，代码高亮在本地完成。
- 附件类型、大小和数量的 UI 规则由 `/uploads/capabilities` 驱动。
- 从 `/info` 获取产品版本；不要与 Alembic 修订号混淆。

## Agent 安装界面

流程和接口见 [Agent 安装包](agent-packages-and-migration.md)。源码在 `console/configuration/agent-packages.js`、`agent-package-export.js` 和 `console/agent-packages.css`。安装方式新建/覆盖用 Radio；覆盖时隐藏名称和默认模型，目标选择 placeholder 使用灰色，真实选项正常显示。检查和反馈用紧凑表格，状态有轻量颜色，缺失修复动作写对应行，不在底部重复提醒。反馈提供文字“查看 Agent 配置”和主按钮“确定”。

使用现有主题变量；各步骤和反馈保持同一尺寸，长内容滚动、不分页。通过当前项目缓存标识刷新静态依赖，不复制浏览器临时预览属性。

## 验证清单

纯文案/样式只检查改过的模块并做一次受影响桌面流程冒烟；不默认测移动端、重跑完整 Console 或后端矩阵。行为变化按需选定向 Console 测试、API、实时/历史或 Deferred 回归。没有新失败或未解风险时不重复测试。

```bash
node --check console/configuration/agent-packages.js
node --test tests/frontend/console/agent-packages.test.mjs
```

测试文件名先从当前检出代码确认。使用已有本地实例；不要为了静态 UI 刷新重启共享数据库连接。自行创建的浏览器在 finally 中关闭上下文和实例，清理临时产物；用户原有浏览器保留。


## UI 规范入口与实施顺序

目标仓库的 `docs/guides/console-ui-guidelines.md` 是具体实施规范；按仓库根定位该相对路径。`console/AGENTS.md` 保留页面语义，`console/ui-primitives.css` 是公共尺寸与基础样式来源。不要将本参考的历史快照当成覆盖当前源码的理由。

1. 先读项目规范第 11 节，找到同类控件和一个相邻页面；核对最终 CSS 加载顺序，不只读最早定义。
2. 正文/辅助/元信息为13/12/11px，一级页面标题16px/20px，详情分组及弹框标题16px/24px，分组标题栏32px并顶对齐；控件32px、行内28px；圆角8px、内部6px、弹层12px；图标14/16/18px；组内8px、组间28px。使用公共token，不复制常量。
3. 分组复用 `console-section` 家族；表格复用 `.table-wrap.console-list-a > .data-table`，分页归表格右下；搜索复用 `console-search`。选择器复用 `ui-select.js` / `ui-editable-select.js`，提示复用 `ui-field-help.js`，图标仅本地Lucide和已有品牌资产。
4. 普通详情组、资源目录卡、分段切换、内容页签分别按语义复用；不要为了统一外观抹掉真实选择和滚动边界。聊天、代码、日志、轨迹与图表内容不是普通设置字段，保留专用排版。
5. 纯UI只做静态检查与受影响桌面冒烟，视觉由用户确认。记录实际检查，不声称全页/多主题已验证；无需启动迁移、升版本或自动提交。

全局配置为常规/模型/运维：网络搜索可编辑；模型目录浏览和导入导出；运维依次是接口文档入口、数据库信息、环境运行设置。三页共用1080px最大内容宽度及左右24px内边距。UI工具选择只有整包关联；旧明细字段兼容事实可查 `src/deep_agent/agent_packages/archive.py` 的 `discard_legacy_ui_tool_selection`、`db/stores/agent/base.py` 保存逻辑及包导出逻辑。

带前置图标的筛选统一复用 `console-control-with-icon`：16px图标、12px左内距、8px图文间距，文字起点36px，32px控件内垂直居中；由 `ui-primitives.css` 的 token 管理，现有诊断类共享同一实现。新增页面不复制 `left/top/padding-left`，无图标控件不保留空位。

公共控件拥有层：按钮/输入/搜索/分页/分段切换在 `ui-primitives.css`，下拉触发器与选项在 `ui-select.css`。现有 `.da-select-*` 与 `.console-select-*` 共用外观而保留各自控制器及浮层定位。新增页面直接复用；调整时删除旧页面重复声明，并检查加载顺序与浏览器计算样式，不能持续追加高优先级覆盖。日志行与行内操作等密度例外保留且必须说明。
