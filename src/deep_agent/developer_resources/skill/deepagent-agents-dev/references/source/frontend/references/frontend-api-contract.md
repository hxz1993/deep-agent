# 前端调用 deep-agent 契约

资料核对：2026-11-06，DeepAgent {{DEEP_AGENT_VERSION}}。原生业务结果另读[结构化输出](frontend-structured-output.md)。

## 目录

- [地址与请求头](#地址与请求头)
- [接口清单](#接口清单)
- [聊天请求](#聊天请求)
- [SSE 状态机](#sse-状态机)
- [会话与历史](#会话与历史)
- [附件与模型](#附件与模型)
- [错误处理](#错误处理)

## 地址与请求头

浏览器统一调用同源前缀：

```text
/deepfos-server/deep-agent-server1-0/api/v1
```

前端请求约束：

- 所有 fetch 使用 `credentials: "include"`。
- 显式透传 `app`、`space`、`language`；值优先来自平台运行时信息和 URL `sign`。
- 不在浏览器代码里保存或打印 token/cookie。
- 不把 Body/Query 中的 `user` 当作身份。部署环境由网关提供可信 Header；本机 8000 联调由后端 `deep_agent.local_dev` 的私有身份 profile 提供，不由前端代理补造。
- 前端遗留类型若仍有可选 `user`，不要在新调用中传入。以当前 OpenAPI 为准逐步清理遗留字段。

## 接口清单

| 前端用途 | 方法与路径 |
| --- | --- |
| Agent 展示信息 | `GET /agents/{agent_id}` |
| 模型选择 | `GET /llm-models` |
| 会话列表 | `GET /sessions?agent_id=...&has_messages=true&limit=...&offset=...&sort=updated_at` |
| 当前运行与只读观察 | `GET /sessions/{session_id}/run-status`，取得运行 ID 后订阅 `GET /sessions/{session_id}/live?agent_id=…&run_id=…` |
| 会话信息 | `GET /sessions/{session_id}?agent_id=...` |
| 会话历史 | `GET /sessions/{session_id}/history?agent_id=...` |
| 会话重命名 | `PATCH /sessions/{session_id}?agent_id=...`，Body `{"title":"..."}` |
| 删除会话 | `DELETE /sessions/{session_id}?agent_id=...` |
| 停止当前运行 | `POST /sessions/{session_id}/cancel?agent_id=...` |
| 上下文状态 | `GET /sessions/{session_id}/context-status?agent_id=...&model_id=...` |
| 手动压缩 | `POST /sessions/{session_id}/compact?agent_id=...`，Body `{"model_id":"..."}` |
| 上传附件 | `POST /uploads`，multipart `file` |
| 流式聊天/续跑 | `POST /chat/stream` |

这份清单只描述前端消费接口，不授权调用 Agent/UI Tool Admin 写接口。

当前 DeepCube 助手只消费自动压缩直播事件，不主动调用 `context-status` 或 `compact`。只有产品明确要求主动压缩 UI 时，才按目标环境 OpenAPI 接入这两个接口。

`GET /agents/{agent_id}` 当前响应包含 `model_id`、嵌套 `model`、`prompt`、`capabilities`、`skills`；原生输出开关/完整定义按管理员output-config接口回读（`model_id/model` 可空，`temperature` 已移除）。前端展示模型时使用响应中的真实模型 preset，不按模型名称或旧 manifest 猜测；新增响应字段应向后兼容，不要求页面把全部配置暴露给用户。

`GET /llm-models` 默认 `selectable_only=true`，只返回当前可选 preset；普通聊天客户端不需要按 `enabled` 再过滤。管理页面需要查看停用模型时才显式请求 `selectable_only=false` 并依据 `selectable/unavailable_reason` 展示。

## 聊天请求

新消息：

```json
{
  "message": "用户原始消息",
  "session_id": "已有会话时传",
  "agent_id": "Agent preset ID",
  "model_id": "LLM preset ID",
  "context_refs": [
    {
      "key": "deepcube.editor",
      "type": "deepcube.editor_context",
      "schema_version": "1",
      "label": "DeepCube 元素信息",
      "payload": {},
      "metadata": {"source": "element-python3"}
    }
  ],
  "attachments": []
}
```

HITL 续跑：

```json
{
  "session_id": "同一会话",
  "agent_id": "同一 Agent",
  "model_id": "当前选择的 LLM preset",
  "deferred_tool_results": [
    {
      "tool_call_id": "pending 返回的原 ID",
      "result": {
        "confirmed": true,
        "values": {
          "target_account": {"value": "A001", "label": "收入"}
        },
        "message": "目标科目=收入"
      }
    }
  ]
}
```

规则：

- `model_id` 使用 `/llm-models` 的数据库 preset `id`，不要传显示名。
- `context_refs[].payload` 是模型可见业务事实；`metadata` 只用于历史/UI，不是鉴权或业务事实。
- 对规范化后的 payload 做稳定序列化并比较 signature；首轮、页面上下文变化、历史恢复后的首次续聊或显式刷新才重发业务引用，普通追问和自动压缩成功后不重复发送。
- 续跑不需要伪造一条普通用户消息。
- `branch_from` 与 `deferred_tool_results` 不同时使用。
- 不发送 `undefined`、前端预览 URL、临时文件大小等无关字段。

## SSE 状态机

| Event | 前端动作 |
| --- | --- |
| `session` | 保存服务端 `session_id`，作为后续历史和续跑依据 |
| `context_compaction_progress` | 更新独立的非持久压缩过程状态，不写入 Conversation Store 或正文 |
| `thinking` | 追加/合并思考 segment，记录真实开始时间 |
| `text` | 按流顺序追加正文 segment |
| `tool_call` | 按 `tool_call_id` 建立或更新工具 segment |
| `tool_result` | 按原待执行调用身份填充result、结束运行态；UI返回可来自Resume Run，不按当前Run另建记录或全局按ID合并 |
| `tool_call_removed` | 删除同 `tool_call_id` 的未持久直播工具项，不显示为失败或已中断 |
| `output` | accepted=false是格式尝试；accepted=true才可交业务校验，按output_id去重，详见原生输出参考 |
| `session_title` | 直接更新页头和会话列表标题，不轮询会话详情 |
| `final(status=completed)` | 收口Run/Usage；有output_info时直接采用原生output，文字路径按终态正文校准，均不能重复追加或自动保存 |
| `final(status=pending)` | 结束 Run；实时可交互 Deferred 使用已校验的 Tool Delta，重新打开时以 History 返回的最终 `mode/status/call_id` 为准 |
| `cancelled` | 保留当前内容，结束 loading，将未完成工具标为中断并显示“已停止”；不立即刷新 History |
| `error` | 结束 loading，显示后端 message/error |

解析要求：

- 保留chunk buffer，用完整空帧分隔；兼容CRLF跨chunk拆开、多行data及尾帧，不能把半个CRLF误当两个空帧。
- 支持一个 frame 多行 `data:`，拼接后再 `JSON.parse`。
- reader 结束后解析尾部 buffer。
- 无法解析的 frame 不应让整个聊天组件崩溃；记录证据并按协议异常处理。
- 同一实际调用的参数增量更新旧args，不能渲染成两张表单；跨轮可能复用tool_call_id，不全局覆盖。
- `context_compaction_progress` 的后续 phase 更新同一状态行；`compaction_event_saved=true` 时只收口当前直播标记，History 不恢复该标记。

正式停止规则：

- 发起流后先等待 `session` SSE，拿到本轮 `session_id` 才启用停止按钮。
- 点击停止调用 session cancel 接口，`cancel_requested` 后继续读取同一 SSE；真实停止以 `cancelled` 为唯一终态，取消后不再有 `final`。
- `not_running`、HTTP 错误或网络失败不代表已停止，前端保持原流并允许重试。
- 当前页面不因 `cancelled` 立即重载 History；重新打开会话时以 `assistant.status=interrupted` 校准。模型尚未输出就被停止时，History 可以只有用户消息。
- Pending UI Tool 没有活动 Run，表单取消继续通过 `deferred_tool_results`，不能调用 session cancel。

## 打开正在回复的会话

列表按需传 `include_active_runs=true`，当前实例上的运行项带 `active_run_id`。打开后用 `run-status` 确认当前 Run。发送页继续消费原 POST SSE；另一页面观察该 Run 时，先连只读 Live 并缓冲帧，再读取 History。按 `observation_sync.baseline_sequence` 保留基线历史和本轮用户输入，用回放还原本轮正文与工具。

只读工具结果可能先出现在 SSE，到保存点才出现在 History。关闭观察页只释放订阅；关闭发送页时，服务端尝试取消并保存已收到的正文和已完成的工具。收到 `observation_end` 回读 History；发生 `observation_reset` 或原 Run 无法回放时也按原会话校准。恢复页面只读展示，由用户操作触发审批或业务写入。

## 会话与历史

- 会话列表分页使用后端 `total/limit/offset`，不要把前端当前数组长度当成总数。
- 标题异步生成时监听 `session_title`；人工重命名仍使用 Session PATCH，禁止标题轮询。
- `/history` 校验 `schema_version=1`，读取 `ConversationHistoryResponse.items`，按 `sequence + part_offset + item_id` 排序。
- 空闲会话用 History 全量替换历史消息视图，观察进行中的会话按上述基线衔接；实时 SSE 与 History 归一化成同一种页面消息/segment 结构并复用 renderer。
- `user_message` 保存纯正文、附件和 `context_refs`；不要解析 XML envelope。
- `structured_output`直接读取name/output，item_id=output_id；与直播同结果去重，平台身份不替代业务patch/提交身份，恢复只读不重发操作。
- `assistant_message` 保存服务端投影的正文/思考；Tool 直接使用 `kind/status/mode/call_id/input/output/widget/metadata/artifacts`，不要前端配对历史 Call/Result。
- 只有 `tool.mode=deferred && status=awaiting_input` 可用原 `call_id` 续跑；终态 Tool 不可再次提交。
- History 即使包含 `plan` 投影也不恢复计划面板。计划只属于当前 Run，直播只识别 `write_plan` 完整快照；任一 `final`（含 pending）、`cancelled` 或 `error` 后隐藏。
- 普通聊天不请求或解析 Event API，也不消费 `session_event_appended/updated`。正常 `final` 保留直播结果；打开、切换、刷新、重连或异常断流时才读取 `/history` 校准。
- list/get/history/patch/delete 都按当前 Header 用户做 owner 校验；前端不添加 `user_id` 查询来模拟权限。
- 缺用户身份返回 401；其他用户或无归属会话返回 404 时，不要前端回退成全量读取。

## 附件与模型

以下图片门禁是当前 `element-python3` 的产品行为；后端媒体接受规则与 View Planning renderer 需各自核验，不将这套限制推广为所有客户端的服务端契约。

- 初始化时读取 `/uploads/capabilities` 的 `kinds/allowed_mimes/max_size_mb/max_count`；`kinds=[]` 表示未开放。接口异常时只能使用产品明确维护的安全 fallback，不能把空能力当通配。
- 文档能力取“DeepCube 产品格式白名单 ∩ 环境 allowed_mimes”，不依赖模型视觉模态；图片还要求 `/llm-models[].input_modalities` 包含 `image`。
- 选择、拖拽、粘贴和发送前使用同一校验；文本模型的图片在调用 `/uploads` 前拦截。混合批次保留合法文件并汇总提示跳过项。
- 上传后保存服务返回的附件引用；Chat Body 只发送 `{id,name,mime}`，剔除 `previewUrl`、本地 `File/size`、Blob URL、`extracted_text` 和纯 UI 字段。
- 图片预览下载走平台文件服务，并对 ID/文件名做 URL 编码；移除、新建和卸载时释放 Blob URL。
- 待发送项包含图片时阻止切换到纯文本模型；只有文档时允许切换。发送前再次校验，避免模型列表刷新造成竞态。

`builtin:ptc`（兼容历史别名`builtin:code-mode`）启用后，`run_code`仍使用普通Tool SSE/History投影。前端按服务端工具名、状态和artifacts展示，不解析内部动态MCP函数、不为嵌套调用另造历史。PTC规则去重不会新增客户端协议或要求改提示词／Skill。

## 错误处理

- 非 2xx 先解析 JSON `detail`，其次 `message`，最后显示原始文本或 `HTTP <status>`。
- `AbortController` 只用于组件卸载、页面关闭或传输资源清理；不能作为用户点击“停止生成”的产品语义。
- `final`、`cancelled`、`error` 和请求异常都必须释放 sending/loading；取消、错误、切换会话和卸载还要清理压缩过程状态及计时器。
- 页面出现 `Connection Closed` 时先看 EventStream 最后事件，再判断是前端解析、代理、网络还是后端错误。
- 用量使用实际final.usage及当前协议run_metrics，缺失标未知；管理员诊断可读持久真值但普通客户端不为此取得管理权限。Provider请求、客户端POST与工具调用分开计数，不从History猜token。
