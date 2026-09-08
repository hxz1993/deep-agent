# DeepAgent 文档索引

`docs/` 按用途组织当前说明、设计与历史归档，当前运行和发布入口不放进归档目录。

| 目录 | 类型 | 内容 |
| --- | --- | --- |
| [`guides/`](guides/README.md) | **说明** | 当前协议、使用/开发指南、实现经验和排障记录 |
| [`design/`](design/README.md) | **设计** | 当前设计、研究依据、历史决策和交互原型 |
| [`design/TODO.md`](design/TODO.md) | **待办** | 跨课题唯一待办清单、优先级与来源链接 |
| [`archive/jenkins/`](archive/jenkins/) | **归档** | 原始 Jenkins 脚本，按字节保存，仅供历史对照 |

阅读规则：

- 要了解当前系统如何使用、如何对接，先看“说明”。
- 要了解为什么这样设计、未来准备怎么改，再看“设计”。
- 设计文档不等于已经实现；每篇设计文档应以自身状态说明为准。
- 未完成事项只在 `design/TODO.md` 维护，课题文档负责解释来源和边界。
- 当前 Jenkins 网关、仅 API 与诊断入口见 [`deploy/jenkins/`](../deploy/jenkins/README.md)；归档不是可选发布模式。
