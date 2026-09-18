# 数据库 003：由应用维护引用

v0.9.7 使用 Alembic `003`。移除原有 10 条外键，保留关联 ID、主键、唯一约束、索引和接口；客户端无需修改。

## 运行规则

| 关系 | 写入与删除规则 |
| --- | --- |
| 模型 → Provider | 验证 Provider；删除 Provider 时先确认其模型没有 Agent 引用，再同事务删除模型和 Provider |
| Agent → 模型 | 验证模型；被 Agent 使用的模型不能删除 |
| 会话 → Agent | 验证 Agent；v0.10.9 起确认本人、他人及总会话数量后，级联删除 Agent、会话和事件 |
| UI 工具 → 工具包 | 验证包；包有工具或 Agent 引用不能删除 |
| 事件 → 会话 | 写事件持有会话锁；删除会话同事务删除事件 |
| 渠道账号 → Agent | 验证 Agent；有渠道引用的 Agent 不能删除 |
| 渠道绑定 → 账号 | 验证账号；v0.10.9 起确认绑定及运行记录数量后，同事务清理账号与台账，保留 Agent 和聊天历史 |
| 渠道绑定 → 会话 | 绑定验证会话；删除会话同事务将绑定 session_id 置空 |
| 渠道运行 → 账号 | 接收、重试均校验账号 |
| 渠道运行 → 绑定 | 验证绑定存在且属于账号 |

`channel_message_runs.session_id` 等原有无外键的历史字段保持弱引用，不增加强制约束，删除渠道仅清理运行台账，不删除对应聊天历史。

关联检查和写入必须在同一事务。先锁父记录再锁子记录，同类多条记录按 ID 排序；修改引用先锁旧、新父记录，再锁子记录重新核对。MySQL 使用锁定读取避免旧快照漏掉新引用。SQLite 使用 `BEGIN IMMEDIATE`。

普通写事务持有 `alembic_version` 行共享锁，配置导入持独占锁。平时允许并发写入，整体配置替换期间阻止其他新版实例写入。维护程序不能绕过公共事务入口并发改库。

## 发布与回退

1. 完整备份数据库。配置 `.db` 备份不含会话历史，不能替代全库备份。
2. 停止旧 API 和 Gateway 写入，包含其他维护程序。
3. 运行 `PYTHONPATH=src alembic upgrade head`，核验 Revision `003`、目标十条 FK 已移除、记录和逻辑引用有效。
4. 启动 v0.9.7，验证配置、聊天、历史续聊和渠道。

不能在外键已移除后继续使用旧版写入，也不能新旧版混跑。迁移需要在线连接读取实际约束名称，不支持 `003` 离线 SQL；已发布 `001`、`002` 未修改。

迁移前发现孤儿引用会报告表、关系和数量并停止，不自动清理。运维已移除部分或全部 FK 时可继续升级。MySQL DDL 不是整批事务：中断后保持停写，排除问题后重跑，不能手工修改 Revision；SQLite/PostgreSQL 失败回滚。降级先停写，运行 `alembic downgrade 002`，引用预检通过后恢复原十条外键及删除规则，再启动旧版。

此次迁移不转换字符集。MySQL 仍要求连接及业务文本列支持 `utf8mb4`；已有乱码不会因删除 FK 自动恢复。

## 配置备份与初始化

源库实际为 `002` 就导出 `002`，升级后才导出 `003`。导入保持同版要求，不转换旧 `.db`。配置依赖顺序在 `configuration_transfer.py` 明确声明，新表须同时维护顺序和引用审计。

当前库的 `foreign_key_check` 为空没有证明作用。维护检查使用 `deep_agent.db.reference_schema.assert_logical_references`，传入 SQLAlchemy 同步 Connection；异步连接通过 `run_sync` 执行。历史维护命令仍受各自 Revision 限制。

v0.9.7 的历史回归记录可从 Git 恢复；当前约束以本文、数据库迁移与自动化回归为准。

## v0.10.9 管理删除确认

Agent、渠道删除仍要求控制台管理员。无关联记录可直接删除；存在关联记录时，首次 `DELETE /api/v1/agents/{id}` 或 `DELETE /api/v1/channel-accounts/{id}` 返回 HTTP 409：

```json
{"detail":{"code":"delete_confirmation_required","message":"删除会同时清理关联记录，请确认影响范围后再次删除","impact":{"session_count":2,"own_session_count":1,"other_session_count":1,"binding_count":0,"run_count":0},"confirmation_token":"..."}}
```

Console 保留初次确认，再显示上述数量、不可撤销说明及红色删除按钮，默认聚焦取消。确认后重新 DELETE，JSON body 为 `{"confirmation_token":"..."}`。token 绑定资源、当前用户及实际关联记录集合；事务内再次核对，范围改变重新返回 409，不能自动确认重试。仍绑定渠道的 Agent 需先改绑或删除渠道，仅停用不能解除引用。

确认删除先阻止本实例同资源新运行、等待相关聊天或手动压缩停止及事件落盘，再执行数据库事务。超时返回 409，本次不删除；普通会话删除也等待相关运行结束。此运行协调沿用单 API 实例部署边界，不能保证跨 Pod 取消已经执行的工具副作用。MySQL/PostgreSQL 的父子记录引用仍由数据库事务锁维护。

Agent 清理会话及 Events；渠道清理账号、绑定与运行台账，保留 Agent、聊天历史。引用规范中的 `ondelete` 保留旧外键恢复所需的历史元数据，本版是显式的应用层级联，不修改旧迁移，也不新增 Revision。
