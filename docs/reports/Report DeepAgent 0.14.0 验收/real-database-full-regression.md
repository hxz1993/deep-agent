# 真实 MySQL / PostgreSQL 全部可选数据库回归

最终增强夹具后的 **46 / 46 方法全部通过，0 失败、0 skip**。没有修改生产源码，也没有把无数据库全量中的 skip 记为通过；首轮发现的四条旧断言失败和处理依据完整保留在下文。

本专项在最终无数据库环境的 Python 全量启动后另行执行。它不把全量中的数据库 skip 当通过，也不只运行新增 012 场景。复用本机已缓存 `mysql:8.0`、`postgres:16`，两个临时容器仅映射随机 loopback 端口，随机凭据不输出。

按方言准确选择以下 class/method，避免执行另一方言后把“不适用”误计通过：

| 套件 | MySQL 方法数 | PostgreSQL 方法数 |
| --- | ---: | ---: |
| channel_reference_transactions | 9 | 9 |
| logical_references | 8 | 8 |
| mcp_reference_transactions（含真实父记录 row lock） | 3 | 3 |
| reference_migration_databases | 1 | 1 |
| configuration_import_databases | 1 | 1 |
| structured_output_databases | 1 | 1 |
| 合计 | 23 | 23 |

渠道使用 `channel_fk_test`，逻辑引用和 MCP 顺序共用专用 `runtime_fk_test`，迁移使用 `tmp_deepagent_migration_*`；导入与 012 测试自行创建并清理独立子库。不存在两个测试进程并行 drop 同一个 schema。

## 首轮结果（保留失败，不计最终通过）

MySQL：23 方法，21 通过、2 失败、0 skip，测试约 48.3 秒（含启动约 62.1 秒）。PostgreSQL：23 方法，21 通过、2 失败、0 skip，测试约 17.9 秒（含启动约 24.0 秒）。两方言均只有同样两条旧渠道断言失败：

1. `test_claim_and_delete_keep_existing_channel_history` 仍要求旧提示文案“保留配置”，实际返回现有统一删除预检提示“删除会同时清理关联记录，请确认影响范围后再次删除”。已确认请求没有完成删除；是否调整夹具由主执行审查现行产品契约，不能直接删断言凑通过。
2. `test_unicode_title_round_trip_with_new_session` 仍把 `peer_metadata.display_name` 当标题来源，实际现行可信账号资料来源返回 `wecom｜User｜fixture-peer`。该测试需要核对已确认的命名来源，再补正确的 Unicode 资料夹具；不能以删除 emoji 断言代替真实字符集往返。

既有逻辑引用、MCP 竞态/真实锁、迁移、导入和新 012 场景在两方言中均通过。最终结论须以根会话处理后完整重跑 46 方法的结果为准，首轮失败不隐藏。

## 旧测试修复依据

主执行独立核对了真实旧基线 `552b7b2`：其中 `db/deletion.py` 已使用 `DeletionConfirmationRequired` 及统一确认提示（历史提交 `9166cb4`）；现行企微标题取持久账号资料的实现也已存在于该基线（历史提交 `e075aa4`），不是本次 012 引入的新行为。

因此只调整两条落后的旧场景，未修改生产源码，也未降低原测试目的：

- 删除竞态实际先保存一组 Unicode 会话原始消息，再并发 claim/删除。要求具体 `DeletionConfirmationRequired`、64 位 token、正确绑定/会话/运行影响；锁先后允许预检 run_count 为 0 或 1，之后重新计算必须是 1。账号、绑定、claim、会话与原始消息均独立回读仍在。避免把旧提示文案误作删除安全性。
- Unicode 场景实际写入 `WecomApplication`、未过期 `WecomUserProfile` 并把渠道配置关联应用；可信持久名称为中文和多个 emoji，消息 display_name 故意不同。标题必须精确采用可信中文 emoji 与原 peer ID，不能采用不可信昵称；再次 resolve 不丢元数据、不改标题，数据库 profile 和 session 独立回读等值。

本专项启动时的无数据库环境全量已结束：运行 3,105 方法，3,045 通过、60 skip、约 639.3 秒。本专项增强后的完整 46 方法另行执行，不冒称全量已执行这 46 个 opt-in 场景。

按主执行对该轮全量日志的分类，60 个 skip 分别是：

| 分类 | 数量 | 本次处理 |
| --- | ---: | --- |
| opt-in MySQL / PostgreSQL 场景 | 46 | 本报告最终完整 46 / 46 真实执行通过、0 skip，覆盖这组缺口 |
| SQLite 无服务端父记录 row lock | 1 | 该方言不适用；对应行为已在 MySQL / PG 真实行锁场景执行，不把 SQLite skip 算通过 |
| Nacos 拓扑集成 | 5 | 本机未搭建该外部拓扑，保留未执行，不伪装已通过 |
| macOS 平台能力限制 | 8 | 保留平台限制，不伪装当前主机执行了相应 Linux 等平台场景 |

以上分类不等于把 skip 改成 pass；它区分了另行真实补验、方言不适用和未执行环境。最后封板全量的实际发现数/结果和发布门禁由主验收报告签发。

## 完整复跑

| 方言 / 固定镜像 | 实际运行 | 通过 | 失败 | skip | 测试耗时 | 含容器启动耗时 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| MySQL 8 / `mysql:8.0` | 23 | 23 | 0 | 0 | 50.678 秒 | 61.539 秒 |
| PostgreSQL 16 / `postgres:16` | 23 | 23 | 0 | 0 | 17.648 秒 | 23.927 秒 |

六套数据库用例均实际执行，包括既有渠道九项、全部逻辑引用、MCP 父记录行锁阻塞/解除与真实 011→012/降级/恢复。执行器汇总所有子命令 returncode，存在失败时退出非零；最终退出 0。

只有测试模块 `test_channel_reference_transactions.py` 的上述两个场景及验收报告发生调整，生产版本不变。两个容器、随机凭据文件与临时子库均在 finally 清理；最终 `docker ps` 为空。临时日志和运行器已在结论进入本报告后删除，保留可重复的正式测试源码。

每轮执行器在 finally 删除容器和随机凭据文件；首轮结束已核对 `docker ps` 为空。没有改动任何共享库、稳定本地数据库或生产 DDL。
