# 数据库迁移、配置备份与初始化

资料核对：2026-11-06，DeepAgent {{DEEP_AGENT_VERSION}} / Head013；目标环境须另读 /info 和 OpenAPI。0.14.4／0.14.5相对0.14.3未增加或改写迁移；运行代码回退与数据库降级分开评估。单Agent迁移使用[Agent安装包](agent-packages-and-migration.md)；本页用于实际DDL、全量配置备份和初始化。源码Head不证明目标数据库或镜像已升级。

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
```

已发布迁移（当前001–013）保持不变。不要用历史同名／错号迁移映射、手工stamp或旧005/007开发期补列办法“修复”当前共享库。历史维护／rebaseline工具仅限其文档规定的原始结构，不能通用补列。003后的逻辑引用由Store／Service事务锁与reference_schema校验，物理外键为空不证明逻辑引用有效。

012新增agent_output_configs及agents的structured_output_enabled（非空默认false）、updated_by（可空）；不添加结果表或Run表。输出事实仍在chat_session_events。升级后核对实际列/表、旧Agent默认关闭、旧会话可读；显式启用需另外满足客户端及完整Schema验收。已有定义/更新人/输出事实时保护性拒绝降级，不自动删除数据换取回退。

012升级到013新增agent_output_configs.allow_text_output Boolean NOT NULL DEFAULT false，不在agents加第二个策略字段。无配置按false；删除定义清策略，关闭总开关保留草稿。配置备份／复制保留策略，历史011/012备份只投影其真实物理字段。013降级到012会删除该列；先检查true草稿与历史冻结mixed策略，并在MySQL首个隐式DDL前预检完整降级路径，不能013已删列后才发现012保护拒绝。

## 验证与交付

1. 使用与来源Revision兼容的代码，按授权准备配置或物理备份，保留无关配置/会话。用户明确不新增备份时遵从范围并说明恢复条件，不私自复制配置。
2. 在隔离SQLite/FileStore/Skill运行目录验升级、允许及拒绝降级、重复启动和受影响Store/API；临时产物标TMP to delete。
3. DDL核对MySQL/PostgreSQL方言；只在可用隔离实例实测。编译通过不声称真库通过，小库耗时不代替生产评估。
4. ORM/Store/Schema、配置协调/引用锁、迁移、备份导入、Console及测试同版本交付；维护发布时核对准确镜像、真实Head/物理结构与受影响API。
5. 固定依赖基础镜像遵从deploy/dependency-images.md；requirements、传递依赖、wheel/ABI和系统库变化单列运维说明，不在启动时pip install绕过。

## 配置备份

在仓库根使用已有虚拟环境及目标私有Profile：

```bash
PYTHONPATH=src .venv/bin/python -m maintenance.config_backup \
  --config ~/.config/deep-agent/test.env --environment test
```

默认仓库外`../deep-agent-config-backups/<environment>/`，记录来源实际Revision；以`db/configuration_transfer.py`的分类与ORM为准。当前配置表包括：

```text
global_settings, llm_providers, mcp_servers, model_catalog,
skill_packages, ui_tool_packages, llm_models, ui_tool_definitions,
agents, agent_output_configs, wecom_applications, channel_accounts, integration_settings
```

配置备份保留原生关闭草稿及开关/定义状态；会话/事件、channel_session_bindings/channel_message_runs和wecom_user_profiles仅建结构，不复制运行数据/成员缓存。Skill元数据只含来源File ID，另需导出原包；Token缓存/进行中任务不备份，OAuth凭据及状态的脱敏规则以当前备份器为准。历史ui_tool_selection不恢复为运行授权。

备份可能含Provider/MCP、Bot/App Secret等敏感配置；物理备份还包含更多原始凭据。文件权限600、目录700，不打印行内容、不入Git。检查SQLite完整性、Revision、表形状/行数与逻辑引用，不能把配置备份当完整业务/会话备份。

## 同Revision新环境初始化

使用当前`docs/guides/configuration-import.md`及`POST /api/v1/configuration/import`：

- multipart的database_file必填；含上传Skill时另传skills_bundle全量原包。
- 默认apply=false只读预检；正式执行相同文件并apply=true/confirm=true。
- 备份/元数据/目标Revision完全一致，双方无会话、目标无活动Run；多实例先协调停流。
- 配置整体事务替换保留主键，Skill目标上传后生成新File ID；不写备份的alembic_version，不负责跨Revision升级/降级。
- 原配置会被替换，目标与摘要须在用户授权范围；安装不确定先回读核实，不重复提交或清空。
- 导入后读回配置、Agent与资源；按任务再做对话冒烟。配置安装、完整Agent ZIP、单Skill ZIP是不同契约。
