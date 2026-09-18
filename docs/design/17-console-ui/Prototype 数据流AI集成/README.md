# 数据流节点 AI 辅助用模型集成原型

独立交互原型，不调用真实业务接口、不写数据库。刷新后恢复未配置状态。

- 编码：`deeppipeline-node-ai-model`，名称：数据流节点 AI 辅助用模型。
- 固定目录展示三项集成；没有初始配置记录，首次保存有效选择才创建模拟记录，清空后删除模拟记录。
- 保存模型 ID 和温度；Provider 图标与模型名仅展示，默认 placeholder 为“请设置”。
- 电子表格、DeepCube 同款弹窗，均提供适用功能和配置接口示例，具体能力受 Agent 配置约束。
- 拟议 GET `/api/v1/integrations/deeppipeline-node-ai-model`，返回 `{code, settings}`，settings 包含四个 OPENAI 环境变量同名字段。
- 数据流后端继续调用模型，不是 DeepAgent completions 代调用。密钥响应必须限定授权后端服务身份，不沿用普通用户可读范围；控制台只操作模型 ID，不回显密钥。
- 仅展示兼容现有 OpenAI Chat Completions 调用的模型是后续实现约束。温度是专用集成设置，不代表普通 Agent 支持温度。
- 接口协议、错误状态及保存示例见弹窗“接口示例”；全部 ID、模型和响应均为示例，接口尚未新增。
