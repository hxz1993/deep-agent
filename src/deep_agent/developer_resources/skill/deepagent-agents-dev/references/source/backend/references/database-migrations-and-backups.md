# 数据库迁移、配置备份与初始化

资料基线为 DeepAgent {{DEEP_AGENT_VERSION}} / Head 016。运行代码回退与数据库降级分开评估。单 Agent 迁移使用 [Agent 安装包](agent-packages-and-migration.md)。实际迁移前检查目标环境的接口、数据库和镜像。

## 授权与启动边界

普通启动执行Alembic upgrade head。连接共享库或启动含新迁移的代码前，核对检出代码/未提交迁移、Revision图、真实数据库Revision与物理结构、已部署镜像准确SHA；分支名或相同Revision不足以证明兼容。

目标仓库AGENTS要求结构变更独立明确授权，涵盖表/字段/索引/约束、迁移版本、必要性、替代方案及数据规模下的耗时/锁/发布/恢复影响。功能实现、Git发布或产品版本授权不能默认为DDL授权；已有明确覆盖该结构变更的授权不重复询问。确认前只读分析，不启动会自动迁移的进程。不为重复保存结果/回执或罕见异常随意新增表或Runs字段。

- 日常本地沿用已有SQLite/附件及会话，未发布或破坏性迁移在可丢弃SQLite上先验；共享Test不是首次迁移试验场。
- 不兼容DDL、新旧写入进程混跑、数据库比镜像新或同号缺结构时先解决发布兼容。只改静态UI无需启动迁移。
- 显式`local_dev --read-only-preview`只连接已到当前Head的已有库，跳过迁移和写入初始化；写请求/WebSocket拒绝。见[本地运行](local-run-and-api.md)。

## 当前修订图

```text
001 initial_schema
002 wecom_channel_gateway
003 application_references
004 integration_settings
005 nullable_agent_model
006 wecom_applications
007 event_usage
008 channel_source_identity
009 channel_message_operations
010 remove_channel_message_operations
011 channel_webhook_credentials
012 structured_output
013 allow_text_output
014 reasoning_effort
015 integration_app_settings
016 agent_publications
```

已发布迁移（当前 001–016）保持不变。不要用历史同名／错号迁移映射、手工stamp或旧005/007开发期补列办法“修复”当前共享库。历史维护／rebaseline工具仅限其文档规定的原始结构，不能通用补列。003后的逻辑引用由Store／Service事务锁与reference_schema校验，物理外键为空不证明逻辑引用有效。

012新增agent_output_configs及agents的structured_output_enabled（非空默认false）、updated_by（可空）；不添加结果表或Run表。输出事实仍在chat_session_events。升级后核对实际列/表、旧Agent默认关闭、旧会话可读；显式启用需另外满足客户端及完整Schema验收。已有定义/更新人/输出事实时保护性拒绝降级，不自动删除数据换取回退。

012升级到013新增agent_output_configs.allow_text_output Boolean NOT NULL DEFAULT false，不在agents加第二个策略字段。无配置按false；删除定义清策略，关闭总开关保留草稿。配置备份／复制保留策略，历史011/012备份只投影其真实物理字段。013降级到012会删除该列；先检查true草稿与历史冻结mixed策略，并在MySQL首个隐式DDL前预检完整降级路径，不能013已删列后才发现012保护拒绝。

014 增加可空的 `llm_models.reasoning_options` 和 `agents.reasoning_effort`。015 增加 `integration_scoped_settings`、`skill_packages.scope_ref` 及索引，历史空作用域按通用包读取。存在应用配置或专用包时，015 拒绝降到 014。

016 创建 `agent_publications` 和 `remote_agent_connections`。跑过早期内部 016 的环境需核对两张表的实际结构。配置备份保留发布应用范围、远程连接和 Agent 绑定，运行任务、CLI 工作区和 FileStore 文件各自保留在原处。有发布或远程连接记录时，016 拒绝降到 015。

专用 Skill 的 `scope_ref` 存 `app:<App ID>`，集成应用配置的 `settings.skills` 存原包编码。旧 `015` 数据在备份并停写后用 `python -m maintenance.app_skill_ownership --config <配置>` 预检，追加 `--apply` 转换。转换保留内部主键及原包。新版恢复旧备份时会先核验旧清单，再保存新归属和引用。
