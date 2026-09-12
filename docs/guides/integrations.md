# 组件集成配置（数据库 004）

“集成”提供固定功能目录和通用 JSON 配置。当前目录由代码定义，数据库不初始化组或集成配置；只在管理员保存非空配置时新增记录。v0.9.11 引入此功能，数据库结构版本为 004。

| 组编码 | 组名称 | 集成编码 | 集成名称 |
| --- | --- | --- | --- |
| `deeptable` | 电子表格 | `deeptable-assistant` | 电子表格助手 |
| `python-server` | Python Server | `deepcube-assistant` | DeepCube 助手 |

目录名称、图标、归组关系都来自 `integrations/catalog.py`，不作为数据库数据重复保存。读取目录时叠加已有配置，没有记录的集成返回 `settings: {}`、时间戳 `null`。读取永远不新增记录，不需要发布后的初始化操作。

控制台使用普通表格平铺这两项配置，不展示分组；后端目录与数据库结构保持不变。

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
| `GET /api/v1/integrations/{code}/api` | 企业管理员 | 页面接口弹窗的路径、说明和参数示例 |

当前运行接口职责为读取集成配置，不发起新的模型执行、不创建 Agent 或会话；组件使用配置继续调用现有会话接口。若后续要提供单独执行入口，需要再定义输入输出和会话归属。

未知编码返回 404，`detail.code=integration_not_found`。运行接口请求未配置项返回 409，`detail.code=integration_not_configured`，提示“该集成尚未配置”。管理目录正常显示空配置。响应禁止缓存。

接口弹窗和 OpenAPI 示例从 `schemas/integration_api.py` 同一份定义生成，避免示例漂移。后续可按需在固定目录增加新的功能定义，仍不需要发布时插数据库行。
