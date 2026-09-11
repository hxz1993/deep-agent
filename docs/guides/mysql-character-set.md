# MySQL 字符集部署要求与 Emoji 保存排查

DeepAgent 的 MySQL 部署应使用 `utf8mb4`，保证用户输入、模型回复、工具参数和结果中的
完整 Unicode 字符可以无损存取。要求覆盖应用连接、数据库和表的默认字符集，以及已有文本字段。
MySQL 的 `utf8`（`utf8mb3`）不支持部分 Emoji 和扩展汉字，不能作为完整 Unicode 的部署配置。

本文是部署要求与人工检查指引；v0.9.6 尚未实现启动时的字符集检查或自动转换。
不要把其他项目或 MySQL 新版本的默认配置当成当前环境实际生效的配置。

## v0.9.6 的实际影响

字符集正确时，Emoji 可以正常使用。字符集不兼容时，写入可能直接失败，也可能发生字符替换；
不能承诺“只是显示问号，不影响功能”。

已观察到的故障链路是：回复先写入，部分 Emoji 在数据库读出后变成 `?`，后续增量保存发现
已有内容与原消息不同，触发不可变消息校验，最终返回 `Unable to persist completed message`。
诊断日志为 `message_persistence_failed`，异常类型为 `ValueError`。这表示已有部分数据保存，
但本轮没有正常完成；同一错误也可能有其他原因，不能只凭提示判断为字符集问题。

这条一致性校验从 v0.8.5 已存在，v0.9.6 没有新增。旧版本未收到投诉不能证明没有字符损失。
普通中文、英文正常也不能证明完整 Unicode 正常；如果损坏的是代码、工具参数或查询条件，
还可能影响后续执行。不得通过关闭一致性校验或忽略保存错误来掩盖数据损失。

“有些 Emoji 正常”不能证明已修复：例如 `✨`、`☕` 位于基本多文种平面，三字节字符集也能保存，
而 `👋`、`📊`、`🙂` 需要四字节。仅从应用接口读出问号，不能区分写入时替换还是读取连接转换；
运维需结合原始字段字节与连接设置确认。流式页面仍有表情不代表重新读取历史也正确。

## 运维只读检查

选择 DeepAgent 实际使用的数据库，再执行下列查询。连接参数必须在应用实际连接中检查，
运维客户端的 `@@SESSION` 值不能代替应用连接；同时核对驱动和数据库代理的连接设置。

```sql
SELECT VERSION() AS db_version, DATABASE() AS db_name,
       @@character_set_server AS server_charset,
       @@character_set_database AS database_charset,
       @@SESSION.character_set_client AS client_charset,
       @@SESSION.character_set_connection AS connection_charset,
       @@SESSION.character_set_results AS results_charset,
       @@SESSION.sql_mode AS sql_mode;

SELECT TABLE_NAME, TABLE_COLLATION
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'
ORDER BY TABLE_NAME;

SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, CHARACTER_SET_NAME, COLLATION_NAME
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND CHARACTER_SET_NAME IS NOT NULL
ORDER BY TABLE_NAME, ORDINAL_POSITION;
```

重点检查 `chat_session_events.payload` 和 `search_text`，并检查其他存放名称、提示词、配置和
工具内容的文本字段。事件 `payload` 在当前实现中使用文本 JSON，并非 MySQL 原生 JSON 列。
应结合字段用途处理结果，不能把有意使用二进制类型的字段判断为错误。

## 修复与验收

### 外键阻止整表转换时

这是维护时的 DDL 依赖问题，不需要永久取消外键。当前 v0.9.6 的模型和迁移定义共 10 条外键；
线上实际名称、规则和数量以 `SHOW CREATE TABLE` / `information_schema` 为准。
字符串外键两端的字符集和排序规则需保持兼容，不能只转换关联的一侧。

一次性维护应按以下顺序执行：

1. 停止 API、渠道网关及其他写入方，备份数据和受影响表的 `SHOW CREATE TABLE`。
2. 盘点 DeepAgent 表及外部表对它们的引用，保存完整外键恢复语句，保留原有
   `ON DELETE` / `ON UPDATE` 规则。当前应用表清单见下方；共享数据库不能无差别转换其他业务表。
3. 如果外键阻止转换，临时删除受影响的外键约束，保留 ID 字段、主键及所需索引。
   在关联两端统一转换实际文本字段，并设置数据库和表的默认字符集。
4. 校验引用完整性后按原定义重建外键；外键恢复失败时继续保持停写并排查，不带着缺失约束恢复服务。
5. 核验应用驱动、代理、连接协商和结果字符集，重建应用连接，完成下方真实会话验收。

MySQL 文档也允许在特定整表转换场景临时关闭 `foreign_key_checks` 后转换关联两端，但这不是通用
的一条修复命令，重新开启检查不会自动扫描期间产生的全部孤儿记录。维护方案需结合实际 MySQL
版本和错误选择，不能依赖全局关闭外键来绕过问题。

仅调整 `chat_session_events.payload`、`search_text` 这类非外键文本字段，可以避免修改 ID 关联列；
这属于限定范围修复，不代表其他提示词、配置和文本字段已全部统一。

当前应用表：`llm_providers`、`llm_models`、`model_catalog`、`agents`、`global_settings`、
`skill_packages`、`mcp_servers`、`ui_tool_packages`、`ui_tool_definitions`、`chat_sessions`、
`chat_session_events`、`channel_accounts`、`channel_session_bindings`、`channel_message_runs`。

### 转换注意事项

1. 核对连接和现有字段后，由运维制定转换方案。连接及业务文本应支持 `utf8mb4`；排序规则另按
   数据库版本及现有唯一键语义选择，不要求统一成某个特定的 `utf8mb4_*` 排序规则。
2. 转换已有表前备份，并评估索引长度、唯一键、字段类型变化、锁表和维护窗口。
   仅修改数据库或表的默认字符集不会转换已有字段；仅设置连接字符集也不能修复字段限制。
3. 应用连接配置调整后重建连接池或重启应用。不要只在运维 SQL 客户端执行 `SET NAMES` 后就认为
   应用已修复。v0.9.6 不提供专门的字符集环境变量，不能填写一个未被实现的变量充当配置。
4. 用独立验收会话测试中文、英文和 `👋📊🙂`，确认本轮正常结束；刷新页面重新读取历史，
   核对文字无替换，再继续追问，确认没有新增保存失败日志。仅看流式输出正常不算验收通过。

已经被替换成 `?` 的历史内容不会因修改字符集自动恢复，需要可靠的原始数据。
不要自动重放可能有副作用的工具来重建历史。

参考：[MySQL Unicode 支持](https://dev.mysql.com/doc/refman/8.0/en/charset-unicode-utf8mb4.html)、
[连接字符集](https://dev.mysql.com/doc/refman/8.0/en/charset-connection.html)、
[已有表转换](https://dev.mysql.com/doc/refman/8.0/en/alter-table.html)。
