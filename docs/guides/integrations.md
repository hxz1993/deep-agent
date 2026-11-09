# 组件集成配置

“集成”提供固定功能目录和通用 JSON 配置。当前目录由代码定义，数据库不初始化组或集成配置；只在管理员保存非空配置时新增记录。v0.9.11 引入此功能，数据库结构版本为 004。

| 组编码 | 组名称 | 集成编码 | 集成名称 |
| --- | --- | --- | --- |
| `deeptable` | 电子表格 | `deeptable-assistant` | 电子表格助手 |
| `python-server` | Python Server | `deepcube-assistant` | DeepCube 助手 |

目录名称、图标、归组关系都来自 `integrations/catalog.py`，不作为数据库数据重复保存。读取目录时叠加已有配置，没有记录的集成返回 `settings: {}`、时间戳 `null`。读取永远不新增记录，不需要发布后的初始化操作。

控制台左侧显示集成目录，右侧显示当前配置和接口示例。点击「关联 Agent」打开下拉菜单，选择或清空 Agent 后点击「保存」。

## 存储结构

Alembic `004` 在 `003` 后仅新增 `integration_settings`，不改旧表、旧数据，不插入种子记录。

| 字段 | 类型 | 约束与用途 |
| --- | --- | --- |
| `code` | VARCHAR(64) | 主键，取固定集成编码 |
| `settings` | JSON 序列化文本 | 非空，保存完整键值配置 |
| `created_at` | UTC 时间戳 | 首次保存时间 |
| `updated_at` | UTC 时间戳 | 最近保存时间 |

`settings` 沿用项目 `JSONType`：SQLite/PostgreSQL 使用 TEXT，MySQL 使用 LONGTEXT；接口和值保持 JSON 类型，不依赖数据库原生 JSON 类型。根必须是对象，最多 64 KiB UTF-8、8 层嵌套、2048 个值；键为非空字符串且最多 128 字符。支持字符串、有限数字、布尔、null、数组及嵌套对象，不把值强制转成字符串。

当前控制台的两个集成项均选择 Agent，保存为 `settings.agent_id`。存储结构仍为 JSON，没有 Agent 外键，也没有固定 `agent_id` 列；后续功能可使用其他 JSON 键，存储层不把它解释为关系。已认证组件客户端可读取配置，因此此处用于组件公共运行配置，**不用于保存密码、模型 Key 等凭据**。

保存使用各数据库的原子 upsert，并沿用公共写事务与配置导入锁。`{}` 与清除操作都会删除已有行，恢复未配置状态。004 降级至 003 删除本表及其配置，其他业务表保留；往返测试仅使用隔离数据库。

配置备份自动包含本表；从尚未升级的 003 导出时仍保留 003 结构，不要求此表。配置导入继续要求源、目标数据库 Revision 相同。

## 接口

| 方法与路径 | 权限 | 行为 |
| --- | --- | --- |
| `GET /api/v1/integrations` | 企业管理员 | 固定目录与配置叠加，始终返回两个组 |
| `PUT /api/v1/integrations/{code}` | 企业管理员 | `{"settings": {...}}` 完整替换配置，空对象清除 |
| `DELETE /api/v1/integrations/{code}` | 企业管理员 | 幂等清除，返回未配置目录项 |
| `GET /api/v1/integrations/{code}` | 已认证用户 | 返回 `{"code": "...", "settings": {...}}` |
| `GET /api/v1/integrations/{code}/api` | 企业管理员 | 页面接口示例的路径、说明和参数示例 |
| `POST /api/v1/integrations/deeptable-assistant/app-configs/validate` | 对所属企业有管理权限的企业管理员 | 传入 `{"app_id":"budget001"}`，验证有效应用并返回应用、空间和企业 ID |

当前运行接口职责为读取集成配置，不发起新的模型执行、不创建 Agent 或会话；组件使用配置继续调用现有会话接口。若后续要提供单独执行入口，需要再定义输入输出和会话归属。

未知编码返回 404，`detail.code=integration_not_found`。运行接口请求未配置项返回 409，`detail.code=integration_not_configured`，提示“该集成尚未配置”。管理目录正常显示空配置。响应禁止缓存。

页面接口示例和 OpenAPI从 `schemas/integration_api.py` 同一份定义生成，避免示例漂移。后续可按需在固定目录增加新的功能定义，仍不需要发布时插数据库行。


## 电子表格助手应用配置

在「集成 → 电子表格助手 → 应用配置 → 添加应用」可在应用输入框按名称、ID 或所属空间搜索。选择可管理的应用后，点「确定」进入详情。详情左侧保存追加提示词，右侧上传 App 专用 Skill。在测试工作台的调试菜单可输入 App ID 或选择已配置的应用。

015 新增 `integration_scoped_settings`。应用配置独立于共用 Agent，作用域键使用已验证的 `space_id/app_id`，企业 ID 用于管理范围和列表筛选。`settings.prompt_append` 最多 4000 字。移除配置会一并移除此 App 的专用包，清空关联 Agent 只清除集成使用的 Agent。

同一 ZIP 可以分别上传到通用 Skill 和不同 App，显示原包名，各自独立更新。历史 `NULL` 和新写入的 `general` 均视为通用包。专用包归当前应用配置所有，使用相同的上传、替换、资源预览和下载流程。Agent 安装包只包含通用包。全量配置备份中的专用包通过 `GET /api/v1/configuration/skills/export` 下载，恢复时核对同 Revision、应用归属、企业权限和包摘要。

运行时按当前用户的平台访问权核验 App 与 Space，每轮只向模型提供应用上下文。仅当前 Agent 为电子表格助手的关联 Agent 且该 App 有配置时，追加该应用的提示词和 Skill。同一业务口径优先采用应用专用内容，通用内容和平台授权照常生效。专用 Skill 的 `load_capability` 使用目录提供的 ID，例如 `app--budget001--monthly-report`。读取资源、执行脚本及 PTC 使用 `skill_code`，例如 `app:budget001:monthly-report`。旧会话使用已保存的 App 与 Space，新选择的调试 App 用于新会话。权限目录暂时无法核验时，本轮使用通用配置。

管理接口以 `/api/v1/integrations/deeptable-assistant/app-configs` 为前缀。

| 接口 | 行为 |
| --- | --- |
| `GET /` | 列出当前账号可管理、已配置的应用 |
| `GET /available` | 列出当前账号可管理的应用候选，含应用、空间和企业标识及名称 |
| `POST /validate` | 验证 App ID 与所属企业 |
| `GET /{app_id}` | 读取已验证的应用及配置 |
| `PUT /{app_id}` | 保存 `config_id`（新配置可空）和 `prompt_append` |
| `DELETE /{app_id}?config_id=…` | 移除应用配置和专用 Skill |
| `/{app_id}/skills` | 与通用 Skill 接口相同的列表、安装和详情操作，权限限定到该应用 |

原组件读取接口 `GET /api/v1/integrations/{code}` 沿用已有响应。应用配置在聊天服务端根据会话 App 选择。
