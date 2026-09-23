# C04-USAGE：个人用量统计计划

状态：2026-09-23 已按本方案在本地实现 0.11.0 / 007，UI 验收中，尚未提交推送。事件表仅新增 `usage` JSON 与 `usage_model_id` 两列；不新增 Usage 表或计量事件行。实施接口和历史回填说明以 [用量诊断指南](../../guides/diagnostic-usage.md) 为准；下文保留设计背景和未来扩展讨论。

原更新索引 C04 指的是价格数据后台更新；本课题是独立的产品用量统计，沿用 C04-USAGE 编号。价格刷新、金额、额度和限流均不在本轮范围。

## 最终形态

回答三个问题：一个人在指定日期内用了多少 Token、主要用在哪些模型与 Agent、能否追溯到具体会话和模型请求。

复用 Pydantic AI 的供应商 Usage，不重新计算真实消耗。产品只补请求归属、可靠保存、时间筛选和汇总展示。上下文预计占用仍沿用现有预算逻辑，不进入消费统计。

首版仅管理员可访问：在内置 Console 诊断区增加独立“用量”页，与日志、会话并列。普通用户不能进入，不增加“我的用量”入口或个人查询 API。点开会话沿用现有管理员详情，不另设一套逐用户查看权限。只改内置 Console，不跨仓改电子表格和 DeepCube 客户端。

## 上游核验结论

本次读取本地上游源码快照，不代表所有项目线上服务的完整实现。下述路径以知识库 `deepfos-AI/` 为根；项目之间的 Token 分桶定义不完全一样，不能直接复制求和公式。

| 项目 | 实际已有能力 | 本次采用的经验与边界 |
| --- | --- | --- |
| Pydantic AI 2.46.0 | `RequestUsage` 保存每次模型请求，`RunUsage` 累计一次运行；包含输入、输出、缓存等；嵌套 Agent 可共享累计对象 | 直接复用单请求归一化 Usage。共享 RunUsage 不能作为每次请求值再累加；本身不提供按用户、日期查询的产品页面 |
| Harness 0.32.0 | `SpendLimits` 支持 run/conversation/day/month/total，以及从 deps 指定 user/tenant scope；不设金额和 Token 上限可只计数。默认内存存储，可用 Redis；CLI `/usage` 按保留历史显示各轮及合计 | 官方确实已有按人、日、月计数的基础。计数器主要累计总 Token、请求、金额，有保留期；CLI 依赖保留历史。不能等同于永久、可筛模型、可查单请求的个人明细 |
| OpenAI Agents Python | 单次 Run 汇总、逐请求 usage entries；嵌套和续跑有累计规则 | 参考请求明细与 Run 汇总分层；不是现成的跨用户统计后台 |
| OpenCode 本地 CLI | 会话、模型、工具、项目统计 | `--days` 按会话更新时间筛选，再统计整段会话，不能用于准确的每日个人消费 |
| OpenCode Console / Zen | 独立 usage 请求表，含 workspace、model/provider、key、session、时间和 Token 分桶；通过 API key 关联用户，按日期、模型、key 查询 | 最接近本次数据结构；但其图表主要度量费用，仅覆盖通过 Zen 的调用。借请求记录和身份归属，不照搬费用功能 |
| Dify | 常规监控按应用与日期汇总消息、会话、活跃终端、Token；Workflow 另有运行统计 | 参考自然日和时区筛选；活跃终端数不是个人用量明细。Message、Workflow 汇总不能叠加。部分停止路径使用估算，不照搬这一口径 |
| OpenClaw | 按日期、模型、Provider、Agent、渠道、会话创建者等维度统计，来自会话 transcript 投影 | 参考筛选和完整性表达；源码明确 creator 是不可变会话创建者，并非逐轮计费归属。运行时辅助调用计数与历史报表覆盖不能混为一谈 |
| Codex 开源客户端 | response/turn/thread 用量及累计值，持久化到 Rollout；最后一次上下文用量与累计消耗分开 | 参考请求关联及占用/消费分离；不是官方账户后台按个人用量系统，也不能假设其累计代码自带全局响应去重 |
| DeepSeek Harness | durable event 投影会话/单轮 Token、缓存、路由；同一尝试的后续样本替换，重试独立累计；缺失时不强行给精确值 | 参考重放、重试、部分数据与上下文分离；已核验的这条统计链没有直接提供跨用户、日期、模型的管理页面 |

