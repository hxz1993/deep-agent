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
- Provider、模型、模型目录和刷新。
- MCP、Skill、UI Tool、UI Tool Package。
- 内置工具、系统工具、全局设置、数据库信息、Sandbox健康和上下文测试场景。

继续按业务使用语义开放：

- 按固定ID读取Agent：`GET /api/v1/agents/{agent_id}`。
- Chat、Session、History、Cancel、主动压缩和Deferred恢复。
- 附件上传与能力查询、Request Context、Health和Info。

静态 Console 文件可以加载，但页面只有授权通过后才读取管理数据；管理 API 同时由后端强制校验，
直接访问URL或手工发请求不能绕过门禁。当前实现不建设企业、空间、应用级配置数据隔离。

## 安全边界

- 只信任部署网关注入的当前用户和认证上下文。
- 向平台权限接口转发认证Header时不写日志，也不在错误响应中回显。
- 平台权限服务不可用时失败关闭，不因Console可用性需求绕过鉴权。
- 固定超级用户`admin`明确拒绝；不把账号名当作企业管理员依据。

## 本地 SQLite 调试例外

回环地址专用入口 `python3.12 -m deep_agent.local_dev` 支持在仓库外私有 Profile 中显式设置
`DEEP_AGENT_LOCAL_ADMIN_BYPASS=true`。该入口仍要求 `DEEP_AGENT_LOCAL_USER`，但可以不提供平台
Token，也不要求 App/Space；本地适配器通过不可由浏览器伪造的 ASGI scope 内部标记放行管理接口。

这个例外不属于部署配置：`deep_agent.main:app` 不读取或识别该开关，外部 HTTP Header 也不能触发。
无 App 时平台文件、下载中心和需要继承业务上下文的 MCP 仍按各自契约不可用，普通 Provider/模型/
Agent 配置与对话不受影响。
