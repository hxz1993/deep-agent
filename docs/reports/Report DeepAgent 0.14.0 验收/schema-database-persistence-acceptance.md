# Schema、真实数据库与接受事实专项验收

冻结后定向组合 **97 个测试方法、43.889 秒全部通过、0 skip**：持久化/Live/规模 27、Schema/校验 26、旧备份 15、配置导入/安装生命周期 29。

额外完成 [全部真实数据库可选回归](real-database-full-regression.md)：MySQL 23/23、PostgreSQL 23/23，0 skip，包含既有渠道/逻辑引用/MCP 行锁，记录首次失败和历史夹具修复依据。

本记录使用 0.14.0 候选源码、真实 Pydantic SDK 和隔离 Event Store。模型行为由 FunctionModel 确定性产生，在线 Provider 的执行结论另见主报告。未修改稳定 8000、共享数据库、Cloud 或两个业务客户端；不把合成夹具当客户业务采用。

## Schema 与独立校验资源

`tests.unit.test_structured_output_schema` 13 项、`tests.integration.test_structured_output_validation` 13 项，覆盖以下边界：

- Draft 2020-12 完整验证；对象、数组、所有根标量与内部布尔 Schema；外层 optional、内部 required、条件/组合、additionalProperties、enum/const、数值与长度约束。
- default 仅保留声明，不补值；字符串数字/布尔不强转；null、false、0、空串、空对象/数组原值保留。
- 本地非循环 `$defs/$ref` 展开与 pointer 转义、引用兄弟约束；拒绝外部/文件/动态/递归引用及改变作用域的嵌套 `$id`，不发网络或文件读取。
- Schema 64 KiB、深度 16、节点 2,048、属性 512、enum 1,024、pattern 长度 512 的精确边界，以及展开后预算；缓存容量 128 和防御复制。
- 结果 1 MiB 的 UTF-8 精确边界、深嵌套数据、非 JSON Python 对象、NaN/Infinity；错误最多 20 条、单项字段最多 256 字符、不回显候选值。
- date/date-time/time/uuid 有效/无效值均由 worker 实际检查，不依赖可选 format 扩展包是否安装。
- 每 API worker 两个 active、16 个等待者；等待 2 秒、执行 2 秒；容量/启动/协议/系统错误是服务故障，不能作为模型内容纠错。
- 恶意 `^(a+)+$` 实际达到超时，进程 kill 后 wait/reap，事件循环心跳持续；取消回收进程并释放容量，后续请求可以继续。测试后无校验 worker 残留。

实现使用当前 Python 解释器、已安装 jsonschema 及其已有依赖；没有新增生产依赖、修改固定镜像或发布时动态安装步骤。

## 数据库 012 与旧备份

执行了 SQLite 一项完整场景、真实 MySQL 8 三项、PostgreSQL 16 三项。MySQL/PG 包括既有迁移引用与配置导入套件及新增 012 场景；不是 skip 或用 SQLite 代替。增强并发/011 备份后最后复跑：SQLite 1 项约 2.5 秒、MySQL 3 项约 8.0 秒、PostgreSQL 3 项约 14.1 秒，全部通过。

- 真实 011 Agent、会话、Unicode 原始事件升级 012 后不变；仅一新表两列，实际列长度、非空、布尔默认 false、更新人默认 NULL、主键和无物理外键均独立查询断言。新建 raw insert 也使用数据库 false 默认，无默认配置行。
- Schema/中文/emoji/UTC 往返；不同 Agent 可以同名输出，同 Agent 第二行由真实主键拒绝。
- 故障发生在配置写入 flush 后，Agent 开关/时间/更新人/定义完整回滚。故意制造孤儿可被逻辑引用预检拒绝。
- 每个真实方言分别三次并发修改输出与删除父 Agent，无错误、死锁或孤儿；父记录锁序保持一致。
- 012 全配置备份和真实恢复保留开启定义、关闭草稿及原更新人，不混入聊天运行事实。
- 真实 011 备份仍按 011 物理契约识别并恢复到 011，不要求 012 新表/新列；011→012 跨 Revision 恢复仍明确冲突，不偷改版本标记或自动迁移。
- 关闭草稿、开启 Agent、更新人、接受/失败输出、冻结快照分别阻止危险降级；没有新事实的空库真实降到 011 并重新升到 012，旧事件保留。

修复配置恢复对历史 Agent 表的识别：新增明确的 011 表契约和按版本选择配置表集合。旧 006/010/011 测试夹具按实际物理列写入，不把最新 ORM 默认塞进旧结构；没有改写既有 Alembic 迁移或放宽已知类型/主键/引用检查。