## 本轮采用方式

Pydantic AI 提供单请求 `RequestUsage`、整轮 `RunUsage`，以及单轮约束 `UsageLimits`。Harness `SpendLimits` 在此基础上提供 scope（可按用户/租户）和 run/conversation/day/month/total 窗口，不设上限时只计数；`SpendRecordedEvent` 可向应用报告单响应用量，`status()` 可查询配置窗口。默认计数器在内存，Redis 可跨进程共享；这些能力不自动生成个人历史报表，日/月窗口默认按 UTC 划分。

本轮直接复用归一化的单请求 Usage，Payload 保留原始依据，同事务提取到独立 usage JSON 列；模型 ID 单独作为常用筛选键。数据库先按范围筛选，只读取小型 usage JSON，由后端解析、去重与汇总，不扫描消息正文。不新增 Usage 表、不引入 Redis，也不为了展示统计启用费用/限额能力。

以后需要配额、跨实例一致计数、会话删除后独立保留消费或查询性能确有瓶颈时，再评估专用表和 Harness 计数器。模型可选权限与用量配额是不同能力：限制模型选择本身不要求 Usage 表；配额执行也不是建表就完成，还需要并发、预留和失败处理。SpendLimits 作为 Agent(capabilities=[...]) 中的能力，在 agent.run 内自动累计并检查下一请求，status(scope=...) 读取计数；它不是消息 Payload 属性。本轮统计接口只校验现有管理员权限，不开放普通用户查询，不能只在菜单上隐藏入口。

## 维度和页面

首版核心为按人、按时间：查询任意允许的日期范围、每日趋势、用户汇总。建议支持以下辅助维度，避免把每一项都做成独立页面：

| 维度 | 回答的问题 | 首版位置 |
| --- | --- | --- |
| 用户 | 谁用了多少 | 管理员分组/筛选 |
| 日期 | 今天、本月或某一段时间用了多少 | 日期筛选与每日趋势 |
| 模型（含 Provider） | 主要消耗在哪个模型 | 首要辅助分组，区分不同 Provider 的同名模型 |
| Agent | 哪个助手场景消耗多 | 次要分组/筛选 |
| 会话 | 某笔消耗发生在哪个对话 | 下钻明细、可授权跳转 |
| 用途 | 聊天、标题、压缩、模型式搜索分别用了多少 | 明细筛选，不单独增加图表 |

App/Space、渠道、工具维度暂不增加独立报表；可靠归属信息可随原事件保留。普通工具没有模型 Usage，不能把工具次数当 Token 消费。

指标建议保持简单：输入、输出、总 Token、已记录模型请求数、会话数；缓存读取/写入及加权命中率放次级详情，复用 C08。请求数不是用户发言次数，未知 Usage 的请求应单独标识，不能强行视为零。

建议在诊断区新增独立“用量”页，与日志、会话并列，仅管理员可见和访问。布局为日期/用户等筛选、简洁概览、日趋势、可切换分组表。点击会话进入已有管理员会话详情，不在原会话列表、聊天侧栏或配置页堆叠统计内容。

建议仅增加管理员 `/diagnostics/usage/*` 接口，复用当前管理员鉴权与会话详情授权。不提供 `/usage/me/*`，普通用户直接请求统计 API 同样拒绝。单次范围先沿用现有最多 31 天的约束；默认近 7 个自然日。查询完整集合后汇总，再分页明细，不因页面 limit 少算。

## 事件表只新增两列

