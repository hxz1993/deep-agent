# 2026-10-22 设计核验记录

> 本记录说明这次实际做过什么，不是结构化输出功能的验收报告。设计与测试计划见[课题入口](README.md)。

## 1. 工作边界与现场基线

- 目标：核对既有09课题和最新产品决定，更新完整设计及回归计划。
- 仓库：DeepAgent；分支 `test`；HEAD `552b7b25291e85a23b22244d5178823f1c60570e`；产品 `0.13.9`，迁移文件 Head `011`。
- 不切分支、不拉取/重置、不改正式代码/测试/依赖、不启动或重启服务、不访问共享数据库写接口、不运行DDL、不提交推送或打Tag。
- 原有19项未提交改动保留；文档修改前 `git diff --binary` SHA256 为 `62bac4e044d8b172a8b9fc9c036fb756c5a53f9bcbfbd74c103517f668a271f7`，收尾排除本次 `docs/design` 变化复核。
- 验证使用 `.venv/bin/python`、`PYTHONDONTWRITEBYTECODE=1`；框架模型是内存 FunctionModel，未发真实付费模型请求，未导入生产 main。

## 2. 当前实际依赖

通过 requirements 和解释器 distribution metadata 双重核对：

| **包** | **requirements** | **本次解释器实际版本** |
| --- | --- | --- |
| pydantic-ai-slim | 2.46.0 | 2.46.0 |
| pydantic-ai-harness | 0.32.0 | 0.32.0 |
| pydantic | >=1.10,<3 | 2.13.4 |
| jsonschema | >=4.23,<5 | 4.26.0 |

系统级包路径与venv包路径并非完全相同，正式回归应继续记录实际模块来源。没有安装或升级任何包，不能拿AI Labs的较新2.57代码当当前运行行为。

## 3. 旧安装器归档测试：12项通过

实际执行命令：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  -m unittest tests.unit.test_agent_packages.AgentPackageArchiveTests -v
```

结果：退出0，**12项通过，约2.862秒**。覆盖当前格式版本、未知内容拒绝、重复JSON字段、危险ZIP路径/符号链接、文件预算、资源关联与真实builtin加载。

这是**当前旧安装器测试**。其中“拒绝未知安全扩展”的期望在新版本实施时需要按新包兼容规则调整并拆分，危险ZIP/重复字段等断言不能一起删除。没有运行完整新安装流程。

## 4. 内存包探针：当前旧版的真实行为

在同一解释器内用 `archive.build_zip/canonical/parse_package` 构造合成ZIP字节，不落盘、不写数据库：

| **输入** | **实际结果** |
| --- | --- |
| 旧v1基础包，来源0.9.11 | 接受 |
| 来源99.42.7、包格式仍v1、无新增内容 | 接受 |
| 新增 `structured_output_enabled:false` | 拒绝，extra_forbidden |
| 新增结构化配置字段 | 拒绝，extra_forbidden |
| 新增 `output-config.json` | 拒绝，无法识别文件 |
| manifest schema_version=2 | 拒绝，只支持v1 |
| 缺 `ui-tools.json` | 拒绝 |
| 已认识JSON含重复键 | 拒绝 |
| 额外未知文件路径是 `../outside` | 拒绝 |

结论仅限：旧版对来源软件版本宽容，但不容忍新增键/文件，**false也不能带**。新版关闭导出必须显式省略，不能把模型默认字段序列化出来。

## 5. 真实历史 builtin 包读取

读取仓库原始 ZIP，只输出来源、格式与哈希，没有打印提示词或业务正文：

| **文件** | **来源软件** | **包格式** | **SHA256** |
| --- | --- | --- | --- |
| deepcube-assistant.zip | 0.10.0 | 1 | `5f35ce440e95f30709b9b8d8e99d9f61022ff018e09e3be006b0103bac1190bb` |
| deeptable-assistant.zip | 0.13.3 | 1 | `936dbee389cce11ec95ad901cb5dc7fd893f22b81892501905d10ae55a9ddfe7` |

两个包均由当前解析器成功读取。此结果不是“新版已能导入旧包”，新版尚未实现；它们已被指定为后续跨版本安装的固定历史夹具。

## 6. 固定 SDK 的普通/流式隔离验证：6条通过

使用 `Agent + FunctionModel + ToolOutput + DeferredToolRequests`，公开 `AbstractCapability.after_model_request` 拒绝混调，`after_output_validate` 在非partial阶段执行 Draft202012Validator。

对象类型使用 StructuredDict；数组类型使用带 WithJsonSchema 的动态类型。模型回调固定产生四个响应：

1. lookup与lease_result混调，整批拒绝；
2. 单独lookup，实际执行一次；
3. 单独lease_result但缺required（数组则minItems不足），要求修正；
4. 单独正确lease_result，接受结果。

| **入口** | **根类型/结果** | **实际断言** | **结果** |
| --- | --- | --- | --- |
| 普通run | object/dict | 请求4次，混调拒绝1次，lookup仅执行1次，所有请求Schema相同，最终dict等值 | 通过 |
| 普通run | array/list | 同上，最终list而不是字符串或response包装 | 通过 |
| run_stream_events | object/dict | 同上，完整事件流完成后得到dict | 通过 |
| run_stream_events | array/list | 同上，完整事件流完成后得到list | 通过 |
| 普通run | UI CallDeferred | 真正返回DeferredToolRequests，calls数量1 | 通过 |
| run_stream_events | UI CallDeferred | 同样返回DeferredToolRequests，calls数量1 | 通过 |

实际流式调用遵循本版本 API：

```python
async with agent.run_stream_events("synthetic") as events:
    async for event in events:
        if hasattr(event, "result"):
            result = event.result.output
