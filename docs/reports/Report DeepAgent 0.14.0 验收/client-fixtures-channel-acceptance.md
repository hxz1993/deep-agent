# 客户端设计样例与渠道输出验收

## 平台业务结构夹具

`tests.integration.test_structured_output_client_contracts`：3 项通过，normal/stream 均使用真实 SDK、Runner、现有 Event Store 和隔离 JSON Schema 子进程。模型为确定性 FunctionModel，没有改动两个业务客户端、线上 Agent、Prompt 或 Skill。

这些是三方确认文档对应的**最小合成协议样例，明确不是两项目的完整生产 Schema**：

- BOM 用 `bom_turn_v3` 输出编码，结果仍为原业务根对象；保留业务 `schema_version: 3`、turn_id、base_revision/base_hash、revision/source_signature、project/version、summary/snapshot/patches、question_set/question_receipts/review/next。首轮 snapshot.source_model/rows、patches=[] 和续轮 snapshot=null+稀疏 patches 使用相同工具定义。业务 turn_id/hash/patch_id 不由平台 output_id/turn_id 替代；正式行方案 null question_set、空 receipts 和 needs_adjustment 数据不会自动变成 Deferred。
- 租赁保留 `lease_contract/lease_contract_patch/lease_clarification/lease_review/lease_review_file` 编码。完整合同+澄清、patch+澄清、review+patch、仅正文、仅澄清、文件引用及 `{}` 都使用同一套全 optional 外层定义。内部合法 null、false、数值 0 保持原生类型，业务问题 revision/submission/receipts 原值不改。
- 完整合同与 patch、review 与 review_file 的两项静态互斥由真实 worker 拒绝；真实 Runner 在预算内纠正后仅保存/回放接受结果。缺 snapshot.source_model、首轮同时返回 patch、未声明可空的合同返回 null，同样由 worker 拒绝。
- 普通与流式接受结果在 History 中深度相等，item_id 与 output_id 一致，输出编码只在元信息中，不凭空增加业务包装。

可用于 JNT-01/02/03/10/11/12/13/14/15/16/17/18/26 中**平台格式及数据保留部分**的证据。下列项目继续明确列为后续客户端验收，不能将样例通过自动写成整格通过：

- 完整生产 Schema 的编译/展开/真实 Provider 线包（JNT-27）。
- BOM/租赁的业务签名、revision/hash、真实节点/父链/目标、业务采用与补丁幂等。
- submission_id/rules_version 在客户端真实请求中的位置、提交幂等及恢复规则。
- 审批、不可变计划、真实人审、填写、保存、生成、执行回执与完整业务读回（JNT-05/07/08/09/19/22/23/28 等）。
- 真实文件权限、接近 5 MiB 下载、过期快照、错误 Hash、文件失效与超时。当前只验证原引用字段可以保留，不验证文件真实存在。
- 客户端实际接收器、回放/采用一次、交互体验和旧挂起只读逻辑。

## 渠道结构化输出

`tests.unit.test_channel_gateway` 加 `tests.unit.test_channel_message_finalization` 31 项通过；与上述 3 项合计 **34 项、0 失败、0 skip**，最后组合约 8.1 秒。

修改只作用于 `final.status=completed` 且包含 `output_info` 的路径；关闭 Agent 的原有文字、预览、命令、投递和主动消息收尾沿用原逻辑。

| 要点 | 已验证行为 |
| --- | --- |
| CHN-02/03 | 英文输出编码加通用 JSON 代码块；根对象/数组/字符串/false/0/null/空对象/空数组均保留。 |
| CHN-04 | 依据 adapter.message_capabilities().text_limit 的 UTF-8 字节限制分段，代码块标明英文编码和序号；每段闭合，Unicode 不拆字符，长中文/emoji 尾部和完整 JSON 可重建；API/数据库原生值不被修改。 |
| 首段与后续段 | 首段复用原 reply_id 并 finish，关闭原 loading；后续段通过现有 send_component，固定当前账号/Peer/消息/线程，等待本段 ACK 后再发下一段；原生 SDK为每条创建独立消息/请求标识。模型不能指定投递目标。 |
| 失败与重放 | 第二段 ACK 不确定立即停，不发第三段、不重复首段、不自动重试；channel processing 保持 completed、delivery 标 failed；重复接收同一消息不再调用模型或投递。单段沿用现有 outbound 的 120 秒截止。 |
| CHN-08 | 输出尝试候选不作为回复；Pending/error/interrupted 即便携带候选或 output_info 也不投递它，只沿原状态提示。 |
| CHN-09 | 复用 job 中已确认发送的文字哈希；已主动发送相同 JSON 全文/格式化全文或相同分段时跳过，不把未知发送冒充成功；不建新表或新的持久化回执。 |

FakeAdapter 测试走真实 ChannelService.receive→队列→SSE consumer→投递→数据库状态，不是只测字符串拆分函数。没有向真实人/群发送验收消息；线上各渠道权限、频率与真实消息展示不是本组通过项。

后续补充 `tests.integration.test_structured_output_channel_lifecycle` 一项（文字/附件两分支）通过，与最终存储 16 项组合为 **17 项、0 失败、0 skip、3.687 秒**。该项把真实 SDK/Runner、JSON Schema 子进程和 Event Store 接入真正 ChannelService consumer，再经 FakeAdapter 验证 CHN-05/06/07：`/new` 保留旧接受历史，预建新会话不调用模型、不产生输出；第一条文字或实际上传的合成附件按当前配置运行；同会话后续关闭输出返回普通正文，再换 Schema 开启返回新 JSON，不重发旧值；`/stop/status/help/whoami` 仍走原控制通路，不调用模型、回复不经 Schema 包装。没有使用真实外部联系人或群。

## SDK 协议依据

核对了当前安装的官方 `wecom-aibot-python-sdk` 1.0.2（Python 包名 `aibot`）源码及[官方 WSClient 文档](https://github.com/wecomteam/wecom-aibot-python-sdk/blob/master/_autodocs/api-reference/wsclient.md)：

- reply_stream 透传收到的 frame.headers.req_id；资料未保证同一 frame 更换 stream_id 可以创建多条独立消息。
- 已有适配器 send_component(text) 调用 SDK send_message(chatid, markdown)，SDK 每次生成新的 SEND_MSG req_id。
- 因而后续段使用现有主动发送路径，不猜测额外回复协议，不改适配器源码，不新增依赖。

依赖变更（运维）：此补充无生产依赖变更；复用现有标准库、SDK、Adapter 和消息能力配置。
