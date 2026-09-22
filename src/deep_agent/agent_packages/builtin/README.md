# 自带 Agent 安装包

这里保存随部署交付的完整 Agent ZIP，属于 Agent 安装功能的自带资源，与解析和安装服务一起维护。每个 ZIP 集中保存 Agent、关联的上传 Skill 和自定义 UI 工具，与 Console 导出、上传安装使用同一种格式。

电子表格包于 **2026-09-22** 更新为 Alpha 正式导出的 V91 配置资产；DeepCube 包保留 **2026-09-14** 从 Test 官方接口导出的版本，导出服务版本为 `0.10.0`。它们不会在启动时自动安装，只有用户显式选择覆盖安装才更新目标 Agent；替换 ZIP 文件本身不修改数据库。

| 安装包 | Agent | 包内 Skill | UI 工具 |
| --- | --- | --- | --- |
| `deeptable-assistant.zip` | 电子表格智能助手 | `deeptable-assistant-workflows` 1.4.5、`deeptable-intelligent-analysis` 2.13.2、`consolidation-model-analysis` 1.1.3 | `ui:deeptable`：澄清、分析导航、个人报表确认 |
| `deepcube-assistant.zip` | DeepCube助手 | `deepcube-script-authoring` 1.0.2 | `ui:deepcube`：澄清、配置填充 |

电子表格另引用运行环境内置的 `deepfos-readonly-analysis` Skill。两个包均引用通用工具和 Python 沙箱；包内模型参考为 `deepseek-flash`（Provider 类型 `deepseek`），安装优先匹配目标环境同名可用模型，无匹配时选择首项或留空；MCP 只保留编码参考，没有对应项也不影响安装。内置资源不复制进安装包；UI 工具携带完整定义，实际 widget 渲染由目标组件提供。

## 目录和格式

```text
src/deep_agent/agent_packages/builtin/
├── README.md
├── deeptable-assistant.zip
└── deepcube-assistant.zip

每个 ZIP 内部：
├── manifest.json
├── agent.json
├── ui-tools.json
└── skills/<skill-code>.zip
```

ZIP 文件名去掉 `.zip` 后就是自带包编码：小写字母开头，允许字母、数字、短横线、下划线，最长 64 字符。目录只保留完整 ZIP 和说明，不保留散开的提示词、Skill 或 UI 定义副本。

服务从此模块相对路径读取 ZIP，不依赖进程工作目录。自带与上传来源共用解析、预检和安装逻辑，采用相同大小、文件数量、路径和内容校验。README 和子目录不进入安装列表；无法读取或无效的 ZIP 返回目录错误，符号链接不允许作为安装包。

发布需保留 `src/deep_agent/agent_packages/builtin/`。当前 Jenkins 的完整源码 `git archive` 会携带已纳入版本控制的 ZIP；无需解压或在启动时写入数据库。

## 替换安装包

1. 准备同格式的完整 ZIP，使用包解析器或 Console“上传安装包”预检内容。
2. 将验证后的 ZIP 原子替换到相同文件名；先写同目录的临时文件，再重命名，避免读取到半写入内容。
3. 再次打开“安装 Agent → 自带安装包”检查目录与内容。替换只影响后续安装；已打开的旧预检在包内容变化后会返回冲突，要求重新预检。

每次同步后更新本说明的版本、来源和清单，并运行 `tests/integration/test_builtin_agent_packages.py`。测试包使用隔离测试目录，不进入此目录。

## 当前来源及校验

DeepCube 源环境：`https://test.deepfos.com/deepfos-server/deep-agent-server1-0`，通过官方导出接口获取完整 ZIP，保持原样。电子表格通过 Alpha 官方导出接口获取完整 ZIP，保持正式导出原文并使用同一解析器校验；下列来源仅用于追溯，不是安装目标。

| 安装包 | 来源 | SHA-256 |
| --- | --- | --- |
| `deepcube-assistant.zip` | `39e5a100-72d4-4a0a-8558-05f0c3af6bc2` | `5f35ce440e95f30709b9b8d8e99d9f61022ff018e09e3be006b0103bac1190bb` |
| `deeptable-assistant.zip` | Alpha 正式 Agent / V91 | `57f24aff7d68a4e4c8218841e38e7c26a933c02547a9a85133e91561f9ce51a5` |

电子表格源环境为 `https://alpha.deepfos.com`，正式 Agent `812dba72-458b-474c-bb5f-ed684eac0f69`，通过官方接口于 2026-09-22 导出；包内仅保留可迁移引用，不保存目标绑定 ID。

- 提示词：`deeptable-unified-agent-v91`，已与 View Planning 当前源码核对一致（忽略首尾空白）。
- UI：三个工具完整定义及精确选择范围保持一致，未更改 UI 协议。
- Skill：新增合并分析专项 Skill 1.1.3；工作流 1.4.5、智能分析 2.13.2。19 个交付文件与源码核验，智能分析三个 Markdown 仅有空白差异，保留正式导出原文；`deepfos-readonly-analysis` 仅保留内置引用。
- `manifest.exported_from` 保留正式服务导出值 `0.10.14`；模型参考为 `deepseek-flash/deepseek`。
- 相比 V85，补充合并分析路由、证据边界、集中发现工具，以及按实际 Schema 使用 `summary`、精确 Sheet ID 和 `member_script`；明确保存态与实时态的区别。
- ZIP 为 77,574 字节；包不带 Provider/MCP 凭据、会话、渠道配置或前端 widget 渲染代码。

预制目录名称旁展示包内提示词标题明确标注的版本：电子表格 V91、DeepCube V34。无明确版本不显示；这不是服务版本或整个安装协议的版本。

### 原暂行版来源（已替换）

- 电子表格原取自 `element-deep-table` 提交 `7c0213b71b3d0a27f364d6e9d3826cc806a9ad34` 的 `src/pages/sheet/use/components/aiPanel/agentAssets/`；提示词 `deeptable-unified-agent-v82`，UI 清单 `deeptable-ui-tools-v3.6.0`。
- DeepCube 原取自 `element-python3` 提交 `0605cb57a8f19f1edf68ed3530962c43d7b74032` 的 `packages/page/src/components/layout/content/DeepCubeChatbot/agent-config/`；提示词 V34，资产清单版本 7。

完整包协议和接口见[Agent 安装包](../../../../docs/guides/agent-packages.md)。
