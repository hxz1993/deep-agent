# DeepAgent 配置备份

这个维护命令把一个 DeepAgent 环境的配置导出为单个 SQLite `.db` 文件。
文件可以直接用 DBeaver 打开，也可以复制后作为本地 DeepAgent 数据库使用。

```bash
python3.12 -m maintenance.config_backup \
  --config ~/.config/deep-agent/test.env \
  --environment test
```

默认输出到仓库相邻目录：

```text
../deep-agent-config-backups/<环境>/
deep-agent-config-<环境>-v<数据库版本>-<时间>.db
```

备份包含完整的当前表结构，以及以下配置表数据：

- `agents`
- `llm_providers`
- `llm_models`
- `model_catalog`
- `mcp_servers`
- `skill_packages`
- `ui_tool_packages`
- `ui_tool_definitions`
- `alembic_version`

`chat_sessions` 和 `chat_session_events` 只保留表结构，不复制会话事件。备份中的
API Key、Token 和 MCP Header 保留原值。命令完成前会自动检查 SQLite
完整性、各配置表行数、数据库版本、模型到Provider的逻辑引用完整性和DeepAgent数据层连接。

备份程序会把全部 ORM 业务表强制归入“复制配置数据”或“只保留结构”两类。以后增加表却没有
同步备份清单时，测试和备份命令会直接失败，避免生成表面成功但缺配置的文件。备份文件内的
`_backup_info.scope_manifest`同时记录本次范围、只保留结构的会话表，以及未内嵌的外部文件类型，
便于脱离仓库后核对备份边界。

`llm_models` 会完整保留上下文窗口、最大输出 Token、输入模态与能力标签；
`llm_providers` 会先于模型复制，并完整保留Provider类型、默认地址模式、明确地址、密钥与启停状态；
`model_catalog` 会完整保留 Lab、标准模型、Provider 组合关系、发布时间以及模态、上下文、
能力和扩展元数据 JSON。目录本身不包含价格信息。

v0.3.2 起备份同时保存 UI 工具包及每个手工 UI 工具的 `package_code`，因此恢复后 Agent 的
整包选择、包内工具目录和引用保护可以保持一致。

v0.3.18 起备份同时保存 `skill_packages` 与 `agents.skills`。其中上传 Skill 只备份平台 File ID、
Hash、Manifest 和资源索引，不包含平台文件服务中的原始 ZIP，因此配置备份本身不是跨环境自包含
的Skill迁移包。需要完整留存或迁移时，应同时在Console Skill页面执行“导出全部”；集合中的每个
`skills/<code>.zip`都可在目标环境通过现有“上传安装”导入。若当前环境中的File ID已经失效，
先在Skill管理页重新提交相同ZIP修复原包，再重新导出。Agent的Skill选择仍保存在配置DB中，
导出的Skill ZIP不携带Agent绑定。

会话标题不再有独立模型配置；首轮标题请求直接复用该轮实际生效的Provider和模型，因此配置备份
无需保存标题专用表或环境变量。

备份工具只负责数据库配置备份，不提供恢复命令。Skill原包的跨环境迁移由独立的单个/全部导出
负责，两者职责不合并，避免把平台二进制文件塞入配置数据库。

## 会话历史归档

会话历史与配置是两个独立备份范围。Revision 007 升级到 008 前必须额外执行：

```bash
python3.12 -m maintenance.session_history_backup \
  --config ~/.config/deep-agent/test.env \
  --environment test
```

默认输出到仓库相邻目录：

```text
../deep-agent-session-backups/<环境>/
deep-agent-sessions-<环境>-v007-<时间>.db
deep-agent-sessions-<环境>-v007-<时间>.db.sha256
```

归档原样保存 Revision 007 的 `chat_sessions`、`chat_messages`、旧上下文投影及会话清单；同时记录
来源方言、Revision、Git Commit、表级/会话级 Canonical Hash 和外部 Artifact 句柄，但不下载平台
文件。命令必须通过 SQLite `integrity_check`、源/归档行数、Pydantic Message解析、孤儿检查与Hash
校验后才会留下最终文件；失败的`.partial-TMP-to-delete`会清理。目录权限为`0700`、文件为`0600`，
正文、凭据和完整Tool Result不写日志。

该归档是以后按指定Session ID编写一次性导入器的只读来源，不是可直接挂载的新数据库，也不提供
通用Restore命令。清空会话不会删除平台File；跨环境恢复仍需单独迁移外部文件。

## v0.3.2 工具包迁移

Alembic `002` 新增 `ui_tool_packages`，给 `ui_tool_definitions` 增加非空外键
`package_code`，并一次性把 Agent 的旧工具编码改为工具包编码。上线前必须用仍停留在 `001`
的代码执行一次配置备份；部署 v0.3.2 并升级到 `002` 后，再用新版备份工具生成第二份备份。