| 列 | 类型与规则 | 用途 |
| --- | --- | --- |
| `usage` | 可空 JSON，沿用项目 TextJSON 兼容存储 | 单请求或多请求的用量、kind/source、模型与Provider ID及名称快照、请求键、完整性信息放一起；不同单位分开保存；普通非计量事件为空 |
| `usage_model_id` | 可空字符串，与 llm_models.id 同长度，按查询需求建索引 | 存实际使用的配置模型 ID，不是 models.dev 目录 ID；同一事件全部请求属于同一已知模型时填写，混合/未知时为空 |

不新增 Token 各列、Provider 列、状态列、请求 ID 列、用途列、时间列，也不另存总计或命中率。原有 event_type、created_at、session_id、turn_id、run_id 复用；用户与 Agent 从会话归属读取。首版按会话归属用户统计，管理员代操作仍归该会话，渠道沿用平台账号。

`usage_model_id` 是历史快照/筛选字段，不建数据库外键，不加入 RESTRICT、CASCADE 或孤儿引用检查。删除模型/Provider 不应因这些历史 Usage 被阻止，也不能清空历史 ID；既有 Agent→模型等删除规则保持不变。展示 LEFT JOIN 当前模型及 Provider：存在时显示当前名称与 Provider；模型已删除时显示“已删除 · 当时模型名”，并展示历史模型 ID 和 Provider 快照。usage.calls 冗余保存模型/Provider 的 ID及当时名称，原始 Payload 保持原样。快照缺失则退回 ID。Provider 单独被删除也保留快照/ID及不可用提示，不能隐藏模型记录；查询不能 INNER JOIN 当前配置而丢掉历史记录。

优先按配置模型 ID 分组，重名不同 ID 分开；可在后续展示层提供名称合并，首版不另做。旧数据拿不到 ID 时保留为空，显示“未关联模型”；原始 Payload 有名称可在 Raw 查阅，不能用现在的同名模型猜 ID。

建议 JSON 形态如下（仅结构示例）：

```json
{
  "version": 1,
  "calls": [
    {
      "request_id": "local-model-call-id",
      "model_id": "configured-model-id",
      "model_name": "model-name-at-call",
      "provider_id": "configured-provider-id",
      "provider_name": "provider-name-at-call",
      "kind": "model",
      "state": "reported",
      "tokens": {
        "input_tokens": 10000,
        "output_tokens": 200,
        "cache_read_tokens": 8000,
        "cache_write_tokens": null
      }
    }
  ]
}
```

多次调用用 calls 数组，不把 RunUsage 累计值塞成一次调用。只有已确认字段写数值；未知字段保留 null。请求键和 state 都在 JSON 内。供应商额外详情保留原始 Payload，不复制完整 HTTP 响应。

## 不新增事件行的保存方式

- 主聊天：复用现有模型响应 Part 事件，在固定的一条写 usage，其他 Part 留空。原 Payload 保持现状；终态补写同一条。现有事件幂等键、会话写锁与 JSON 请求键联合防止重复，不能再声称有新增请求 ID 列的数据库唯一约束。
- 标题：成功则附在现有模型标题事件；未采用/失败时附在该次已有 fallback 标题事件，保存用量不改标题正文或有效标题选择。Store 在锁/事务内决定唯一承载位置，避免成功与 fallback 各存一份。
- 压缩：原压缩事件 usage.calls 保存每次摘要请求，包括重试及有 Usage 的失败响应；不新增 usage/compaction 等事件类型。
- 网络搜索（web_search，不是会话历史检索）：留作后续独立统计。搜索服务可能不引用 llm_models.id；不能按同名猜模型 ID，也不为本轮计量重构搜索适配器。
- 无可关联持久事件的调用、进程强杀和底层隐藏重试不承诺完整计量，不能为了本轮覆盖这些情况创建新事件行。连接测试首版不纳入。

同一业务事件的多个模型/重试通过 calls 明细分别分组。即使 usage_model_id 为空，按模型筛选也必须纳入这些候选事件再检查 calls，不能漏掉混合模型。普通单模型事件用模型 ID 索引快速筛选；未知 ID 旧记录不硬匹配当前配置。

