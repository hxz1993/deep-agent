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
- `llm_models`
- `model_catalog`
- `mcp_servers`
- `skill_packages`
- `ui_tool_packages`
- `ui_tool_definitions`
- `auxiliary_model_configs`
- `alembic_version`

`chat_sessions`、`chat_messages` 和 `chat_context_checkpoints` 只保留表结构，
不复制会话或上下文投影数据。Checkpoint 是可重建的派生数据，不属于配置备份。备份中的
API Key、Token 和 MCP Header 保留原值。命令完成前会自动检查 SQLite
完整性、各配置表行数、数据库版本和 DeepAgent 数据层连接。

`llm_models` 会完整保留上下文窗口、最大输出 Token、输入模态与能力标签；
`model_catalog` 会完整保留 Lab、标准模型、Provider 组合关系、发布时间以及模态、上下文、
能力和扩展元数据 JSON。目录本身不包含价格信息。

v0.3.2 起备份同时保存 UI 工具包及每个手工 UI 工具的 `package_code`，因此恢复后 Agent 的
整包选择、包内工具目录和引用保护可以保持一致。

v0.3.18 起备份同时保存 `skill_packages` 与 `agents.skills`。其中上传 Skill 只备份平台 File ID、
Hash、Manifest 和资源索引，不包含平台文件服务中的原始 ZIP，因此配置备份不是跨环境自包含的
Skill 迁移包。恢复或跨环境发布前必须另行确认对应 File ID 在目标平台可下载。

备份工具只负责备份，不提供恢复或跨环境迁移命令。

## v0.3.0 数据库基线重整

v0.3.0 只保留 Alembic `001` 完整基线。旧库从 `023` 切换前先只读校验：

```bash
python3.12 -m maintenance.rebaseline_database \
  --config ~/.config/deep-agent/test.env \
  --expected-revision 023 \
  --report '/path/DeepAgent-schema-before-v0.3.0-TMP-to-delete.json'
```

校验会检查九张业务表的字段、类型族、空值、默认值、主键、唯一约束、外键和必要索引，
并只输出行数与内容哈希，不输出 API Key、Header 或业务行内容。检查通过且旧服务不会再
重启后，执行：

```bash
python3.12 -m maintenance.rebaseline_database \
  --config ~/.config/deep-agent/test.env \
  --expected-revision 023 \
  --stamp --yes \
  --report '/path/DeepAgent-schema-after-v0.3.0-TMP-to-delete.json'
```

命令内部使用 Alembic `stamp --purge 001`，并再次验证 stamp 前后表结构、行数与内容哈希
完全一致。它不创建、删除或改写业务表。数据库 stamp 为 `001` 后，旧版 `023` 镜像不得
重启或扩容；应尽快部署 v0.3.0。

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
