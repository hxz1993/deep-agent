# 结构化输出 Schema 快照清理与完整回归

日期：2026-10-23。基线：`test` / `3facbbb78d5354a406ee399d9dc1cc14626d6ecc`，保留实施前全部未提交工作。版本保持 `0.14.3`，数据库 Revision 保持 `013`。本轮快照清理及本地回归已完成，保持待审；没有提交、推送、发布或通知其他会话。原有 UI 工具重复行与旧运行时接续限制见末节，未将其隐瞒为“所有场景无问题”。

## 实际改动与保留边界

执行链路改为：每个 Run 从当前 Agent 配置读取一次 → 在内存构造 Output Type 和校验器 → 执行 → 保存真实消息、调用及结果。UI 工具暂停后的新 Run 重新读取当前配置；不支持恢复旧配置版本。

| 位置 | 实际删除或调整 |
| --- | --- |
| `agent/structured_output.py` | 删除 `deepagent_output_contract` / `deepagent_output_contract_ref` 常量、请求引用写入、来源事件状态及快照摘要匹配；保留当前配置计算的 `schema_digest` |
| `agent/runner.py` | 删除首个用户消息的完整配置快照写入；删除续跑扫描、解析和验证旧快照／引用的分支；普通执行和续跑共用当前配置加载入口 |
| `db/conversation.py` | 接受事务不再查询用户事件中的 Schema；Run 通过不落库的 `output_root_is_object` 参数说明 SDK `response` 包装方式 |
| `console/trajectory.js` | 删除快照提取、配置解包猜测及 Schema 页签；输出详情固定为 Summary / Output / Raw，成功展示原生输出，失败候选展示真实源参数 |
| Console 资源入口 | 同步缓存引用，刷新后加载 `20261023-output-no-snapshot1` 资源 |

仍然保留：完整 Schema 校验、复杂约束、真实输出调用与 SDK 确认、源参数匹配、会话／所有者／Run／源 Part 关联、拒绝与未分发门禁、原子接受、幂等、提交后断线处理、`schema_digest`、请求和格式预算、尝试序号，以及三种输出模式。普通文字不会伪造接受事实；结构化字符串依然按输出调用分支识别。

此次没有改动配置表、开关、完整定义、允许文本配置、DB 结构／迁移、依赖、版本、安装包格式。历史 `instructions`、System Prompt、工具定义装配、动态工具通知、业务上下文、附件、计划、Usage、耗时、消息关联和文字展示标记保持原样。历史事件不删除、不重写、不按新配置重新校验。

审查中看到的计划执行状态、MCP 检查缓存、安装预检摘要、配置备份和迁移降级保护，均与本次输出 Schema 运行快照不同，未扩大清理。012／013 迁移中的旧快照检测用于阻止不安全降级，并非当前 Run 的恢复依赖，原文件保留。

实施前已有的传输解码、失败部分响应镜像、迟到助手事实替换、轨迹顺序、共享 JSON 卡片及诊断标题修改均保留，纳入本轮测试；没有将这些既有修改归为本次快照清理新改动。

## 公共协议对照

基线使用真实 Git 归档加实施前已有改动补丁；额外复制当时尚未跟踪、此轮未改变的 `http_transport.py`，并记录哈希。前后分别启动独立进程采集隔离 SQLite 夹具，未用当前实现伪装旧基线。

| 验证 | 结果 |
| --- | --- |
| HTTP / SSE | 38 组：关闭 2、严格 16、混合 20；采集范围业务差异 0 |
| Live | 6 组、12 条观察／重放流；断开观察者不重跑生产者，身份一致 |
| UI Pending → Resume | 前后基线均通过，公共输出、状态及工具调用身份一致 |
| History | 完整响应逐项比较：类型、原生值、正文、状态、顺序和输出身份一致 |
| 关联断言 | 前后每侧 420 条额外断言，检查 output/final/Event/History 的 output_id、源事件存在、接受发布顺序 |

归一化仅处理生成的 UUID、时间／耗时及已明确删除的快照元数据，保留 ID 之间的关联。HTTP 比较核心最终字段；SSE / Live 比较公共文字、思考、输出、final、工具、错误及取消帧，临时 context 包络不计入协议零差异结论；持久镜像通过完整 Events 对比。最初 7 处不透明重试历史指纹因 SDK 随机 tool-call UUID 不同而变化；在合成夹具内固定该随机输入后重跑，7 处也一致，没有简单忽略指纹字段。

客户端继续通过 `output_info` / History 类型识别结构化输出，不需要获取 Schema 或新增过滤逻辑。新原始 Events 的配置快照和引用省略属于预期变化；业务事件及普通 History 类型不改。

## 历史、暂停和回滚兼容