Payload 的原始 SDK Usage 保留；主响应投影和辅助调用明细由同一采集/提取逻辑写入，统计只读取 usage，不把 Payload 再加一遍。元数据补写不得更改已确认正文、模型历史或压缩成功状态；不能因仅补计量就把会话活动时间搬到今天。Calls 内存在多个用途的异常关联需保留来源元数据，不能仅依父事件名称猜测。

## 模型与独立 API 的边界

两列职责固定：usage_model_id 仅存模型配置字符串 ID或NULL，不混入 JSON、Provider 名、URL 或 api:xxx 等编码。网络搜索没有模型配置 ID就为空；kind/source等分类在 usage.calls 内表达，不再增加列。

计量分类：kind=model 表示主聊天/标题/摘要等对话模型请求；kind=web_search 表示独立网络搜索；未来 kind=image_generation 表示图片生成（仅约定分类，不在本次实现图片工具）。用途仍通过原事件/调用来源区分。分类按业务入口，不靠模型 ID 是否为空判断：旧模型请求也可能没有 ID，未来图片工具也可能引用模型配置。

网络搜索 source 保存 provider 类型及有证据的配置引用/名称，不保存完整凭据或带密钥 URL，不要求创造一个不存在的模型 ID。后续图片生成若真的引用 llm_models.id 可以填模型 ID，但仍归图片类别；独立服务则按供应商/API入口统计。

管理员“模型用量”不混入独立搜索、图片服务。本轮不采集网络搜索或图片生成的独立计量；未来需要单独入口统计时可复用预留分类。Token、请求次数、credits、图片张数分别保留单位，不能直接相加或擅自换算。

主聊天调用网络搜索所产生的正常输入/输出仍算主模型消费，不能把主响应也排除；独立搜索服务的用量只记在工具事件，不复制同一份主模型 Usage。既有供应商原生工具计量若已经包含在主响应，不再作为独立调用重复记录。

## 查询与效率

沿用现有事件 created_at 和指定时区按日期查询，不新增 usage_at；这是事件记录日期，多个调用挂同一事件时按该事件日期统计，不提供逐请求跨日计费精度。

数据库先按日期、会话用户/Agent和模型 ID 缩小范围，仅选择身份字段及 usage；后端分批解析 JSON、按请求键去重、按人/时间/模型/Agent/会话聚合，用途按原事件类型映射。先算完整匹配集合，再分页返回；不能只累加页面已加载的事件。

当前 TextJSON 是文本兼容存储，MySQL 5.6 也属于设计兼容范围，因此不承诺统一使用 JSON SQL SUM/GROUP BY 或 JSON 数组索引。该方案节省 Payload IO 与正文解析，但并不消除 Usage JSON 的解析成本。以现有数据量实测，优先按时间/会话/模型筛选建立必要索引；真正出现性能瓶颈再考虑专门统计表，不预先造日汇总和缓存体系。

## 口径约定

- 单次请求使用 SDK Usage，缓存是输入子项，reasoning 若属于输出就不重复加入总数；上下文估算不进入真实消费。
- 同一请求的流式样本/Part/终态覆盖或合并同一份计量，不逐样本累加；新模型重试单独计数。Deferred 跨 Run 产生的新模型请求同样独立统计。
- 输入/输出全部未知显示“—”，有部分数据显示已知合计与缺失数量；缓存比例延续 C08 完整性规则，不平均请求百分比。
- 按可信会话平台用户归属，辅助调用随所属会话汇总。渠道仍按配置的平台归属账号统计，不自动等于企微发言人；管理员代操作不另改会话归属。
- 复用计量承载事件的 UTC created_at，按指定时区自然日 `[start,end)` 筛选。不得改成请求开始时间口径或使用 updated_at，以免回填/终态补写搬移日期。
- 报表需读取日期内全部相关事件，不能只读当前压缩后的模型上下文或前端已加载的消息。
- 首版是基于仍保留事件的统计：普通压缩不应减少原始历史统计；会话/事件真正删除后该部分不再可汇总，不承诺独立永久账本。
- 原有会话/C08 聊天响应口径保持不变，个人报表新增辅助调用后通过“用途=聊天”与原统计对账，界面注明含辅助调用。

