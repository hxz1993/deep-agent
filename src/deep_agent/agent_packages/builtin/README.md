# 自带 Agent 安装包

这里保存随部署交付的完整 Agent ZIP，与 Console 导出、上传安装使用同一种格式。唯一目录为 `src/deep_agent/agent_packages/builtin/`，不保留展开的提示词、Skill 或 UI 定义副本。

2026-11-05 电子表格预制包已从 V92 更新为 **V124**，来源为独立电子表格前端的已提交资产；DeepCube 包保持不变。替换文件只影响后续安装，不在启动时安装，不更新既有 Agent、Skill、模型或集成绑定。

| 安装包 | Agent | 包内 Skill | UI 工具 |
| --- | --- | --- | --- |
| `deeptable-assistant.zip` | 电子表格智能助手 V124 | 常规工作流 1.4.18、智能分析 2.18.5、合并分析 1.1.3、外部金融对标 1.1.8、Wind 0.1.0、商务财务图表 2.0.6 | `ui:deeptable`：澄清、分析导航、个人报表确认 |
| `deepcube-assistant.zip` | DeepCube助手 V34 | `deepcube-script-authoring` 1.0.2 | `ui:deepcube`：澄清、配置填充 |

电子表格另外引用内置 `deepfos-readonly-analysis`、`file-reading-verification` Skill，内置正文不复制进包。Skill 上传包均为真实过滤后的交付文件，不含本地测试、样例答案、旧原型、缓存或凭据。

## 当前电子表格来源

- 仓库：`front-end-development-group/element-deep-table`。
- 分支：`feat/ai-assistant-panel-ruge-0806`。
- 提交：`4334b0fd325d528fd19feb166dde14a25a7ad1f5`。
- 源目录：`src/pages/sheet/use/components/aiPanel/agentAssets/`。
- 先调用来源仓库 `scripts/prepare-agent-assets.mjs` 的 `loadAssets()`；该检查器负责必需文件、工具选择一致性和过滤规则。再将六份展开 Skill 分别压成 ZIP，以 DeepAgent 正式包模型规范化配置、解析整个包。
- 来源共 45 个交付文件；外层仅 3 个 JSON 和 6 个 Skill ZIP。图表包保留原 Python 图形算法、主题及平台执行入口，不携带演示 HTML、G2 库或开发测试。
- 提示词 `deeptable-unified-agent-v124`；SHA256 `3317e3fd1bca787d510a4c4ead98370855ab8e108769e91ea07f7ef2e4b3207c`，与来源逐字一致。
- `model_reference` 按来源保持未绑定（null／缺省），不沿用旧包固定的 `deepseek-flash` 参考。安装时由目标目录选择模型；显式 null 可安装为无模型，聊天仍须选择可用模型。
- MCP 引用为 `mcp:context7`、`mcp:deepfos`、`mcp:wind-stock`、`mcp:wind-edb`。本助手金融数据仅关联 Wind，不携带已解除关联的 iFinD；不删除其他 Agent 的共享定义。
- 产品模型规范化移除已退役 `builtin:general`；时间能力由新版运行时默认提供。旧 `ui_tool_selection` 不写入发布包；三个完整 UI 定义保留，目标按 `ui:deeptable` 整包启用。
- 关联 `builtin:ptc`、`builtin:sandbox`、`builtin:document`。MCP、模型和这些工具的部署／授权仍由目标环境配置；包安装成功不等于所有外部依赖已可用。

| 安装包 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `deeptable-assistant.zip` | 158,814 | `ed86a5ec4f9cb6d9b3c5829a4774b356b386ccc1075955baa7875861abc74881` |
| `deepcube-assistant.zip` | 保持原包 | `5f35ce440e95f30709b9b8d8e99d9f61022ff018e09e3be006b0103bac1190bb` |

DeepCube 来源仍为 2026-09-14 Test 正式导出 Agent `39e5a100-72d4-4a0a-8558-05f0c3af6bc2`，导出服务版本 `0.10.0`；模型参考为 `deepseek-flash`（Provider 类型 `deepseek`）。来源仅供追溯，不是安装目标。

## 可重复同步与验证

在 DeepAgent 仓库根运行，来源路径按实际检出目录提供：

```bash
.venv/bin/python scripts/sync_deeptable_agent_package.py \
  --source-repo /path/to/element-deep-table --write
.venv/bin/python scripts/sync_deeptable_agent_package.py \
  --source-repo /path/to/element-deep-table
PYTHONPATH=src .venv/bin/python -m unittest tests.integration.test_builtin_agent_packages
```

脚本要求源资产与整理器无未提交修改，使用确定性 ZIP 元数据；缺省只核对，`--write` 经过完整解析后原子替换同名包。不修改来源仓库、不读取登录配置、不连接平台、不安装到数据库。每次同步须更新本说明和真实版本断言。

隔离回归覆盖预制目录、上传／预制两种预检、新建、上传覆盖同一 Agent、模型空值、完整 UI 定义、六份 Skill 安装后的全部运行文件，以及导出后内容一致性。测试使用临时 SQLite、独立 FileStore／Skill 运行目录，不覆盖本地 8000 的正式资源。

服务从模块路径读包，与工作目录无关；同格式的大小、路径、文件数及内容保护保持。README 不进入安装目录。Jenkins 完整源码归档会携带受版本控制的 ZIP，无需解压或启动时落库。旧预检遇到包变更会冲突，应重新预检。

## 历史来源

此前电子表格包为 Alpha V91 正式导出基线合入 View Planning V92 候选（工作流 1.4.6、智能分析 2.13.2、合并分析 1.1.3）；SHA256 `936dbee389cce11ec95ad901cb5dc7fd893f22b81892501905d10ae55a9ddfe7`。2026-09-22 的 V91 正式导出 SHA256 `57f24aff7d68a4e4c8218841e38e7c26a933c02547a9a85133e91561f9ce51a5`，来源 Agent `812dba72-458b-474c-bb5f-ed684eac0f69`。这些是已替换版本，不代表当前预制内容。

完整包协议和接口见 [Agent 安装包](../../../../docs/guides/agent-packages.md)。本轮不声称更新了任一线上 Agent；用户需在目标环境显式预检、新建或覆盖安装。依赖变更：无；数据库保持 013。
