# DeepSeek Harness 轨迹调研与 DeepAgent 落地建议

## 结论

功能可做，交互也适合直接放在当前对话标题下，以 `对话 / 轨迹` 两个主视图切换。
但 DeepSeek Harness 的轨迹并不是从聊天 History 反推出来的：它先维护一份追加式、
可持久化的 Session Event Log，再由浏览器端把同一批事件投影成 Chat 和 Trajectory。

DeepAgent 当前持久化的是 Pydantic AI `ModelMessage` 历史，已经足以还原用户消息、
结构化Context Reference、附件、思考、正文、工具调用/结果、Run ID、时间、用量、Deferred、
Planning 和 Checkpoint；
但无法可靠还原每次模型请求真正看到的完整 System Prompt、动态工具目录、工具 Schema、
模型参数、请求重试边界、首 Token 时刻和逐步骤耗时。因此不能只改前端就宣称支持
“原生请求轨迹”。

推荐把“原生”定义为**模型实际可见的、经过脱敏和大小限制的语义请求快照**，
而不是包含 Authorization、Cookie 或供应商私有字段的 HTTP 原始报文。

## DeepSeek Harness 的实际做法

### 1. 一份事实日志，多个独立投影

核心 Session Log 按顺序记录：

- `turn/start`、`turn/end`
- `step/start`、`step/end`
- `user/message`
- `request/header`：Provider、Model、模型参数、System Prompt、完整工具目录
- `request/context`：本次模型可见上下文
- `assistant/chunk`、`assistant/message`、Usage
- `tool/call`、`tool/result`
- 请求重试、压缩和 Session 终止

`ui-conversation` 和 `ui-trajectory` 分别注册自己的 Definition 和 View Builder，
消费同一批 Session Event。轨迹不是 Chat Snapshot 的附属格式，也不会进入模型上下文。

关键源码：

- `packages/client/ui-trajectory/src/client/index.ts`
- `packages/client/ui-trajectory/src/client/trajectory-snapshot-builder.ts`
- `packages/client/ui-trajectory/src/client/trajectory-request-header-definition.ts`
- `packages/client/ui-trajectory/src/client/trajectory-assistant-definition.ts`
- `packages/client/runtime/src/client/sessions/request-inspection.ts`

### 2. 轨迹列表与时间轴

列表按 Turn 和 Step 组织 User、System、Context、Assistant、Tool、Subtool、Compaction。
工具调用和结果配对为同一条可检查记录；Assistant 记录保留输入/输出 Token、TTFT、
解码耗时和推理文本。长列表使用虚拟行，默认跟随尾部，向上滚动时分页读取旧事件。

顶部时间轴支持：

- 实际耗时 / 等宽切换
- Turn、Call 整体折叠
- 搜索结果与时间轴联动
- 选择时间区间过滤记录
- 悬停查看时间点和耗时
- 缩放、平移和清除选择

关键源码：

- `TrajectoryView.tsx`
- `TrajectoryTable.tsx`
- `TrajectoryTimeline.tsx`
- `trajectory-virtual-rows.ts`
- `trajectory-search-index.ts`

### 3. 记录详情

选中记录后按类型显示不同页签：

- 普通记录：`Summary / Payload / Result / Schema / Timing`
- 模型请求：`Summary / Options / Usage / Timing`
- System 更新：`Diff / System Prompt / Tools`
- Markdown：`Summary / Preview / Raw / Source`
- Compaction：`Summary / Raw Output`

工具 Schema 是“调用当时模型看到的 Schema”，不是事后从当前 MCP 重新获取。

### 4. Session Log 导出

`Session log` 按钮属于独立导出包，不属于轨迹组件本身。浏览器先 HEAD 预检，
再让下载管理器直接下载 Host 流式生成的 ZIP；ZIP 可包含根 Session、子 Session 和附件。
它不会创建模型轮次，也不会进入模型上下文。

关键源码：

- `packages/session-query/session-log-export/`
- `packages/host/apiproxy/` 的 `/api/session.export`

### 5. 可复用性与许可

DeepSeek Harness 根仓库和 `ui-trajectory` 包均为 MIT License，可以复制、修改和再分发，
但复制代码时仍应保留许可证和版权说明。

DeepAgent Console 是无构建的原生 HTML/CSS/JavaScript，DeepSeek Harness 是
React/TypeScript 插件体系，因此不建议直接复制 React 组件。适合移植的是纯逻辑：