示例：一个人在一天中调用聊天模型两次、标题模型一次、摘要模型一次，个人汇总应覆盖这 4 次；按用途筛选“聊天”则只剩 2 次。由同一批事件按人、日期、模型分组即可，不需要为每个维度保存另一套累计数。

## 历史和实现范围

007 仅新增上述两列及必要索引；旧聊天按完整响应边界分批、幂等回填 usage 和有证据的模型 ID，保留原始 Payload 和 created_at，不在启动时无界解析所有历史。历史标题缺失、摘要只有累计值的保持未知，不猜算。回填未完成时明确显示覆盖范围，不能把空列当已验证零值。支持 dry-run 与同条件对账，不从字符串日志拼接消费。

新增两列、终态补写、查询和历史回填一起验证；不新增辅助事件类型，不改变普通历史/压缩投影。保留现有配置备份与逻辑引用规则。007 已在隔离数据库与本地 SQLite 验收环境验证；未操作共享 Alpha／Test 数据库。

## 实施顺序与定向验收

### 本轮可行性复核（2026-09-23，仅确认，未实现）

结论：两列、不增表、不新增计量事件行的结构可行。它兼容现有事件和模型历史，但需要轻量的 Usage 采集与原行补写，不能仅加 ORM 字段就算完成。

- 官方复用：`ModelResponse.usage` 是单请求事实；正常返回用 `result.new_messages()`，异常取证可用 `capture_run_messages()`。需要覆盖外层响应校验/重试时，通过公开 `AbstractCapability.wrap_model_request` 收集，这是 Harness SpendLimits 也采用的边界；不重写 HTTP/Model 代理、Token 算法或模型循环。注册顺序须定向测试，不能宣称该 hook 覆盖供应商所有内部请求。
- 本地已用 Pydantic AI 2.46.0 / Harness 0.32.0 的 `FunctionModel` 做无网络、无数据库小验证：已有 Run 输入 1000，本次响应输入 20 / 输出 5，结果 Run 输入 1020，但 `new_messages()` 单响应仍为 20 / 5；工具随后抛错时，`capture_run_messages()` 仍保留之前响应的 30 / 7。这只是 SDK 接口验证，不是产品联调或真实模型验收。
- 标题：现有 fallback 事件在模型调用前已落库；标题生成失败、取消、用户提前改名导致 CAS 失效，都需向原承载事件尽力补写 Usage。成功/失败承载选择同事务，保留用户标题，不新增标题或计量事件来绕过 CAS。会话已删除则不能重建。
- 压缩：现有 running 事件足以承载各次摘要响应。`summary_agent.run(usage=ctx.usage)` 共享累计，必须改为采集每个 response.usage；摘要重试、验证失败和 stale 仍可补原行 Usage。现有 update_lifecycle 仅匹配 running，需要独立 usage-only 更新，绝不能把失败/过期改成成功。
- 历史兼容：现有 `_event_rows_hash` 仅包含 sequence、event_type、payload。新增两列不会进入压缩 hash 或模型消息投影。补统计只更新新列，不改旧正文/Payload/source、created_at、session.updated_at 或业务状态。
- 扩展：JSON 的 version/calls/kind/source 与单位可以容纳未来网络搜索、图片等；模型 ID 无外键/删除依赖，已删除模型通过名称快照和 ID 展示。混合模型事件 ID 为空仍需解析 calls，不能被模型索引筛掉。
- 边界：无供应商 Usage、进程强杀、无原始承载事件、隐藏 HTTP 重试无法保证完全恢复；日期按原事件时间、删会话随事件删除用量。这是运行统计，不是独立永久计费账本。

证据：`agent/title_generator.py:221`、`agent/runner.py:1430`、`db/conversation.py:805`；`agent/context/compaction.py:1409`、`:1483`；`db/stores/event/base.py:369`；`db/conversation.py:866`。本轮保持产品代码和数据库不变。

### 后续实施步骤

