# 接口索引、版本与来源

## 常用接口

路径相对于目标服务根；管理员标记表示需要管理权限，不意味着其他接口匿名开放。完整请求/响应以目标OpenAPI为准。

| 用途 | 方法与路径 | 权限 |
| --- | --- | --- |
| 健康、产品信息、接口定义 | GET /health、/info、/openapi.json | 按目标网关 |
| 管理权限确认 | GET /api/v1/admin/access | 当前认证用户 |
| 当前有效请求范围 | GET /api/v1/request-context | 当前认证用户 |
| Agent 列表与搜索 | GET /api/v1/agents，使用 `q` 按名称或 ID 搜索 | 当前认证用户 |
| Agent 更新人 | GET /api/v1/agents?include_updater=true | 管理员 |
| 创建 Agent | POST /api/v1/agents | 管理员 |
| 指定Agent展示/更新 | GET、PATCH /api/v1/agents/{id} | GET按业务、PATCH管理员 |
| 完整原生输出配置 | GET /api/v1/agents/{id}/output-config | 管理员 |
| Provider目录/配置 | /api/v1/llm-providers | 管理员 |
| 可用模型 | GET /api/v1/llm-models | 当前认证用户 |
| 模型管理全目录 | GET /api/v1/llm-models?selectable_only=false | 管理员 |
| MCP目录与配置 | /api/v1/mcp | 管理员，实际方法见OpenAPI |
| MCP 绑定应用候选 | GET /api/v1/mcp/bound-apps | 管理员，返回当前账号可配置的应用与空间 |
| Skill列表/安装 | GET、POST /api/v1/skills | 管理员 |
| Skill原包下载/替换 | GET、PUT /api/v1/skills/{code}/package | 管理员 |
| Skill资源索引 | GET /api/v1/skills/{code}/resources | 管理员 |
| 电子表格助手关联的 Agent | GET /api/v1/integrations/deeptable-assistant | 当前认证用户 |
| 电子表格应用配置、专用 Skill | /api/v1/integrations/deeptable-assistant/app-configs | Console 管理员且具备目标 App 配置权限 |
| 普通/流式聊天 | POST /api/v1/chat、/api/v1/chat/stream | 业务用户 |
| 会话列表 | GET /api/v1/sessions | 本人范围 |
| 历史/事件/状态 | GET /api/v1/sessions/{id}/history、/events、/run-status | 本人范围 |
| 轨迹文件预估/打包 | GET /api/v1/sessions/{id}/trajectory-export/preview；POST /trajectory-export?include_files=true | 本人范围，管理员使用diagnostics/sessions对应路径 |
| 停止 | POST /api/v1/sessions/{id}/cancel | 本人范围 |
| 附件上传 | POST /api/v1/uploads | 当前认证用户 |
| 管理员会话诊断 | /api/v1/diagnostics/sessions/** | 管理员 |
| 当前实例日志 | /api/v1/diagnostics/logs、/status | 管理员 |
| Agent整包导出 | GET /api/v1/agents/{id}/export | 管理员 |
| Agent包目录/预检/安装 | GET /api/v1/agent-packages；POST /preview、/install | 管理员 |
| 开发资料目录/下载 | GET /api/v1/developer-resources；GET /api/v1/developer-resources/{code}/download | 管理员 |

不要依据目录表猜测请求Body、上传字段或覆盖语义；先查看目标OpenAPI及本包对应章节。管理接口不是模型工具，Coding Agent调用这些API仍必须按任务授权执行。

Console 的「场景」对应 `/api/v1/integrations`，电子表格助手应用配置前缀是 `/api/v1/integrations/deeptable-assistant/app-configs`。该前缀的 `GET` 只列出已保存且有权配置的 App，`GET /available` 列出全部有权配置的候选应用。选中后直接使用 `GET /{app_id}` 读取详情，`PUT /{app_id}` 保存 `config_id` 和 `prompt_append`。兼容的 `POST /validate` 不用于新版选择器。详情与写入各自校验目标 App；App 角色不扩大共用 Agent 或配置备份权限。`/{app_id}/skills` 下的列表、上传和下载均限定到对应应用。

## 有源码时如何继续定位

项目README/AGENTS作为入口；当前实现集中在src/deep_agent，接口在api/v1，运行在agent，Skill/文件/模型/MCP有各自模块。console是内置静态管理界面；业务电子表格或BOM/合同页面属于外部客户端，不能顺手混改。

修改框架源码、数据库、部署镜像或客户端代码时需要相应仓库和开发规范，本包同时提供[后端与内置Console](source/backend/index.md)、[业务客户端](source/frontend/index.md)开发参考，按具体任务读取。没有源码仍可使用已授权公开接口完成配置管理和诊断。

## 资料来源与取舍

本包对应 DeepAgent {{DEEP_AGENT_VERSION}}。应用配置、专用 Skill 和迁移基线于 2026-11-10 更新。

- DeepAgent仓库README、根AGENTS及console管理规范。
- guides中的Agent包、Skill、结构化输出、SSE、会话/日志诊断、文档解析、图片查看和平台接入说明。
- Codex维护的deepfos-dev-agent-backend与deepfos-dev-agent-frontend及其任务参考。

为了面向OP实施和业务开发，本包去掉了维护者本机路径、私有Profile、历史客户凭据、具体客户会话、旧部署快照与框架发布命令；保留版本核验、真实请求契约、资源授权、结果身份及交付边界。BOM和合同只作业务形态示例，不携带客户材料或默认业务Schema。

功能及兼容开关必须以目标实际版本和OpenAPI确认；不能因为原型界面有按钮就认定产品接口已经实现。企业鉴权适配器不是本包提供的功能。本包由管理员开发资料接口提供下载，不创建API Key、平台权限或远端资源。
