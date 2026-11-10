# 配置第一个业务 Agent

0.16.0 可给模型配置 `reasoning_options`，按 models.dev 的 effort 枚举格式保存。Agent 的 `reasoning_effort` 是当前默认模型的一档，null 表示供应商默认。模型更换时校验适用性，模型标签不用于推断档位。

## 推荐初始化顺序

1. 确认目标版本、登录身份和管理权限。
2. 配置Provider的地址/凭据，再配置模型preset。使用连接测试确认可访问；测试成功不证明复杂工具、视觉或业务流程均通过。
3. 按任务配置MCP、业务Skill及UI工具包。
4. 新建隔离Agent或预检后安装预制包，显式选择模型和资源。
5. 用真实但脱敏的正常/异常输入测试，查看最终结果与轨迹，再交付业务页面接入。

模型列表里用于运行的 `id` 与供应商的模型编码是不同字段。Agent的 `model_id` 引用preset ID；它可以为空，但运行前需要Agent默认或本次请求提供可用模型，不存在环境默认模型的隐式回退。不要写已移除的temperature或max_requests字段。

OpenAI／OpenAI Compatible 模型的“API 协议”默认是 Chat Completions；确认目标服务支持 `/responses` 后可显式选择 Responses。对应模型 `tags` 增加 `openai_responses`，保留其他标签，去掉该标记可切回；不根据模型名称或 Base URL 推测。Responses 模型勾选“推理”能力时自动请求思考摘要，订阅 GPT 也请求摘要，均不改变默认思考强度。摘要由供应商决定是否返回，不等于完整内部思维链；没有摘要文本不能据此判断未进行推理。Chat 专用高级兼容选项在 Responses 下不生效，切回后保留。

## Agent配置最小示例

使用管理员接口，先从目标目录获取真实模型与资源编码：

```http
POST /api/v1/agents
Content-Type: application/json
```

```json
{
  "name": "业务核验助手-隔离测试",
  "model_id": "<可用模型preset的id>",
  "prompt": "你负责核验已提供资料。缺少信息时明确提出问题；未经确认不修改业务数据。按任务加载已关联Skill。",
  "capabilities": ["builtin:sandbox"],
  "skills": ["<已安装业务Skill的准确code>"]
}
```

`capabilities` 是显式工具包授权，`skills` 是任务说明和资源关联；两者都必须按实际任务选择。不要因示例有Python就给所有Agent开启它，也不要假设关联Skill会自动启用Python/MCP。

更新前读取 `GET /api/v1/agents/{id}`；使用PATCH只提交本次需要改变的字段，省略表示保持。关联列表是完整列表，显式 `[]` 表示清空；`null` 不等于“不改”。保存后回读，核对模型、Prompt、工具和Skill没有被顺带清掉。

## MCP配置

先确认目标MCP的传输方式、地址、认证与工具目录。固定认证和继承当前请求身份的MCP按实际连接配置区别处理，不能把平台Token发送到任意外部服务。保存前/后按现有界面检测并回读快照；读取旧快照不证明当前网络可用。

业务 Skill 中按模型实际披露的工具名调用。新版 MCP 模型侧通常是 `mcp__<服务名>__<工具名>`，控制台、历史和 Agent 包仍沿用业务编码。PTC 内的 Python 函数使用 `run_code` 当轮提供的名称。遇到 Tool Search 或缩短后的名称，直接使用实际披露值。MCP Resource、Prompt 和可调用工具各自有独立用途，写权限以当前授权为准。

## 输出策略

普通问答可以保留文字；需要前端采用、填表或流程执行的结果，使用完整原生结构化Schema。总开关和定义由管理员配置，不逐次Chat传Schema。

```json
{
  "structured_output_enabled": true,
  "allow_text_output": false,
  "output_config": {
    "name": "review_result",
    "description": "返回待用户核验的建议，不表示批准或保存成功",
    "json_schema": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "summary": {"type": "string"},
        "questions": {"type": "array", "items": {"type": "string"}, "maxItems": 4}
      },
      "required": ["summary", "questions"]
    }
  }
}
```

这是教学用小Schema，正式业务须完整表达版本、状态、产物、互斥分支和必要字段。保存后用管理员 `GET /api/v1/agents/{id}/output-config` 回读；普通客户端无需读取完整Schema。

输出配置、Prompt或业务契约变更后新建测试会话。平台每Run读取当前配置，旧历史不保存一份可自动恢复的完整配置版本；不要在旧会话里混用新旧语义作对照结论。
