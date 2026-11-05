# 结构化流程助手：BOM与合同等业务

## 先设计业务契约

确认输入基线、业务schema_version、输出状态、引用、完整快照/稀疏补丁、澄清、人审与保存的边界。完整Schema由管理员保存在Agent输出配置，不让客户端每次传Schema，也不用Markdown围栏代替原生JSON。

流程输出应表达“这是建议/澄清/复核结果”，而不是写一个success=true就代表保存完成。BOM可以有明确的bom_result业务根；合同可以组合合同、patch、clarification与review。不同产品保留自己的契约，不由通用平台自动加一层统一业务外壳。

## 可落地的Schema规则

- required只表达真正必须的字段；省略、null、空字符串、false与0保持不同语义。
- 完整对象与补丁互斥、多个结果分支恰好一个非空等规则应写进oneOf/条件约束，不能只写提示词。
- 需要证据的数组可以设置minItems；数量、字符长度、类型、枚举和additionalProperties按正式业务定义设置。
- 动态ID是否来自本轮来源、来源是否足以支持结论、基线Hash是否匹配，仍需客户端业务校验；静态Schema不能证明这些事实。

示例：两种业务产物恰好一个非空（教学片段，不替代完整Schema）：

```json
{
  "type": "object",
  "properties": {
    "snapshot": {"type": ["object", "null"]},
    "patch": {"type": ["object", "null"]}
  },
  "required": ["snapshot", "patch"],
  "additionalProperties": false,
  "oneOf": [
    {"properties": {"snapshot": {"type": "object"}, "patch": {"type": "null"}}},
    {"properties": {"snapshot": {"type": "null"}, "patch": {"type": "object"}}}
  ]
}
```

Schema预算、引用展开和供应商兼容要在目标平台验证。不要删除正式约束换取模型更容易通过，也不要用固定样例代替复杂业务结构。

## 四个完成点

```text
平台accepted
  → 客户端核对来源/版本/引用/补丁并采用为草稿
  → 用户批准具体版本
  → 业务执行并独立回读保存结果
```

每个阶段分别记录状态。SSE `output`与`final`可能指向同一结果；以output_id去重，不能重复填表或重复应用patch。相同ID若返回不同值应拒绝，不覆盖原始接受事实。

平台格式失败在同Turn内自动纠正。`output(accepted=false,status=error)`不是结束，继续消费流；真正终态才决定完成/取消/失败。客户端不能通过另发Chat重置额度。格式通过后的业务拒绝另走有界业务修复，并关联被拒output_id和当前基线。

版本边界：上述纠正流程在{{DEEP_AGENT_VERSION}}中已存在；本轮正在开发的细粒度纠错反馈尚未发布，不把它作为{{DEEP_AGENT_VERSION}}的新能力承诺。可读取的错误字段以目标服务实际事件为准。

## 首轮、续轮与历史

首轮可能返回完整snapshot；续轮只返回明确的增量patch，缺省产物不等于清空旧值。引用签名、revision和源文档Hash可用于确认补丁仍对应原始输入。人工编辑后旧批准是否失效应按业务语义计算。

实时与History使用相同领域校验/渲染入口。刷新、断流和重新打开仅恢复已有结果，不再次运行模型、patch、批准或保存。旧历史保留原output身份和原值，不用当前Schema重新校验后改写历史。

允许文本输出默认false。仅当产品确实接受纯文字完成时开启allow_text_output；文字结束没有结构化接受身份，不能当作BOM、合同等业务产物。伴随说明可存在，业务采用仍只认真实structured_output/output_info。

## 最小回归矩阵

完整首轮、续轮补丁、缺字段后自动纠正、互斥错误、缺证据、重复/冲突通知、final-only消费、断线只读恢复、取消、真正Deferred、旧历史读取以及业务保存独立回读。记录每次格式失误，即使最终纠正成功；不要用最终100%成功掩盖首次失败率。
