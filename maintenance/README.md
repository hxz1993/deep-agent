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

`chat_sessions` 和 `chat_messages` 只保留表结构，不复制会话数据。备份中的
API Key、Token 和 MCP Header 保留原值。命令完成前会自动检查 SQLite
完整性、各配置表行数、数据库版本和 DeepAgent 数据层连接。

`llm_models` 会完整保留上下文窗口、最大输出 Token、输入模态与能力标签；
`model_catalog` 会完整保留 Lab、标准模型、Provider 组合关系、发布时间以及模态、上下文、
能力和扩展元数据 JSON。目录本身不包含价格信息。

这个工具只负责备份，不提供恢复或跨环境迁移命令。