数据库容器仅映射随机 loopback 端口，随机凭据不打印。临时数据库、容器和凭据文件在 finally 删除；保留的安全结果不含密码。生产 DDL/混合 Worker 发布顺序继续作为上线前门禁，未在本轮执行。

## 不可变接受事实和故障边界

`tests.integration.test_structured_output_persistence` 共 27 个测试方法：24 个持久化方法、2 个实际 API/Live 方法、1 个列表规模方法。完整冻结候选复跑结果以主报告为准；其中提交后的辅助故障包含 12 个子场景，不将其误算为 12 个 unittest 方法。

- 接受前必须具备同一会话/拥有者/Agent/Turn/Run 的真实输出调用、后序 SDK ack 和原始冻结契约；source ordinal/part index/call ID/name/digest 全部一致。原生候选与接受值必须深等，不能引用别轮同 ID 调用。
- UUID5 结果身份稳定；同事实重复保存不增加 sequence，修改值/attempt/accepted 不能覆盖已写事实。同调用 ID 在不同响应和 Run 不冲突。
- rejected-dispatch 的源响应不能被接受；伪造 source、错 Agent/用户/Turn/Run、缺 ack、ack 提前或在别轮均零接受写入。
- commit 前失败/取消回滚；commit 已开始时等待其明确结束再关闭 session；数据库确认丢失通过稳定 ID 独立回读确认一次成功，不伪装失败或重复提交。
- SDK ack 写入失败，normal/stream 都没有 accepted 或成功 final；预算检查点落库失败，在下一次模型请求前停止，不留下半接受结果。
- 接受后的观察队列连接错误不能反转事实；压缩、metrics、context 展示、标题任务、日志、冗余 flush 和技能辅助记录故障不能把完成变失败。取消/transport 关闭仍可终止连接，不能改写已提交的 DB 完成事实。
- 真 HTTP/SSE、Live 和 History 使用相同 output_id/原生值；partial 不提前接受；一个观察者断开不取消生产者，且模型仅执行一次。实际 Event API 每页两条跨输出事实遍历，无缺失/重复，source event_ids 可定位。
- 慢观察队列达到上限发送 reset，随后可从完整 History 恢复同一结果；语义事实不重复提问数/请求/Token/工具执行次数或“最近异常”。真实一次失败修正后成功只有一条提问、两次真实模型请求、零普通输出工具执行、无最近异常。
- 旧同名普通工具仍是普通工具；旧围栏候选 normal/stream 不进入接受结果、普通 History 或 text SSE。既有 Plan、Context、附件、普通工具与旧正文项目在加入接受输出后 ID/值完全一致；官方 PlanStore 从原始 write_plan 快照恢复，输出不污染计划。
- 已接受结果经过两次正式 compaction projection、物理 SQLite backup 和全新 Python 子进程回读，仍是同一 output_id、同一值、一个冻结快照。
- Agent 管理列表 10/100/1,000：每次一个列表 SQL、零 Schema 查询、一次批量 resolver；真实账号批次资源实测另见资源报告。真实聊天与手工会话改名不改变 Agent 更新人/更新时间。

这组测试发现并修复了 source/ack 提交归属、commit 确认丢失/取消清理及提交后辅助异常反转成功的实际缺陷。重复 tool_call_id 的 Part 编码问题由边界专项发现并另有独立回归。

## 体积、安全显示与日志边界

近上限结果经过真实隔离校验和 Event Store：

| 指标 | UTF-8 payload 字节 / 数量 |
| --- | ---: |
| 完整 Schema | 62,119 |
| 原生输出 | 1,048,567 |
| 接受前事件 | 3 |
| 接受后事件 | 4 |
| 接受前 payload 合计 | 1,112,405 |
| 接受后 payload 合计 | 2,161,239 |
| 新增不可变接受事实 | 1,048,834 |

结果在真实 SDK 调用与接受事实各保存一份；没有额外结果表或正常 Run 表。完整 Schema 只在冻结用户快照中出现一次，搜索字段不含完整 Schema，output/attempt 的 search_text 为 NULL。校验模块与输出状态模块没有完整 Schema 日志调用，错误仅记录有界字段；普通诊断回归也验证私有候选标记不进入结构化诊断字段。

`structured-output-forward-compatibility.test.mjs` 三项通过：未知 SSE/History 结构不产生正文、工具或 UI 动作；已知输出忽略包络扩展，保留原生业务 JSON；未知 raw 事件不创建轨迹。恶意正文按通用 renderer 转义，不执行脚本。此证据只代表平台 Console，不替代第三方客户端的接收器、幂等采用和业务保存。
