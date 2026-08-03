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