```

验证过程中最初将run_stream_events直接用于async for，得到TypeError，按本版本context manager形式修正后全部6条通过。没有据此修改任何业务代码。

边界：这些测试只证明公开框架组合可行，没有连接DeepAgent的持久化、Session锁、请求预算、Provider、渠道或Console；也没有完整验证Deferred答案恢复。不能写成产品端到端通过。

## 7. 源码确认的实施缺口

以下路径从仓库根解析，行号是本次核对基线位置；后续改动后应按符号重新定位。

| **入口** | **当前事实** | **设计处理** |
| --- | --- | --- |
| `src/deep_agent/db/models.py:145` | Agent无通用JSON配置、无更新人/输出开关 | 两Agent列＋一配置表，012 |
| `src/deep_agent/agent_packages/archive.py:36` | StrictModel也被InstallOptions使用 | 只放宽包内容，不能全局吞掉操作参数错误 |
| `archive.py:191` | 必需文件/未知文件/格式版本预检 | 安全未知扩展忽略；必要和认识的内容验证保留 |
| `archive.py:229` | 包摘要含全部文件 | 忽略的安全文件仍入摘要 |
| `agent_packages/service.py:41` | 配置快照显式列模型 | 加新表，覆盖预检/导出/提交核对 |
| `agent_packages/service.py:282` | 安装直接写ORM，绕CRUD Store | 开关/定义/操作者同安装事务 |
| `src/deep_agent/agent/runner.py:2809` | dict/list目前会str() | 统一原生JSON序列化 |
| `src/deep_agent/agent/request_budget.py:70` | 最后请求去掉输出工具并强制text | 开启时保留输出工具，关闭不变 |
| `src/deep_agent/agent/message_persistence.py:205` | 普通调用接受过滤可能剔除output/retry | 专用输出保留与来源完整性 |
| `src/deep_agent/agent/events.py:280` | 仅现有Function/Part事件映射 | 明确支持OutputTool事件 |
| `src/deep_agent/db/event_codec.py:21` | 四类MESSAGE_EVENT_TYPES | output/attempt不进SDK消息类型集合 |
| `src/deep_agent/db/conversation.py:289` | append_lifecycle仅compaction | 专用不可变attempt追加路径 |
| `src/deep_agent/conversation/models.py:67` | Assistant项只有text/thinking | 新structured_output投影 |
| `src/deep_agent/conversation/projector.py:59` | 当前非消息事实被跳过 | 处理接受事实，失败留轨迹 |
| `src/deep_agent/agent/context/sanitization.py:63` | 模型投影删除metadata | Deferred从原始Turn事件取快照 |
| `src/deep_agent/api/v1/deps.py:130` | 解析Deferred来源前构造Runner | 输出最终模式在Session锁内解析Turn后装配 |
| `src/deep_agent/diagnostics/sessions.py:28` | REAL_EVENT_TYPES白名单 | 新事实可查，但不重复计统计/异常 |
| `src/deep_agent/channels/service.py:417` | 非字符串final.output置空 | 渠道层通用JSON投递，不改公共原值 |
| `src/deep_agent/security/diagnostic_users.py` | 已有有界批量账号资料解析 | 更新人复用Username和权限隔离 |

现有Event表event_type为String而非数据库枚举，新output/attempt无需再加表或事件列。SDK本地2.46.0的StructuredDict只提供dict类型和模型Schema，不验证内部required，也不支持递归$defs；本设计因此保留完整服务端验证和明确的引用限制。

## 8. 文档核对与未完成事项

首轮完整设计收尾已完成以下文档检查，此类检查只验证文档自洽，不算功能测试；随后三方对齐的增量另见第10节：

- 课题5份Markdown的内部相对链接和代码围栏均有效。
- 17段JSON示例可解析；合同Schema自身合法，5个允许组合通过，1个缺内部required示例按预期失败。
- 测试计划258个用例ID全部唯一，含12组跨版本安装矩阵；这些是待执行计划，不是258项已通过测试。
- `git diff --check`通过；只新增/修改本次design文档及相关入口，正式源码、测试、依赖和迁移均未修改。
- 排除本次docs/design变化后，原有已跟踪差异SHA256仍为 `62bac4e044d8b172a8b9fc9c036fb756c5a53f9bcbfbd74c103517f668a271f7`；原未跟踪项目保留，没有清理或覆盖。
- 两个独立只读审阅任务核对安装/数据库与运行/协议：已修正Skill缺失引用兼容、包内容摘要定义、更新人降级保护、PATCH最终状态、旧Turn/损坏快照区分、SDK ack先于接受提交、未分发工具投影与统计、纠错预算检查点、HTTP会话身份和数据库/公共状态映射。

明确未执行：

- 新版真正导出包→冻结旧版完整预检/安装/回读；旧包→新版完整安装覆盖。
- 数据库012及生产DDL影响调查、迁移往返和新表备份恢复。
- 正式Runner普通/流式/Deferred恢复/取消/预算/压缩贯通。
- 新公共协议、语义事件、History和Live跨进程恢复。
- 新Console正式保存、更新人列表/卡片和账号查询闭环。
- 真实Provider结构化请求、渠道JSON投递、合同/BOM客户端适配。

全部列入测试计划，开发后逐项记录，不以本次12条旧解析器测试和6条框架测试替代。

## 9. 资料来源

现状结论以本次本地源码/安装包为准。代码图谱查询命中旧目录和旧行号，发现其尚未覆盖当前安装模块后，回退到当前源码核对；没有把索引快照当线上部署事实。

通过Context7核对官方输出文档，并只读打开[Pydantic AI Output](https://pydantic.dev/docs/ai/core-concepts/output/)和[Deferred Tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)。官方当前文档会继续变化，具体Hook和流式调用语法以本次实际安装2.46.0源码与上述运行结果为准；未自动升级到最新文档版本。

依赖变更：无；提交、推送、Tag、部署、DDL执行：无。

## 10. BOM与租赁三方设计对齐增量

同日按用户明确授权，向指定的“峰梅｜智能填报BOM”和“达美乐｜租赁智能填报”会话发送两轮设计评审消息，并读取双方最终确认。双方均确认可按修订设计接入，无剩余设计阻碍；具体会话/轮次/消息ID及产品边界见[联合设计确认](client-design-alignment-20261022.md)。未发送实施、发布或改客户端的通知。

本次增量修订：

- 明确History结构化item_id恒等于跨SSE/Live/final的output_id；混调未分发调用不进入普通History，不作为查询/原生审批证据。
- 区分平台运行身份与BOM/租赁的业务turn_id、schema_version、revision/hash等；accepted不代表采用、授权或保存。
- 记录BOM根包直返、租赁组合产物与静态互斥、文件复核引用、两层纠错和客户端适配入口。
- 明确租赁首版过程进度＋accepted后完整显示，不计为保留原字段级逐段预览；两项目普通澄清不自动转Deferred。
- 追加JNT-01～28，测试计划现有**286个唯一编号**。全部仍待正式实施后执行，两个客户端未开始适配。

增量检查实际结果：课题6份Markdown链接与围栏通过，18段JSON示例均可解析，286个用例ID唯一；租赁外层静态互斥Schema检查中6种允许组合通过、3种冲突组合按预期拒绝。此处只验证外层组合，不是完整租赁/BOM生产Schema已生成或编译通过。

`git diff --check`通过，排除docs/design后原跟踪差异SHA256仍为 `62bac4e044d8b172a8b9fc9c036fb756c5a53f9bcbfbd74c103517f668a271f7`。未改正式代码/测试/依赖/迁移，原19项未提交工作和未跟踪数据保留；没有真实模型、业务写入或线上操作。三方确认后停止在设计阶段，客户端后续由用户另行安排。
