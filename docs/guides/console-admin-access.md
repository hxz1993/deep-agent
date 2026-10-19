# Console 企业管理员鉴权

DeepAgent Console 是配置和运维入口，不是业务用户聊天页面。v0.4.12 起，只有平台综合权限接口
确认的企业管理员可以进入 Console 或调用管理 API。

## 判定链路

```text
可信网关身份 user
→ 固定账号 admin 直接拒绝
→ GET /system-account-api/oauth/checkEnterpriseAdmin?userId=<user>
→ 裸true或平台标准响应包data=true：enterprise_admin
→ 裸false或平台标准响应包data=false：无管理权限
```

`systemAdmin=true` 在这条平台综合权限链路中表示企业管理员，不等同于固定账号 `admin`。
DeepAgent 当前只使用内部角色 `enterprise_admin`；将来若增加空间管理员、应用管理员或开发者角色，
应扩展统一授权器，不在各 Router 中散落判断。

Console 先读取 `GET /api/v1/request-context`确认平台登录身份，再调用：

```http
GET /api/v1/admin/access
```

已认证但不是企业管理员时返回：

```json
{"allowed": false, "roles": []}
```

缺少或失效身份返回401；平台权限服务超时、连接失败或返回异常数据时返回503并拒绝放行。
权限决策按用户ID和凭证摘要缓存30秒，缓存不保存原始Token、Cookie或Header；失败不缓存。

## 接口边界

需要企业管理员权限：

- Agent列表、Prompt估算和增删改。
- Provider 配置、授权和维护；模型新增、修改、删除与连接测试；模型目录查询、导入、导出和刷新。
- 完整模型管理列表：`GET /api/v1/llm-models?selectable_only=false`。
- MCP、Skill、UI Tool、UI Tool Package。
- 内置工具、系统工具、全局设置、数据库信息、Sandbox健康和上下文测试场景。
- 会话、用量、日志、渠道诊断查询与导出，包括 v0.13.8 新增的同路径 POST JSON 只读查询；GET 兼容入口与 POST 使用同一管理员门禁。

继续按业务使用语义开放：

- 按固定ID读取Agent：`GET /api/v1/agents/{agent_id}`。
- 可选模型列表：`GET /api/v1/llm-models`（默认 `selectable_only=true`），以及
  `GET /api/v1/llm-models/{model_id}`。两者要求可信平台用户身份，不查询企业管理员权限。
- 按编码读取指定集成配置：`GET /api/v1/integrations/{code}`；全量集成列表和配置写入仍为管理接口。
- Chat、Session、History、Cancel、主动压缩和Deferred恢复。
- 附件上传与能力查询、Request Context、Health和Info。

静态 Console 文件可以加载，但页面只有授权通过后才读取管理数据；管理 API 同时由后端强制校验，
直接访问URL或手工发请求不能绕过门禁。当前实现不建设企业、空间、应用级配置数据隔离。

## 业务客户端初始化和选模

客户端已知集成编码时，先读取 `/integrations/{code}` 获得 `agent_id`；已知 Agent ID 时可直接
读取 `/agents/{agent_id}`，取得名称、`model_id` 和默认模型摘要。随后读取默认 `/llm-models`
列表展示可选模型及标签；按 ID 补充读取模型信息也无需管理员权限。聊天请求通过 `model_id`
传递所选模型，实际执行仍校验模型启停和 Provider 可用性。

默认列表只包含当前可选模型；详情可返回已停用模型的 `selectable=false` 和不可用原因，
不因此允许调用该模型。业务模型响应保留 ID、名称、说明、调用型号、用途、输入类型、上下文、
输出上限、标签、启停与可用状态，以及安全的 Provider 摘要和凭据状态值；不返回 `base_url`、
`agent_count`、创建更新时间或原始凭据。管理员使用 `selectable_only=false` 获取完整管理列表。
模型新增、修改等管理响应保持原有结构。Agent 详情响应的字段保持现有兼容，不在本轮删减。

“无需管理员”不表示匿名访问。`user` 必须由可信网关认证后转发，缺失时返回 401；DeepAgent
不会因存在任意浏览器 Header 就独立证明用户真实有效，部署入口必须保证身份头不可被外部伪造。
本轮没有新增账号体系或额外的平台用户验证接口。

权限回归见 `tests/integration/test_model_read_access.py`：使用隔离 SQLite 和真实 API 依赖链，
不安装本地身份适配器、不覆盖鉴权依赖。平台管理员判定使用受控 checker，外部模型请求使用
确定性模型；覆盖普通初始化、缺少身份、管理员服务故障隔离、管理拒绝、管理成功、模型过滤、
凭据保护及选模后的 SSE/History。客户端回归覆盖工单助手和电子表格助手的初始化及 `model_id` 传递。

## 安全边界

- 只信任部署网关注入的当前用户和认证上下文。
- 向平台权限接口转发认证Header时不写日志，也不在错误响应中回显。
- 平台权限服务不可用时失败关闭，不因Console可用性需求绕过鉴权。
- 固定超级用户`admin`明确拒绝；不把账号名当作企业管理员依据。

## 本地 SQLite 调试例外

回环地址专用入口 `PYTHONPATH=src python3.12 -m deep_agent.local_dev` 支持在仓库外私有 Profile 中显式设置
`DEEP_AGENT_LOCAL_ADMIN_BYPASS=true`。该入口仍要求 `DEEP_AGENT_LOCAL_USER`，但可以不提供平台
Token，也不要求 App/Space；本地适配器通过不可由浏览器伪造的 ASGI scope 内部标记放行管理接口。

这个例外不属于部署配置：`deep_agent.main:app` 不读取或识别该开关，外部 HTTP Header 也不能触发。
无 App 时平台文件、下载中心和需要继承业务上下文的 MCP 仍按各自契约不可用，普通 Provider/模型/
Agent 配置与对话不受影响。