1. 在隔离数据库验证 007 两列/索引、JSON 契约和固定承载行；验证模型删除不受新增历史引用阻止且展示已删除标记、历史名称及 ID、混合模型筛选不漏计。
2. 统一采集单请求 Usage，补标题、摘要，低成本可取得真实 Usage 的网络搜索独立保存；优先证明共享累计量不重复相加、失败调用不因业务结果被丢弃。
3. 实现事件投影汇总和用户权限，先按人/时间/模型对账，再做简洁页面；会话与用途只做下钻/筛选。
4. 定向测试：多 loop、多 Part、终态补写、重连/重放、同 Provider ID、跨 Run、并行标题、摘要重试、失败/取消、明确零与缺失；管理员跨用户筛选与普通用户拒绝访问、跨日/时区、分页、压缩后历史、删除行为。
5. 本地现有会话做少量真实调用与一次主动压缩，核对逐请求 Usage、用户/日期/模型/用途合计；保留原会话附件，不为报表大量消耗模型。

本地实施已纳入 0.11.0 / 007，待用量页 UI 验收后再逐项推进 C12/C09/C10/C11；未在共享环境执行迁移，不提交推送。

## 暂缓：SpendLimits 配额控制

用户确认本次不接 SpendLimits，仅登记后续需求。它不是每次请求独立重置的限额：Budget window 可选 run/conversation/day/month/total，scope 可按用户；运行时在下一次模型调用前检查对应窗口累计，超限抛 SpendLimitExceeded，使 loop 不再继续发下一次请求。已发出的请求不因累计跨线而自动撤销，并发也可能越过上限。

未来确有用户额度需求时优先复用官方能力，先验证共享存储、窗口时区、取消/缺失 Usage 与并发超额边界，不自行重写预算循环。它不等同于模型选择权限。首版只做历史统计，不配置限额、不接 Redis、不改执行停止语义。

## 源码依据

以下为本地实际核验入口（可按符号搜索）；上游只读，未拉取或修改。DeepAgent 基线 `test@34b6ebf`，0.10.19、DB Head 006。

- `agent-labs/upstream-agent-references/projects/pydantic-ai/pydantic_ai_slim/pydantic_ai/usage.py`：`RequestUsage`、`RunUsage`、`UsageLimits`。
- `agent-labs/upstream-agent-references/projects/pydantic-ai-harness/pydantic_ai_harness/spend/README.md`、`_snapshot.py`、`_capability.py`：纯计数、窗口、scope、保留期及模型请求包裹边界；`pydantic-clai2/src/pydantic_clai2/usage_report.py`：会话 Usage。
- `agent-labs/upstream-agent-references/projects/openai-agents-python/docs/usage.md`：Run 与逐请求、嵌套调用。
- OpenCode `014614d`：`packages/opencode/src/cli/cmd/stats.ts:87`；`packages/console/core/src/schema/billing.sql.ts:114`；`packages/console/app/src/routes/workspace/[id]/usage/graph-section.tsx:27`、`usage-section.tsx:11`。
- Dify `863ce1d7`：`api/repositories/app_statistic_query_repository.py:65`；`api/core/app/task_pipeline/easy_ui_based_generate_task_pipeline.py:430`。
- OpenClaw `ffb50bc04`：`src/shared/usage-types.ts:41`；`src/gateway/server-methods/usage.ts:240`；`src/infra/session-cost-usage-worker-refresh.ts:244`。
- Codex `48f897e4`：`codex-rs/protocol/src/protocol.rs:2235`；`codex-rs/core/src/session/mod.rs:4566`。
- DeepSeek Harness `ddefc45fbc`：`packages/llm/token-meter/src/usage-projection.ts:82`、`turn-usage.ts:81`；`packages/client/ui-chat/src/client/chat/StatsPills.tsx:317`。
- DeepAgent：`src/deep_agent/agent/title_generator.py:221`；`agent/context/compaction.py:1383`；`agent/message_persistence.py:190`；`diagnostics/sessions.py:412`；`web/deepseek.py:80`；`models/connectivity.py:45`；`api/v1/channel_auth.py:44`；`db/configuration_transfer.py:8`。
