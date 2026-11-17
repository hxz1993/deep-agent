# Chat、SSE、附件与历史

0.16.0 的 `/chat` 与 `/chat/stream` 接收可选字符串 `reasoning_effort`。省略或 null 跟随实际模型上有效的 Agent 档位，`default` 显式使用供应商默认。其他值取自模型选择响应的 `reasoning_efforts`。新 History 用户条目可有该字段，旧条目省略。

## 新建和继续会话

`POST /api/v1/chat`等待完整Agent运行，返回JSON；`POST /api/v1/chat/stream`使用SSE。调用一次可以包含多轮模型与工具循环，不是只请求一次LLM。新会话传agent_id；继续时使用真实session_id并保持Agent对应关系。

```json
{
  "agent_id": "<目标Agent ID>",
  "message": "请核验本次选中记录，返回待确认建议。",
  "context_refs": [{
    "key": "review.request",
    "type": "review.document_context",
    "schema_version": "1",
    "payload": {"revision": 3, "row_ids": ["row-a"]}
  }],
  "attachments": [{"id": "<上传返回的File ID>", "name": "source.pdf", "mime": "application/pdf", "input_mode": "reference"}]
}
```

后续请求可加session_id。model_id是可选的本轮模型preset覆盖，不改Agent默认模型。身份不从Body user传入。没有通用`inputs`业务字典字段；业务快照使用context_refs，文件使用attachments。

服务端结果含session_id、run_id、status、output、usage等；原生结构化结果还带output_info。不要假设有一个通用顶层files字段，业务文件引用应按输出契约或真实工具结果读取。

## 附件

`POST /api/v1/uploads`为multipart `file`，返回`id/name/mime/size`。Chat附件使用`id`字段；Python/Skill的files参数使用`file_id`字段，不能混淆。`input_mode=reference`保留引用而不自动向模型发送文件字节，适合后续工具读取；实际权限仍按会话与用户校验。

已经生成的可信文件在同会话可由read_file/view_image等按各自格式能力读取。File ID不是公开URL，也不是跨用户授权。下载使用目标环境已有授权文件接口/Console入口；不要给客户拼未经鉴权的公开URL。

## SSE必须完整消费

| 事件 | 处理 |
| --- | --- |
| session | 保存session_id/run_id等真实身份 |
| thinking / text | 按顺序累计展示；不因一段text就停止消费 |
| tool_call / tool_result | 按会话、响应/轮次和调用身份关联 |
| output accepted=false | 格式拒绝与自动纠错过程，不是Run终态 |
| output accepted=true | 可交业务校验的原生结果，以output_id去重 |
| final | 按status、output_info与deferred确定完成或等待输入 |
| error / cancelled | 真实失败/取消，保留已存在的业务与文件事实 |

SSE可能含注释心跳、分片或一块内多帧，按标准事件边界解析，不用逐行假设“一行一个完整JSON”。流式断开后先查状态与历史，不能盲目重发同一Chat POST，否则可能重复执行业务。

SSE、History 和轨迹继续显示业务工具名。跨分组同名时，按所属会话、Run 和 `tool_call_id` 配对调用与结果，结合事件类型区分普通工具、UI 待操作和结构化结果。结构化结果沿用 `output_info.name` 和 `output_id`。Provider 调用别名由后端处理。

## 会话状态与恢复

```text
GET /api/v1/sessions/{id}/run-status
GET /api/v1/sessions/{id}/history
GET /api/v1/sessions/{id}/events?after_sequence=0&limit=500
POST /api/v1/sessions/{id}/cancel
```

以上按目标OpenAPI和当前用户权限使用；跨用户改用管理员诊断接口。run-status的idle不证明业务已成功，最终结果还要看History/接受事实。History供普通业务渲染，Events供精确轨迹；不要让客户端各自从原始Pydantic消息猜恢复规则。

运行中以聊天 SSE 接收正文和工具结果。History 和 Events 反映已提交的状态，普通只读轮次的结果可能在检查点或收尾时才成批出现。当前实例有运行时，读取 `run-status` 后可用 `GET /api/v1/sessions/{id}/live?agent_id=…&run_id=…` 观察指定 Run。观察连接关闭时，发起执行的连接继续负责原运行。

刷新、切换或重新打开只读恢复，不能因此重新填表、批准或保存。停止使用cancel接口并等真实终态，Abort连接不等于业务已撤销。页面卸载也不保证任务一定继续运行。

## 人工确认续跑

只有真实pending/deferred工具调用才续跑，沿用原tool_call_id：

```json
{
  "session_id": "<原会话ID>",
  "agent_id": "<原Agent ID>",
  "deferred_tool_results": [{
    "tool_call_id": "<真实待交互调用ID>",
    "result": {"confirmed": true, "values": {"target": "demo"}}
  }]
}
```

此请求走`/chat/stream`。result结构必须符合该业务UI工具契约，不照抄示例。普通澄清问题不是pending；不要伪造Deferred或另开Turn重置平台纠正预算。
