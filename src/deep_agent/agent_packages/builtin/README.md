# 自带 Agent 安装包

这里保存随部署交付的完整 Agent ZIP，属于 Agent 安装功能的自带资源，与解析和安装服务一起维护。每个 ZIP 集中保存 Agent、关联的上传 Skill 和自定义 UI 工具，与 Console 导出、上传安装使用同一种格式。

当前两个包于 **2026-09-14** 从 Test 环境对应 Agent 的官方导出接口同步，导出服务版本为 `0.10.0`。它们不会在启动时自动安装，只有用户显式选择覆盖安装才更新目标 Agent；替换 ZIP 文件本身不修改数据库。

| 安装包 | Agent | 包内 Skill | UI 工具 |
| --- | --- | --- | --- |
| `deeptable-assistant.zip` | 电子表格智能助手 | `deeptable-assistant-workflows` 1.4.4、`deeptable-intelligent-analysis` 2.10.1 | `ui:deeptable`：澄清、分析导航、个人报表确认 |
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

源环境：`https://test.deepfos.com/deepfos-server/deep-agent-server1-0`。
通过 `GET /api/v1/agents/{id}/export` 获取完整 ZIP，原样保存；不手工拼装或修改包内文件。来源 Agent ID 只用于追溯，不是安装目标 ID。

| 安装包 | Test Agent ID | SHA-256 |
| --- | --- | --- |
| `deepcube-assistant.zip` | `39e5a100-72d4-4a0a-8558-05f0c3af6bc2` | `5f35ce440e95f30709b9b8d8e99d9f61022ff018e09e3be006b0103bac1190bb` |
| `deeptable-assistant.zip` | `812dba72-458b-474c-bb5f-ed684eac0f69` | `aca42bb5079c36b51aaea91b3582dd39c5275cafcdabe736828c9a54720cfa3d` |

本次同步包含 Test 当前提示词、上传 Skill 原包、完整 UI 工具定义和 `ui_tool_selection`，保留模型名称参考。Skill 版本号与原包相同，但文件内容以当前 Test 导出为准。包不带 Provider/MCP 凭据、会话或渠道配置。

### 原暂行版来源（已替换）

- 电子表格原取自 `element-deep-table` 提交 `7c0213b71b3d0a27f364d6e9d3826cc806a9ad34` 的 `src/pages/sheet/use/components/aiPanel/agentAssets/`；提示词 `deeptable-unified-agent-v82`，UI 清单 `deeptable-ui-tools-v3.6.0`。
- DeepCube 原取自 `element-python3` 提交 `0605cb57a8f19f1edf68ed3530962c43d7b74032` 的 `packages/page/src/components/layout/content/DeepCubeChatbot/agent-config/`；提示词 V34，资产清单版本 7。

完整包协议和接口见[Agent 安装包](../../../../../docs/guides/agent-packages.md)。