| 组合 | 结果与边界 |
| --- | --- |
| 有快照／无快照／只有引用／损坏快照的旧历史 | 定向集成覆盖，展示与续跑不依赖旧配置元数据 |
| 完整 Message、分 Part 和混合记录 | 原投影规则保持；类型、顺序、身份及去重回归 |
| 实际已发布 `3facbbb` 读取新版完成历史 | 实际旧源码投影验证对象、数组、字符串、整数、布尔、null 共 6 种原生值，完整 History 逐字段一致；此项为旧投影源码实测，不另称旧 HTTP 网关实测 |
| 旧运行时创建 Pending → 新版独立进程续跑 | 通过：配置读取 1 次，原失败保留，最终 attempt=2，旧事件不改写，新记录没有快照 |
| 新版 Pending → 已发布旧运行时续跑 | **不兼容**：旧 Runner 视作 legacy Pending，不读取配置、没有输出工具、允许普通文字，可能使严格 JSON 降为文字 |
| 新 Runner／隔离进程重启续跑 | 定向测试覆盖；从真实记录恢复请求数、失败次数与尝试序号 |

部署和回滚限制：不能由旧 worker 接手新版 Pending；回滚前必须结束或取消这些 Pending，并避免新旧实例混跑接续。此限制已用实际旧源码与独立进程验证；不以重新引入快照解决。测试旧源码与对应 Git 对象逐字节／哈希核对，临时源码、SQLite 和子进程已清理。

## 测试结果台账

以下分组含重叠执行，不能相加作为唯一测试总数。定向组先运行，再运行全量；数据库专项的真实执行单独列出，默认环境的 skip 不当作真实通过。

执行方式：定向门禁通过后，Python 全量、真实数据库、正式安装及只读差分等独立组并行执行，并非机械串行跑完全部组；安装矩阵对源代码起止指纹做一致性核对。最终 Console 全量另行完整保存日志，便于复查既有 UI 缺陷与未新增回归的边界。

| 组别 | 结果 | 说明 |
| --- | --- | --- |
| 清理前对照定向组 | 15 通过 | 固定旧基线行为 |
| 后端快照清理定向组 | 84 个唯一方法通过 | 损坏／悬空快照、无快照、一次加载、预算恢复、进程接续、接受门禁、包装根值保真；后续复测 54 与 4 项包含重叠，不重复累计 |
| Console 全量 | 911 通过 | 英文页签、Raw、真实源参数、原生 false/0/null、共享 JSON、迟到事实与卡片 |
| Python 全量最终轮 | 3,151 次执行：3,089 通过，62 跳过，0 失败／错误 | 860.367s；3,064 个唯一方法，3,002 个通过、62 个跳过；87 次重复来自测试发现时导入的同名 TestCase，不重复累计为唯一覆盖；825 个 src/tests Python 文件起止指纹完全一致 |
| MySQL 8.0 真实数据库专项 | 24 通过，0 失败，0 跳过 | 渠道引用 9、逻辑引用 8、MCP 3、迁移 1、配置导入 1、012 输出 1、013 文本策略 1 |
| PostgreSQL 16 真实数据库专项 | 24 通过，0 失败，0 跳过 | 同上；独立容器和回环端口 |
| 真实旧版 HTTP 安装兼容 | 2 个方法通过，0 跳过 | 实际 `552b7b2`（0.14 前）TCP 服务和当前源码；正式导出 ZIP、预检、安装、回读；12 个旧规则组与 6 个混合策略组，不模拟旧解析器；共用本机现有依赖环境，未复刻旧发布镜像 |
| Agent 安装全组合矩阵 | 25,600/25,600 通过，0 失败，0 跳过 | 1,176,960 条断言、3 worker、2221.08s；无截断，`full_matrix_passed=true`、`source_unchanged=true` |
| 重启后真实 Provider | 9/9 通过 | DeepSeek；HTTP / SSE、三模式、结构化字符串、null、普通工具、严格 UI 续跑、混合 UI 文字续跑；首轮自动标题与主请求并发 |
| 浏览器真实 UI 暂停续跑 | 通过 | 隔离 Agent，实际 confirm 确认后得到 `{ok:true,score:3}`，仅一个最终结果 |

执行入口：`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v`；`node --test tests/frontend/console/*.test.mjs`。私有完整日志分别为 `TMP_to_delete_python_full.log`、`TMP_to_delete_console_full.log`。最终只读审查未发现本轮清理新增的校验、来源、ACK、事务或预算门禁遗漏；审查不替代运行测试。

