# DeepAgent Skill 安装与运行指南

DeepAgent v0.3.18 将 Skill 与 ToolPackage 分离，v0.3.20 收口上传 Skill 的运行文件，v0.3.22
增加DOCX/XLSX读取与安全脚本协议。Agent 的 `capabilities` 继续控制可执行工具，
`skills` 只控制任务说明、References 和 Assets；选择 Skill 不会自动授予 MCP 或其他工具权限。

## 管理入口

Console 配置中心的“Skill”页面提供：

- 查看代码内置与上传 Skill。
- 上传 ZIP 安装、替换当前包、启用/禁用、重新加载、下载原包和删除。
- 查看 Manifest、Reference/Asset 索引和脚本安全提示。
- 在 Agent 编辑弹窗的独立 Skill 区域选择允许使用的 Skill。

Builtin Skill 随代码发布，只读且不能替换或删除。上传包保存在平台文件服务，数据库
`skill_packages` 只保存平台 File ID、Hash、Manifest 和资源索引。

上传包在每个 Pod 只生成 `runtime-skills/<code>/` 一份当前运行副本。服务启动、安装、替换、启用
和手工“重新加载”负责同步；普通 Run 只读本地目录，不访问平台文件服务。运行副本丢失或同步
失败时，该 Skill 暂时跳过，其他 Skill、工具和普通回答继续工作。

## 包格式

```text
skill.zip
├── SKILL.md              必需，Frontmatter 含 name 和 description
├── manifest.yaml         可选，声明 code/version/schema_version/label
├── references/           可按需读取的文本、DOCX或XLSX资料
├── assets/               文本、图片或模板资产
└── scripts/ 或 tools/    允许保存和列出，不能当资源正文读取
```

ZIP 最大 10 MiB，解压后最大 50 MiB、最多 256 个文件、目录深度最多 8 层。安装器拒绝路径穿越、
绝对路径、符号链接、加密 ZIP、重复路径和压缩炸弹。模型可分页读取索引内的UTF-8文本、DOCX
逻辑块和XLSX物理行；PDF、PPTX和图片只展示元数据。脚本可以列出路径，但不能通过资源工具读取。

一个 ZIP只能包含一个 Skill。培训 Library或第三方合集包如果包含多个同级 `SKILL.md`，需要先拆成
多个 ZIP分别安装；API会返回 422而不是隐式创建多条配置。

## API

```text
GET    /api/v1/skills
GET    /api/v1/skills/{code}
POST   /api/v1/skills                         multipart: file=<zip>
PUT    /api/v1/skills/{code}/package          multipart: file=<zip>
PATCH  /api/v1/skills/{code}
DELETE /api/v1/skills/{code}
POST   /api/v1/skills/{code}/reload
GET    /api/v1/skills/{code}/resources
GET    /api/v1/skills/{code}/resources/{path}
GET    /api/v1/skills/{code}/package
```

Agent 创建/更新接口增加独立字段：

```json
{
  "skills": ["deepagent-diagnostics"]
}
```

新建 Agent 默认 `[]`；更新不传时保持原值，显式 `null` 返回 422。停用 Skill 的既有引用保留，
但运行时不加载；重新启用后自动恢复。仍被 Agent 引用的上传 Skill不能删除。

## 模型运行

```text
Agent.skills
→ 初始只披露 Skill 名称和简介
→ 模型按需调用 load_capability
→ Harness 返回完整 SKILL.md
→ list_skill_resources/read_skill_resource 按需读取素材
→ 安全 Backend 可用且 Agent 同时启用 builtin:sandbox 时，才可 execute_skill_script
```

`load_capability` 使用 Pydantic AI 官方 Tool Call/Return，正常进入 History，并参与取消和上下文
压缩的工具配对。Skill正文、资源和脚本都不能覆盖系统/Agent Prompt或业务权限。

Run终态的 `context.skills` 会列出 configured、available、loaded和 unavailable，便于确认配置的
Skill是否已就绪及本轮实际加载了哪些正文。该状态不写数据库，客户端不适配也不影响聊天。

## 数据库升级和备份

Alembic 003 创建 `skill_packages`，并给 `agents` 增加非空 `skills` JSON。升级前必须确认
`agents.capabilities` 不存在旧 `skill:*` 引用；迁移遇到此类数据会在 DDL 前失败。降级只允许
`skill_packages` 为空且所有 Agent 的 `skills=[]`。

配置备份包含 Skill 元数据和 Agent 引用，但不复制平台文件服务中的 ZIP，所以不是可跨环境独立
恢复的 Skill 安装包。发布到新环境前必须确认平台 File ID 可用，或重新上传原 ZIP。

脚本执行不发生在DeepAgent主进程。Skill管理接口和Console只展示脚本路径、数量等包内容，不判断、
返回或展示脚本当前是否可执行。真正执行时，Agent运行时才检查目标Skill、`builtin:sandbox`和Backend
生产安全状态；条件不满足时不组装 `execute_skill_script`。未来安全Backend只接收当前Agent已选择
Skill的 `.py`、显式JSON inputs、参数和同Skill资源，并只从 `outputs/result.json`读取正式结构化
结果。脚本源码始终不通过资源工具披露给模型。

### 培训包脚本依赖核验

当前三个培训Skill中只有 `excel-to-dynamic-report`包含脚本：

| Skill | 脚本 | Python依赖 |
| --- | --- | --- |
| `budget-dimension-modeling` | 无 | 无 |
| `quick-create-budget-poc` | 无 | 无 |
| `excel-to-dynamic-report` | `scripts/validate_dynamic_blueprint.py` | 仅Python标准库：`argparse`、`json`、`re`、`sys`、`zipfile`、`pathlib`、`typing`、`xml.etree` |

当前Sandbox Profile预装的第三方包是 `numpy`、`pandas`、`pydantic`；不包含 `openpyxl`或
`PyYAML`。上述真实校验脚本不依赖这些未安装包，而是使用标准库 `zipfile`与XML解析XLSX，因此已
使用 `.venv-sandbox`中的真实解释器、真实 `动态表蓝图模板.xlsx`和原脚本跑通：脚本能生成
`outputs/result.json`，模板不完整时也能保留结构化校验问题。

这个结果只证明“脚本依赖和执行协议兼容”，不代表 `subprocess-dev`具备生产安全隔离。以后安装的
Skill若引入Profile之外的第三方包，执行会明确失败；不得在运行时 `pip install`。需要新增包时，
必须同步更新Sandbox Profile、独立venv、安全Backend镜像和回归测试。

## v0.3.22 发布后复测

1. 备份 Test revision 003配置。
2. 安装拆分后的三个培训 Skill，确认运行状态为“运行文件已就绪”。
3. 用专用临时 Agent验证 `load_capability`、Markdown/HTML/DOCX/XLSX资源读取和跨 Skill加载。
4. 确认 XLSX Asset可按工作表读取；Skill详情只列脚本路径和数量，不展示可执行性判断；当前非生产
   Backend下模型看不到 `execute_skill_script`。
5. 确认普通后续 Run不重复访问平台文件服务；重载失败只影响对应 Skill。

完整设计和安全边界见
[`../design/12-agent-skills/skill-system-design.md`](../design/12-agent-skills/skill-system-design.md)。
