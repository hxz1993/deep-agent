# Context Reference设计（v0.6.0）

Context Reference把DeepCube区域、DeepTable页面/选区、元素和对象等结构化上下文与用户正文分开，
避免前端把业务JSON拼成不可恢复的Prompt文本。

## 请求与存储

聊天请求可携带：

```json
{
  "message": "分析当前区域",
  "context_refs": [
    {
      "key": "selected-region",
      "type": "deepcube.editor_context",
      "schema_version": "1",
      "label": "当前区域",
      "payload": {"element_id": "...", "member_ids": ["..."]},
      "metadata": {"display_only": true}
    }
  ]
}
```

完整引用保存在对应`user/message`Event的Pydantic Part中，不增加独立表或SQL列。`key/type/schema_version/
label/payload`形成模型安全投影；`metadata`只供历史和宿主UI展示，不进入模型、摘要、Search Text或Token
估算。Event API按原Payload回放，Console只展示中性标签和授权后的详情。

## 运行与Deferred

当前轮在模型调用前把安全字段确定性渲染到用户输入，不让Provider决定协议。Context Reference不授予
工具、文件或跨会话权限；业务ID只是待验证数据，真实操作仍由对应MCP或服务鉴权。

首版`context_refs`与同请求的`deferred_tool_results`互斥；Deferred恢复依靠原Call ID和Tool Result，
不从旧上下文猜造新输入。恢复Run继承原`turn_id`，原Context Event保持不变，不重复持久化一份引用。

## 压缩与回查

当前保留尾部中的Context Reference完整进入模型History。进入压缩前缀后，结构化引用不交给摘要模型
自由改写；程序生成`context_evidence`：

- `source_event_id`
- `key/type/schema_version/label`
- `payload_hash`
- 受限稳定字段（元素、对象、成员、坐标等标识）

小型安全Payload可在总预算内原样进入摘要锚点；超预算时原Event仍完整保留，Evidence只保存Hash、稳定
索引和读取入口，不截断半个JSON。“不被压缩掉”指事实、标识和回查能力不丢失，不表示所有历史大Payload
永久常驻模型即时输入。

模型需要精确值时先使用`search_conversation_history`定位，再用`read_conversation_event(event_id=...)`
读取有权限的原Event。摘要Evidence不能成为文件、写入坐标或其他资源权限的唯一来源。

## 客户端边界

- `app_id/space_id`是Session可空来源，不等于Context Reference，也不参与当前Event读取授权。
- 外部助手继续发送结构化`context_refs`；不要把XML、Markdown或JSON字符串作为唯一事实。
- History接口已替换为Event API；客户端按Sequence分页，并从`user/message`Part恢复引用卡片。
- 用户复制只复制可见正文，不能混入Payload或Metadata；包含引用的旧消息不支持无损编辑重试。

## 验收

- Event Codec往返后引用Payload与消息边界无损。
- Metadata不进入模型、压缩输入、搜索或Token预算。
- 压缩前缀生成合法Context Evidence，尾部引用保持完整。
- 压缩后可按Event ID精确读取原引用，跨用户或跨Agent拒绝。
- 中文Label可被安全Search Text定位，但Reasoning和敏感字段不参与搜索。