安装全组合覆盖 upload/builtin、create/overwrite、Skill 5 类、UI 8 类、Builtin 4 类、MCP 4 类、模型 5 类、共享与否；独立夹具 oracle，每组实际使用 SQLite、ZIP、ASGI HTTP、LocalFileStore 与 SkillRuntime，执行预检／安装／回读／加载／再次导出。该 25,600 矩阵本身不含文本策略维度；关闭草稿、严格／混合策略、仅策略变化及旧包覆盖重置等由独立结构化输出／文本策略集成和双 TCP 兼容组覆盖，不能把普通安装矩阵当作这些组合的替代。

真实 Provider 首轮脚本的 2 项 UI 夹具将公开 Deferred 字段 `tool` 误写为 `tool_name`，属于验收脚本错误；保留首轮失败证据，修正私有脚本后单独重跑两项并合并 7 项既有通过，最终 9/9。没有为消除失败修改产品、扩大 Mock 或删除断言。9 个场景均无 `execution/error`，新事件无配置快照／引用；严格对象场景还真实经历一次格式拒绝再成功。

Python 首轮全量运行 3,151 次，结束为 5 failures、1 error、62 skips，不能记为通过。失败集中在 2 个测试方法的子测试：全量进程在 00:35 发现并导入测试，两个文件于 00:36:37 完成修改，进程中仍是旧的 `freeze_contract` 方法／恢复必须冻结的 mock 和未保留 `updated_at` 的夹具。堆栈打印的旧方法名与磁盘新方法名不同；以当前磁盘代码起独立进程，两项均通过（2.351s）。首轮日志独立归档为 `TMP_to_delete_python_full_initial_stale_tests.log`，不删除失败记录。随后记录 825 个 src/tests Python 文件起始指纹，重新完整运行；未借此更改产品逻辑、减少断言或添加 skip。

最终轮退出码为 0；上述两个方法也在完整运行中通过。唯一方法数通过同一稳定源码的 unittest discovery 清单去掉 `tests.` 前缀重复统计，不仅靠日志行数猜测。完整统计与逐项跳过原因保存在私有 `python-full-final-summary.json`、`python-full-discovery.json`。

| 最终全量中的跳过类别 | 数量 | 实际处理 |
| --- | --- | --- |
| 未向默认全量进程注入真实数据库 URL | 48 | 相同方法已在隔离 MySQL／PostgreSQL 中各 24 项真实执行通过；不是未验证 |
| SQLite 不支持要求的数据库行锁 | 1 | 不适用；MySQL／PostgreSQL 对应行锁专项已执行 |
| Nacos 外部环境及凭据未配置 | 5 | 未执行，不声称远程 Nacos 集成通过；与本轮输出配置清理无关，未擅自连接共享环境 |
| 本机 macOS 不支持 Linux 软防护／有限 RLIMIT_AS | 8 | 未执行相应平台专项，不声称 Linux 运行环境验收通过 |

两轮测试进程在解释器退出时均打印一个 `ResourceWarning: unclosed event loop`，创建点未定位；没有将它计为测试失败，也未声称全量无警告。两个全量进程均已退出，未留下运行中的测试 event loop 或子进程；此轮未扩大修改其他 SDK／测试运行框架。

### 回归覆盖落点

| 范围 | 实际验收内容与主要测试族 |
| --- | --- |
| 三模式及原生类型 | `structured_output_runtime/public_protocol/validation`、`allow_text_output_runtime/public_protocol`、Schema 单元组；对象、嵌套、数组、字符串、数字、布尔、null、false/0/空值，HTTP/SSE 与 native identity |
| 校验及有界执行 | `structured_output_boundaries/remaining_boundaries/final_deferred_gates`；required、复杂约束、重复键／非有限数、错类型、非法后成功／文字、持续失败、多调用／混调、服务异常、最后请求及预算耗尽 |
| 工具及续跑 | 上述 Deferred 组及 `history_resume_persistence`、`duplicate_deferred_result`、`turn_request_budget`；普通／并行工具、两次 UI 暂停中间插入查询、部分返回、错误／过期／跨用户返回、重复恢复、取消、进程接续及执行额度保持 |
| 接受事务和断线 | `structured_output_persistence`、`incremental_message_persistence`；来源位置、名称／ID／原值、ACK 未保存、未分发／被拒绝、并发／幂等、提交回滚／取消、丢确认回读、接受后可选逻辑失败、Live 溢出／断连及跨通道身份 |
| 旧历史及展示 | 旧快照各形态、新旧 Message/Part、非输出同名普通工具、旧围栏、失败部分思考、附件／context／计划／标题／压缩；Console 全量、协议差分及原问题会话只读复核 |
| 安装与资产 | `agent_packages_api`、`agent_package_lifecycle_regressions`、`builtin_agent_packages`、文本策略配置／跨版组；新建／覆盖、冲突／换包、回滚、关闭草稿省略、策略摘要失效、复制、引用与文件；独立完整矩阵和真实旧 TCP 安装 |
| 配置与备份 | `configuration_import_api`、`config_backup`、`session_history_backup` 及结构化持久化中的真实物理备份／新进程回读；默认／省略／null、草稿、原子性、同 Revision 配置恢复、历史 Revision 字段投影、旧事件保留 |
| 既有修复 | `model_http_transport`、Runner 历史／部分失败、`agent_max_requests`、Console 迟到事实／顺序／共享 JSON；主模型与标题并发真实模型验证 |