- Turn/Step 和 Call/Result 归组
- 稳定记录 ID
- 搜索索引
- 虚拟行与旧记录锚点恢复
- 不同记录类型的详情页签规则

## DeepAgent 当前数据能做到什么

| 信息 | 当前是否可靠 | 说明 |
| --- | --- | --- |
| 用户、思考、正文 | 是 | 已持久化在 `chat_messages.payload` 的 `ModelMessage`；同一次模型响应在UI聚合为助手Loop |
| Context Reference与附件 | 是 | 用户消息分别保存结构化引用和附件元数据，轨迹按原始`type/label`展示 |
| 工具调用、参数、结果、失败 | 是 | Tool Call/Return 保持配对，包含 Planning、Artifact 等元数据 |
| Run ID、消息时间、终态 | 是 | History 和 SSE 已提供 |
| Run 汇总用量、总耗时 | 部分 | `final.run_metrics` 实时存在；History 没有独立 Run 事实表 |
| 每次模型请求的模型和 Usage | 部分 | `ModelResponse` 有 Provider、Model、Usage；History UI 未完整投影 |
| 完整 System Prompt | 否 | 运行时组装后没有按每次请求持久化 |
| 当次完整工具目录与 Schema | 否 | 当前可用工具是动态的，历史无法事后准确重建 |
| 模型参数与请求重试过程 | 否 | 日志只有有限汇总，不是会话内可分页事实 |
| TTFT、逐 Chunk 时间 | 否 | SSE 没有作为持久事件流保存 |
| 原始 HTTP 请求/响应 | 否，也不建议 | 会包含敏感 Header，且供应商格式不稳定 |

v0.5.3选择先做“消息与工具原生轨迹”：这里的“原生”严格指当前数据库真实保存的
Pydantic AI消息、工具Part和模型响应摘要，不宣称还原HTTP报文或未持久化的模型请求。
这已经覆盖当前最常见的History/SSE、工具参数、工具失败、Run终态和Usage排障。

## 推荐落地方式

### 数据与接口

首版不新增表和API。现有 `/sessions/{session_id}/history` 继续执行Session所有权校验，
History只补充原始`ModelResponse`已经保存的安全字段：模型、Provider、Response ID、
Finish Reason、Usage和工具Outcome。Provider URL与私有详情不投影。

Console收到当前SSE时只在页面内更新轨迹；Run结束后的History刷新会用数据库事实替换
瞬时投影。导出是浏览器对当前安全轨迹生成JSON，不调用模型、不写数据库，也不包含附件
二进制、认证信息、系统提示词或动态工具Schema。

只有未来真实排障必须获得System Prompt快照、当次工具Schema、TTFT、逐Chunk时间或跨大量
历史分页检索时，才重新评估追加式事件存储。届时它应独立于`chat_messages`，不能塞入
Tool Result metadata或模型上下文，并需要统一容量、脱敏和生命周期策略。

### Console

```text
会话标题
对话 | 轨迹

轨迹
├── 顺序 / Run / 调用 / 搜索
├── Pydantic AI Run轨迹列表（助手Loop + 独立工具记录）
└── 工作台右侧Inspector中的选中记录详情
    ├── Summary
    ├── Payload
    ├── Result
    ├── 模型摘要 / Usage
    └── Timing
```

切换到轨迹时，中间内容替换为轨迹列表，选中记录详情复用原工作台右侧Inspector。
对话和轨迹共享同一个 Session，不创建新 Run。返回对话后保留原滚动位置。

## 建议阶段

1. **v0.5.3已实现**：History/SSE投影、Pydantic AI Run、助手Loop、输入上下文标记、工具配对、
   搜索、右侧详情和脱敏JSON导出。DeepAgent不照搬Harness时间轴，因为现有History没有足够完整的
   逐事件起止时间，强行绘制会产生错误因果和虚假精度。
2. **按需增强**：只有真实问题证明现有事实不足时，再补请求快照或精确时间事件。
3. **重型能力门槛**：超长Session确实需要分页搜索时，再引入独立事件存储和虚拟滚动。

当前实现不会为填满界面而反推缺失字段，因此未来即使增加独立事件源，已有轨迹字段语义也
可以保持兼容；只是增加新的事件类型和更精确的Timing，不需要推翻对话History。
