# 结构化输出运行边界独立验收

`tests.integration.test_structured_output_boundaries` 包含 **8 个测试方法、32 个场景**。最后与新增的 16 个 Runner 边界方法组合运行，**24 个方法全部通过，0 skip，32.665 秒**；另外独立 API 越权 Deferred 方法 1 项通过，1.423 秒。

| 场景 | 证据 |
| --- | --- |
| 普通工具、真正 UI Deferred、未知工具与输出混调 | 3 类 × 两种顺序 × normal/stream 共 12 场景；完整响应拒绝后只产生一次修正，再成功输出；普通工具计数为 0；History 无工具/审批项；SSE 不发布被拒工具调用；提交对应假 UI 回执被拒。 |
| 真实 MCP 混调 | 独立回环 streamable HTTP MCP 服务器，两种顺序 × normal/stream 共 4 场景；服务端工具调用记录文件始终不存在，证明没有到达工具函数；History 无伪执行。服务器 finally 关闭。 |
| 多个输出调用 | 不同 ID、重复同 ID × normal/stream 共 4 场景；整响应只计一个失败尝试，下一请求可接受，不能取第一个结果。 |
| 共享修正预算 | 纯文本、损坏 JSON、完整 Schema 失败以两种排列 × normal/stream 共 4 场景；第三个被拒响应后停止，模型请求总数 3、一次 execution/error、3 次失败 attempt，无 accepted；文字候选不进入普通 History、不通过 SSE text 泄漏。 |
| 历史 Pending 与新开关 | 旧 Pending 恢复时当前定义 loader 明确抛错；恢复仍成功返回旧文本，没有新增 output_info/output attempt。 |
| 损坏引用 | 正式创建的 Deferred Pending 包含真实契约引用后，只破坏引用；恢复在消费答案前失败，不再请求模型，同一 Deferred 回执仍通过待处理校验，History 保持 awaiting_input。 |
| 缺快照、坏指纹 | 两个场景分别删除原始快照、改坏 schema_digest；均明确拒绝恢复而保持同一 UI 答案尚可提交。 |
| 压缩后真实进程重启恢复 | 四个场景：首个失败分别为输出 Schema 失败或纯文本缺少输出，均先用掉 2 次修正并暂停 UI，再通过正式 compaction projection 把原始契约移出模型投影；以全新 Python 子进程读取同一隔离 SQLite 恢复，当前 Agent 定义 loader 禁止调用；输出工具 Schema 与原始模型请求深度相等。有效答案在下一请求接受 attempt 3；无效答案下一请求即耗尽，不重置额度。 |

本组发现并回归了一项实际问题：**同一响应多个输出使用重复 tool_call_id 时，原先源 Part 编码把重复调用折为同一持久化身份，失败 attempt 保存报“Output source response is not durable”。** 主实现已对同一响应内重复 Part key 添加稳定位置后缀，保留两个真实 Part；本组 normal/stream 重复 ID 回归已通过。

本组运行真实 SDK/Runner、Event Store、SQLite 和独立 MCP 服务器；模型使用确定性 FunctionModel，不将其计为在线 Provider 或两个客户端业务写入验收。所有夹具均为合成内容，临时库/目录由测试 finally 清理。没有改动正式 Runtime 源码，发现问题交回主实现修复。

## 追加 Runner 与 Deferred 门禁

证据文件为 `tests/integration/test_structured_output_remaining_boundaries.py`。表中普通/流式两路径均运行，流取消和公共 SSE 归属用例按对应专用路径执行。

| ID | 实际断言 |
| --- | --- |
| RUN-04/17 | 上限 1/2/3/None 精确计数；3 和 None 分支两次成功 lookup 后再输出；最后请求仅保留输出工具；无上限仍最多 2 次修正。 |
| RUN-13 | Provider 原生搜索 Call/Return 与最终输出混调被拒；同响应 host 工具零执行，不进入普通 History。夹具明确表示远端搜索已执行，不声称能撤销 Provider 动作。 |
| RUN-16 | 最后请求返回非法输出、混调、普通调用时失败，host 计数 0，模型无额外请求。 |
| RUN-18 | 真实 Runner/SDK capability 边界计入摘要请求，2 次预算只发 1 次主要请求并保留输出工具；原始预算检查点为 2，恢复不重复计数。此确定性计费边界不冒充在线摘要 Provider。 |
| RUN-20 | 模拟 Provider HTTP400 拒绝工具或 Schema：单请求清晰失败，定义原值不变，无原生或文本退回，无 accepted。 |
| RUN-21 | Tool.prepare 在第一次查询后才披露与输出同名工具；SDK 在第二次 Provider 请求前冲突失败，错误目标函数零执行。 |
| RUN-23 | 普通 lookup 第一次 ModelRetry、第二次成功，保留原 tool_call→removed→call→result SSE 生命周期；最终输出 attempt 1，未消耗输出纠错。 |
| RUN-24/DFR-17 | 同会话开→关→另一 Schema 开启，穿插第二会话，所有新用户 Turn 使用当前定义；旧结果不在本轮 SSE 重发，两个接受事实名称和身份独立。 |
| RUN-26 | 第三个非法候选耗尽后原始响应及预算 3/剩余0 落库，失败事实齐全；没有给第三次失败创建 SDK RetryPrompt。 |
| SCH-21 | 损坏 JSON、重复键、NaN、Infinity 真实 Runner 均拒绝，下一次正常输出才接受；错误不泄露候选值。 |
| DFR-02 | UI→查询→第二 UI→输出跨 3 个 Run、同一个 Turn；中间 lookup 真实执行一次，所有请求 Schema 深度相等，恢复禁止读取当前定义。 |
| DFR-03 | 用户拒绝和取消的真实 UI 回执原样到模型，无伪造同意，接受 JSON 的 false 保留。 |
| DFR-09 | 未知、旧、混合、重复 call ID 在执行外拒绝，事件深度不变；另一个真实 HTTP 客户端跨用户答 UI 得 404，不请求模型、不消费原 Pending，原用户随后可成功恢复。 |
| DFR-13 | 已收到半截 JSON 时通过官方取消 token 停止流，0 accepted/最终输出，active registry 清空。 |
| DFR-15 | 真 UI 恢复、接受事务提交后、final 前模拟传输取消：History 仍为 completed，JSON 和 output_id 完全相同，无 execution/error。 |
| DFR-18 | 预建空会话无假用户事件；第一次实际带附件输入保存唯一冻结快照，附件归属及显式标题正常，普通与流式一致。 |
| DFR-19 | 上表真实压缩与新进程 4 变体，包含纯文本首拒绝；输出调用扫描不能重置已耗预算。 |

## 全量启动后的独立补验

`tests.integration.test_structured_output_final_deferred_gates` 是最终全量启动后新增的独立文件，单独 **2 个方法、6 个分支、0 失败、0 skip、3.271 秒**。不能将此单跑写成已包含在当时已启动的全量中。

- DFR-05：真正 UI Pending 期间调用管理员 PATCH，分别传 `structured_output_enabled:false` 保留草稿，或 false 加 `output_config:null` 删除定义；专用 GET 独立回读确认，然后经正式 SSE 恢复。两种都按原名称/Schema 完成，原先失败仍占预算，最终 attempt 2。
- DFR-14：normal/stream 各在实际 JSON Schema 子进程收到灾难性正则输入且运行中取消，以及完整 worker 成功、真实 SDK after_output_validate 完成后 accept 提交前取消。四分支都保留之前失败事实，0 accepted，子进程 returncode 落定且 waitpid 确认已回收，运行登记清空。
