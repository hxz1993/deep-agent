# 场景配置

控制台「场景」用于为电子表格助手、DeepCube 助手等业务场景选择 Agent。电子表格助手还支持按应用追加提示词和 Skill。旧版控制台的入口名为「集成」，接口仍使用 `/api/v1/integrations` 及原有编码。

当前目录由代码定义，数据库只在管理员保存非空配置时新增记录。v0.9.11 引入此功能，对应迁移 004。

| 组编码 | 组名称 | 集成编码 | 集成名称 |
| --- | --- | --- | --- |
| `deeptable` | 电子表格 | `deeptable-assistant` | 电子表格助手 |
| `python-server` | Python Server | `deepcube-assistant` | DeepCube 助手 |

目录名称、图标、归组关系都来自 `integrations/catalog.py`，不作为数据库数据重复保存。读取目录时叠加已有配置，没有记录的集成返回 `settings: {}`、时间戳 `null`。读取永远不新增记录，不需要发布后的初始化操作。

「场景配置」左侧显示场景列表，右侧显示所选场景的配置和接口示例。点击「关联 Agent」打开下拉菜单，选择或清空 Agent 后点击「保存」。

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
| `POST /api/v1/integrations/deeptable-assistant/app-configs/validate` | Console 管理员且可配置该 App | 传入 `{"app_id":"budget001"}`，验证有效应用并返回应用、空间和企业 ID |

当前运行接口职责为读取集成配置，不发起新的模型执行、不创建 Agent 或会话；组件使用配置继续调用现有会话接口。若后续要提供单独执行入口，需要再定义输入输出和会话归属。

未知编码返回 404，`detail.code=integration_not_found`。运行接口请求未配置项返回 409，`detail.code=integration_not_configured`，提示“该集成尚未配置”。管理目录正常显示空配置。响应禁止缓存。

页面接口示例和 OpenAPI从 `schemas/integration_api.py` 同一份定义生成，避免示例漂移。后续可按需在固定目录增加新的功能定义，仍不需要发布时插数据库行。


## 电子表格助手应用配置

在「场景 → 电子表格助手 → 应用配置 → 添加应用」左侧选择企业，右侧按应用名、空间名或 ID 搜索；选「全部企业」可跨企业搜索。选中应用后点「确定」进入详情，左侧保存追加提示词，右侧上传 App 专用 Skill。外层列表只显示已保存且有权配置的应用，企业筛选从这批记录生成。切换页签或返回时复用列表，应用保存、移除或专用 Skill 数量变化后同步相应行。需要重新读取平台权限和配置时，点击「添加应用」旁的刷新图标。在测试工作台的调试菜单可输入 App ID 或选择已配置的应用。

进入 Console 仍需企业管理员身份。能管理哪一个 App 由目标 App 的平台权限决定：其所属企业管理员、所属空间管理员、该 App 的管理员或开发者均可配置；普通 App 访问权不够。配置备份导入和全量 Skill 导出仍按原企业管理员权限核验。

已配置列表只查有相关配置的企业。添加应用时并行读取平台企业范围和完整候选目录，再按企业批量核验配置权限。弹窗短时间重开可复用候选，刷新列表或离开场景页面后重新读取。弹窗确定后直接读详情，详情和每次写入仍由服务端核验目标 App；App 绑定 API Key 始终限定到其绑定应用。

应用配置独立于共用 Agent。作用域键是平台验证后的 `space_id/app_id`，企业 ID 用于核验和筛选。
`settings.prompt_append` 最多 4000 字，`settings.skills` 存当前集成启用的原包编码。上传同时保存 App 包和引用，保存提示词保留引用。移除配置会删除其独占的包，共享包继续供其他引用使用。清空关联 Agent 只清除集成使用的 Agent。

Skill 包直接归 App，`skill_packages.scope_ref` 存 `app:<App ID>`。同一 ZIP 可以分别上传到通用入口和不同 App，显示原包名，各自独立更新。历史 `NULL` 和新写入的 `general` 均按通用处理。应用详情支持上传、替换、预览和下载专用包。Agent 安装包只包含通用包。

新 ASCII 包的内部键为 `app-pkg--<App ID>--<包编码>`，Unicode 包使用哈希键。已安装的旧包保留原主键、文件和缓存路径。已有 `015` 数据按[应用 Skill 数据转换](../../src/maintenance/README.md#应用-skill-数据转换)处理。全量配置备份中的专用包由 `GET /api/v1/configuration/skills/export` 导出，恢复时核验同 Revision、App 归属、企业权限和包摘要。

运行时按当前用户的平台访问权核验 App 与 Space。当前 Agent 为电子表格助手的关联 Agent 且该 App 有配置时，按配置所属企业查询权限，校验通过后追加该应用的提示词及引用的 Skill。同一业务口径优先采用应用专用内容，通用内容和平台授权照常生效。专用 Skill 的 `load_capability` 使用目录提供的 ID，例如 `app--budget001--monthly-report`。读取资源、执行脚本及 PTC 使用 `skill_code`，例如 `app:budget001:monthly-report`。旧会话使用已保存的 App 与 Space，新选择的调试 App 用于新会话。权限目录暂时无法核验时，本轮使用通用配置。排查可查看 `integration_app_context` 诊断记录中的状态与耗时。

管理接口以 `/api/v1/integrations/deeptable-assistant/app-configs` 为前缀。

| 接口 | 行为 |
| --- | --- |
| `GET /` | 列出当前账号可管理、已配置的应用 |
| `GET /available` | 列出当前账号可管理的应用候选，含应用、空间和企业标识及名称 |
| `POST /validate` | 兼容旧客户端，验证当前账号能否配置指定 App；新版选择器不用这个请求 |
| `GET /{app_id}` | 读取已验证的应用及配置 |
| `PUT /{app_id}` | 保存 `config_id`（新配置可空）和 `prompt_append` |
| `DELETE /{app_id}?config_id=…` | 移除应用配置及其独占 Skill |
| `/{app_id}/skills` | 与通用 Skill 接口相同的列表、安装和详情操作，权限限定到该应用 |

原组件读取接口 `GET /api/v1/integrations/{code}` 沿用已有响应。应用配置在聊天服务端根据会话 App 选择。
