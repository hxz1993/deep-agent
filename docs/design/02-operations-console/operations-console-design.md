# DeepAgent 一体化运维控制台设计

> 状态：已实现
>
> 生产入口：`/console/`

## 目标

Console 是面向企业管理员的配置、联调和诊断界面，不是最终业务用户聊天产品。
它把测试工作台、配置中心和诊断中心放在同一个静态前端中，并与当前 DeepAgent 服务
同源发布。

## 信息架构

- 测试工作台：选择 Agent、管理会话、发送消息、查看 SSE、工具事件、附件和压缩状态。
- 配置中心：维护 Agent、Provider、模型、模型目录、MCP、UI 工具及只读全局配置。
- 诊断中心：查看服务状态和管理员排障信息。

旧的分散管理页面不再作为正式入口。

## 管理权限门禁

Console 通过平台综合权限接口判断当前用户是否为企业管理员。平台返回的
`systemAdmin=true`在本场景映射为内部角色`enterprise_admin`；固定超级用户`admin`不特殊
放行，并明确拒绝进入Console。

```text
平台可信身份
→ /api/v1/admin/access
→ 平台 checkEnterpriseAdmin
→ enterprise_admin 才加载 Console 数据
```

页面门禁只负责交互，Agent配置、模型、MCP、Skill、UI工具、全局设置和管理诊断等Router还会
统一执行后端门禁。平台权限服务超时或异常时返回503并失败关闭。Chat、Session、History、附件、
取消、压缩以及按固定ID读取Agent继续服务业务助手，不要求企业管理员角色。

本阶段不做企业、空间和应用级配置数据隔离，也不为普通用户建设Console只读模式。后续扩展
`space_admin`、`app_admin`或`developer`时，应在统一授权器中增加角色，不修改每个业务Router。

## 请求上下文

通过管理门禁后，Console 只允许企业管理员编辑应用编码 `App`：

- `Space` 由 App 去掉末尾三位数字自动推导。
- `Language` 固定为 `zh-cn`。
- `User` 来自平台当前认证用户。
- API 地址使用 Console 所在部署路径的同源基址，不要求管理员拼接服务地址。

切换 App 会清理不再有效的工作台状态。请求上下文与 MCP Header 转发共用后端解析规则。

## 工作台能力

- Agent 搜索、选择和刷新。
- 会话搜索、新建、重命名、删除、历史恢复和测试场景初始化。
- REST/SSE 对话、停止、错误恢复、消息编辑和分支重发。
- 工具调用、Deferred UI、附件、模型覆盖、Token 与上下文压缩事件展示。
- 请求上下文和最终生效值只读核对。

测试会话入口只为快速构造合法历史，不受额外环境变量控制。

### Agent 选择与布局

工作台默认使用 Agent 下拉框，把主要空间留给会话、对话和运行检查器；需要浏览 Agent
列表时可临时固定为最左侧栏：

- 下拉模式：Agent 图标作为下拉框前缀，固定操作位于下拉框右侧。
- 固定模式：刷新和取消固定集中在 Agent 标题栏。
- 测试 Header 和会话详情统一位于对话标题栏右侧，不随 Agent 模式改变位置。
- 固定状态只存在于当前页面内存，浏览器刷新后恢复下拉模式。
- 约 1260px 以下优先隐藏检查器；约 920px 以下恢复可访问的 Agent 下拉入口。

默认桌面布局为“会话 300px / 对话自适应 / 检查器 320px”；固定时增加 188px Agent
列，并将会话列调整为约 288px。会话栏不再重复展示当前 Agent 标题，Agent 列底部也不再
重复放置测试上下文入口。

### 运行检查器

右侧详情不设页签，在一个连续泳道中依次展示当前会话、请求 Header、上下文预算、本轮模型
调用和Compaction。工具调用过程已经在对话区展示，底层SSE事件也不再由详情区重复
缓存和渲染。上下文指标口径：

- 原始历史估算：`context-status` 基于完整原始会话历史计算的快照。
- 动态输出预估、安全余量和可用输入预算：均由后端统一预算算法返回，不是管理员配置。
- 压缩触发线/目标线：基于后端可用输入预算计算。
- Run Usage：当前页面收到的最近一次 `final.usage`，不单独持久化，不能从历史正文猜测。
- Compaction：从Event Store派生的结构化摘要锚点，不替代或改写原始消息Event。

`context-status` 的 `estimate_scope=history_only` 与实际 Run 的 `final.context` 含义不同：前者
用于会话检查，后者是该次运行完成后的预算与压缩观测。

## 配置中心

- Agent 与 capability 关联。
- `llm_models` 运行配置和 `model_catalog` 只读目录/手工刷新。
- URL/内置 MCP 配置和健康状态。
- builtin/manual UI 工具查看或维护。
- 环境变量加载后的全局配置只读快照。

配置页应区分“主体保存失败”和“主体已保存但列表刷新失败”，避免管理员重复创建。

## 部署边界

- 前端静态资源随 FastAPI 服务发布。
- API、Console、Docs 和 MCP 路由都基于部署前缀动态解析。
- 本地默认访问 `http://127.0.0.1:8000/console/`。
- 平台部署可位于多级反向代理路径，前端不硬编码 `/api` 的绝对根路径。

## 原型与生产代码

原型位于 [`prototype/operations-console/`](prototype/operations-console/README.md)，只保留
信息架构和视觉参考。实现和测试以 `frontend/console/`、`tests/frontend/console/`
为准。