迁移只接受已知的两个 DeepCube 手工 UI 工具及可映射的旧 Agent 引用。发现未归包手工工具、
不存在的 UI 工具引用或异常数据时会在 DDL 前失败，要求管理员先明确归属，不会静默删除。
部署期间不要让未发布的 `002` 本地代码连接共享 Test 数据库。

## v0.3.18 Skill 迁移

Alembic `003` 新增 `skill_packages`，并给 `agents` 增加非空 `skills` JSON。部署前先使用
v0.3.17 备份仍位于 `002` 的配置，并确认 `agents.capabilities` 不存在遗留 `skill:*`；迁移发现
旧引用会在任何 DDL 前失败，避免静默丢失授权。

`003` 降级只允许 `skill_packages` 为空且所有 `agents.skills=[]`。上传 Skill 的 ZIP 位于平台文件
服务，不包含在配置 SQLite 备份中；跨环境恢复需要同时迁移或重新上传平台原包。Test MySQL和
平台文件服务验收步骤见 [`../docs/guides/skills.md`](../docs/guides/skills.md)。

## v0.3.23 文件工具配置清理

Alembic `004` 不增加表或字段，只从显式 `agents.capabilities`列表中移除已废弃的
`builtin:attachment`。它保留其余能力顺序，并保持 `null`和空列表语义；已保存历史中的旧工具
调用不会重写。由于降级无法可靠判断旧Agent是否曾主动选择附件包，`004 → 003`只回退revision，
不会凭空补回该编码。部署前后分别生成配置备份，并在升级后确认Agent的其他能力未变化。

## v0.4.4 预制演示MCP清理

Alembic `005`不增加表或字段。升级会删除已下线的预制演示MCP配置记录，并从所有显式
`agents.capabilities`列表中移除对应能力引用；其他能力顺序、`null`和空列表语义保持不变。
历史会话中的旧Tool Call/Result不重写。`005 → 004`只回退revision，不恢复Server记录或Agent授权。
旧版本配置备份作为隔离SQLite启动时，会先升级到当前head再完成同样清理。

## v0.4.5 预制文档MCP清理

Alembic `006`继续执行纯数据清理：删除已下线的预制文档MCP记录，并从显式
`agents.capabilities`中移除对应引用。迁移保持其他能力顺序以及`null`、空列表语义；降级只回退
revision，不恢复已经删除的Server和授权。历史会话及其中已有的Tool Call/Result保持原样。

## v0.5.6 Provider与模型分层

Alembic `007`新增`llm_providers`，给`llm_models`增加非空、带索引和`RESTRICT`外键的
`provider_id`，并删除模型表原有的`base_url/api_key`。升级会按历史地址、密钥和协议推断并合并
Provider；降级时先恢复模型连接列，再从Provider回填当前地址与密钥。配置备份固定先复制Provider
再复制Model，清理顺序相反，并拒绝生成含孤儿`provider_id`的备份。未发布的`007`只能在隔离
SQLite副本验证，不能连接共享Test数据库。

## v0.6.0 会话事件迁移

Alembic `008`是有意的破坏性会话迁移：删除Revision 007的旧会话、消息和上下文投影，建立新的
`chat_sessions`与`chat_session_events`，不转换旧历史。Provider、Model、Agent、MCP、Skill、
UI Tool和模型目录数据不变；旧的会话标题模型映射由后续Revision 009删除。

共享环境发布顺序固定为：先用007代码生成并验证配置备份和会话归档；构建并推送0.6.0镜像；进入
维护窗口并停止全部旧实例与旧镜像自动扩容；复核Revision和物理表；启动新镜像执行008；核对配置
表行数、新会话表为空和健康接口；执行并删除临时对话/工具/压缩冒烟会话；最后生成Revision 008
配置备份。Migration本身完成清空，禁止再执行无审计的手工`TRUNCATE`。

`008 → 007`只重建空旧表，不从Events逆向恢复旧Message；再次升级008仍然是空Event Store。

## v0.6.0 标题模型配置清理

Alembic `009`只删除已废弃的`auxiliary_model_configs`，不修改Provider、Model、Agent、Skill、
UI Tool、Session或Event数据。开发阶段存在两种Revision 008物理状态：共享Test仍保留旧表，部分
本地SQLite已经受控删除；009会先检查表是否存在，使两种状态都安全收敛到同一结构。降级到008时
恢复空的旧表及其`llm_models.id`外键，重新升级009再次删除。

共享环境仍遵循先备份、部署包含009的镜像、停止旧实例、再迁移和冒烟的顺序；禁止只修改
`alembic_version`或在旧镜像仍运行时提前删表。升级后的配置备份文件名使用`v009`。