此表说明验收落点，不把“所有模块存在测试”冒充每个 Provider、操作系统或部署镜像都已实测。真实模型使用 DeepSeek；其他 Provider 的真实网络及真实业务渠道外发未执行，SDK 传输构造／请求头与渠道生命周期使用既有测试。未修改真实业务提示词／Skill 或写入 BOM／AlphaPG。

## 浏览器与原数据核对

浏览器在自建后台标签页验证：历史长短 JSON、分组、折叠、原生空值、Summary / Output / Raw、Schema 消失、复制成功提示、下载入口、真实 UI 确认和结构化续跑。刷新重开后结果不重复，工作台与诊断只读宿主均展示同一原生输出和三个英文页签；诊断宿主没有发送输入入口。原问题会话只读复核：第一轮 Failed 助手在第一轮异常前，第二轮输出 Completed，未把失败行归到第二轮。页面无 Console error / warning。

浏览器复制入口显示成功提示，但后台 IAB 的 clipboard 读取返回空，不能据此声称系统剪贴板内容实测通过；复制内容保真由 Console 自动化断言覆盖。下载另用正式浏览器 download 事件实际保存并读取 `ui_showcase.json`，空数组、空对象、false、数值及文字全部与原生结果一致；核对后删除下载文件并关闭补验标签页，私有摘要为 `浏览器下载核对.json`。

原数据核对：实施前保存配置表与指定会话事件的行数及 SHA256，验收后用独立 SQLite 只读连接再次核对。agents 8 行、agent_output_configs 2 行、ui_tool_packages 2 行、mcp_servers 6 行，全部 SHA256 相等；原问题会话 9 条、保留演示会话 14 条，按 sequence 排序的完整事件行 SHA256 相等。Revision 仍为 013。临时 Agent 和自有会话全部通过正式删除接口清理后核对，没有写入 AlphaPG、共享库或真实 BOM 数据。

## 遗留项、清理与本地待审状态

- **清理前已有的 Console 缺陷，未在本轮扩改**：UI confirm 的调用属于首个 Run，返回属于 Resume Run。实时 `tool_result` 按“调用 ID＋当前 Run”配对，没匹配到旧 Run 行，又追加无 sequence 的直播结果，排在输出之后；刷新后重新投影恢复一行。后端 History 始终只有一个已完成工具，最终输出仅接受一次。使用同一真实 Events/SSE 次序在实施前基线与当前代码各重放，两边都复现；对应谓词在原始 3facbbb、实施前补丁及当前版本逐字符相同。此问题不是 Schema 清理或此前助手 Loop 修复引入，按范围单列证据，不修改其他工具轨迹逻辑。私有证据：`ui-resume-duplicate-readonly-evidence.json`、`ui-resume-duplicate-before-after-summary.json` 及刷新前后截图。
- Docker 正常启动，真实数据库专项容器及临时凭据均已删除；Docker 引擎未重置，未中断其他容器。
- 实时 8000 SQLite 服务保持可用；强杀／重启故障注入仅在隔离进程与临时数据库进行。8000 按正常 supervisor 停止／启动加载本地新代码，未强杀。
- 原始响应、截图、哈希、日志和旧基线证据留在忽略目录 `docs/local-artifacts/Report Schema快照清理回归20261023/`，不进入 Git。
- 真实 Provider 共创建 12 个临时 Agent（首轮 9、修正私有 UI 夹具后 2、浏览器 1），全部和自有会话清理；原配置行数与哈希已验证还原。两个自建浏览器标签页已关闭，用户标签页未关闭。矩阵父进程、resource tracker、3 worker、旧版 TCP helper 及对应临时目录均退出／删除；随机凭据和环境文件为 0。两个 Python 全量进程、独立发现清单进程及定向复验进程均已退出；未发现测试 Chrome／Playwright profile 残留。
- 不暂存、不提交、不推送、不升版本、不打 Tag、不通知其他会话。

最终门禁再次核对：原配置及两组会话事件 SHA256 一致，Revision=013、版本=0.14.3、HEAD=3facbbb 均不变；暂存区为空，迁移／依赖／版本文件没有差异，`git diff --check` 通过。8000 `/health` 与 Agent 列表均返回 200，正常 API／Gateway supervisor 保留运行。安全收尾摘要为私有 `最终收尾核验.json`。
