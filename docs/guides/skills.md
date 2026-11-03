# DeepAgent Skill 安装与运行指南

DeepAgent v0.3.18 将 Skill 与 ToolPackage 分离，v0.3.20 收口上传 Skill 的运行文件，v0.3.23
将Skill文件与会话附件统一到 `read_file`。Agent 的 `capabilities` 继续控制可执行工具，
`skills` 只控制任务说明、References 和 Assets；选择 Skill 不会自动授予 MCP 或其他工具权限。

## 管理入口

Console 配置中心的“Skill”页面提供：

- 使用紧凑表格查看代码内置与上传 Skill，通过 Agent 下拉和关键词筛选；共享 Skill 在全部列表只显示一条。
- Agent 筛选包含全部、未被引用及有现存 Skill 的 Agent；引用列显示一个 Agent，其余通过 +N 浮层展开。关联配置仍在 Agent 页维护。
- 状态列表示当前实例的运行文件是否就绪，不代表业务配置或查询验证结果。引用查询失败明确提示，不把未知关系当成未引用。
- 点击列表进入文件详情，返回保留筛选和列表位置；首次进入不自动读取详情。
- 上传 ZIP 安装、替换当前包、重新加载、导出和删除。
- 单个上传Skill可直接导出原始ZIP；“导出全部”生成包含清单和全部上传Skill独立ZIP的集合包。
- 查看 Manifest、Reference/Asset 和脚本索引。
- 在 Agent 独立编辑页的 Skill 区域选择允许使用的 Skill。

Builtin Skill 随代码发布，只读且不能替换或删除。上传包保存在平台文件服务，数据库
`skill_packages` 只保存平台 File ID、Hash、Manifest 和资源索引。

内置“文件读取与核验”（`file-reading-verification`）提供文本、Word、Excel、PDF和PPTX的
工具选择、续读与关键字段核验方法，附一份按需读取的表格核验参考。管理员需要在Agent编辑页
显式关联后才能使用；不会给现有Agent自动启用，也不会自动授予文档解析或Python沙箱能力。
Skill按本次Run实际工具描述判断格式支持，不假定有图片查看或附件脚本访问能力。它不含可执行
脚本，不修改文件、业务数据或解析供应商配置。

上传包在每个 Pod 只生成 `runtime-skills/<code>/` 一份当前运行副本。服务启动、安装、替换
和手工“重新加载”负责同步；普通 Run 只读本地目录，不访问平台文件服务。运行副本丢失或同步
失败时，该 Skill 暂时跳过，其他 Skill、工具和普通回答继续工作。

替换当前包时，即使 ZIP内容 Hash没有变化，后端也会先检查平台原包：原包正常返回 `noop`；
原 File ID已经失效则重新上传、更新数据库 File ID、恢复当前运行目录并返回 `repaired`。因此
管理员可以直接重新提交手头相同 ZIP恢复原包，不需要修改 Skill版本或伪造内容变化。

Skill覆盖以稳定`code`为准，不以显示名称为准：

- “上传安装”不会自动覆盖。同`code`已存在时返回409。
- “替换ZIP”是显式覆盖入口；新包`code`必须与当前路径中的`code`完全一致，否则返回422。
- 内容Hash相同时返回`noop`；若平台原包已丢失，则同Hash替换会修复原包并返回`repaired`。
- 显示名称相同但`code`不同，是两个独立Skill。

单个“导出”返回平台保存的原始ZIP，可在另一环境直接使用“上传安装”。“导出全部”返回：

```text
deepagent-skills-export-*.zip
├── manifest.json
└── skills/
    ├── first-skill.zip
    └── second-skill.zip
```

每个子ZIP都是可独立安装的原包。集合包不包含代码内置Skill、Agent绑定关系、平台File ID或会话；
也不作为一个多Skill包直接安装，目标环境仍逐个上传其中的Skill ZIP，遇到同`code`时由管理员明确
选择是否替换。

## 包格式

```text
skill.zip
├── SKILL.md              必需，Frontmatter 含 name 和 description
├── manifest.yaml         可选，声明 code/version/schema_version/label
├── .env                  可选，随包配置文件，由脚本自行读取
├── references/           可按需读取的文本、DOCX、XLSX、PPTX或PDF资料
├── assets/               文本、图片或模板资产
└── scripts/ 或 tools/    允许保存和列出，不能当资源正文读取
```

支持根目录 `.env`，以及 `assets/` 下的 `.env` 或 `*.env` 文本配置。文件出现在 Skill 资源列表中，
管理员可以在详情页按原始文本预览。执行脚本时将路径显式放入 `resource_paths`，文件按原路径复制到
本次临时执行目录的 `skill/` 下，脚本自行解析；平台不自动加载 `.env`、不注入变量，也不继承宿主平台凭据。
例如入口为 `scripts/report.py`，传入 `resource_paths=[".env"]` 后，脚本可通过
`Path(__file__).resolve().parents[1] / ".env"` 定位 Skill 根配置。脚本目录更深时按实际层级定位。
此配置属于原始 ZIP 的内容，单包下载、导出和迁移会原样携带；不要将含真实凭据的原包公开分发。
其他资产后缀、文本大小、路径与脚本执行规则保持不变。

ZIP 最大 10 MiB，解压后最大 50 MiB、最多 256 个文件、目录深度最多 8 层。安装器拒绝路径穿越、
绝对路径、符号链接、加密 ZIP、重复路径和压缩炸弹。模型可分页读取索引内的文本、DOCX逻辑块
、PPTX逻辑块、XLSX物理行及PDF物理页文本层。`read_file` 不解析图片OCR或旧DOC/XLS/PPT文件。Agent 显式关联“文档解析”后，
`parse_document` 可读取已授权且当前供应商支持的这些资源，保留原生正文与页码；
供应商和格式边界见[文档解析指南](document-parsing.md)。脚本可以列出路径，但不能
通过文件工具读取源码。

一个 ZIP只能包含一个 Skill。培训 Library或第三方合集包如果包含多个同级 `SKILL.md`，需要先拆成
多个 ZIP分别安装；API会返回 422而不是隐式创建多条配置。

## Console 文件预览

管理员在Skill详情中可点开每个文件：文本沿用原文、Markdown和JSON展示，DOCX／XLSX提取正文或表格；Python等文本脚本使用与对话正文相同的代码高亮、复制、折叠及换行控件。长文本或代码可点击“下一段”，也可返回开头。

PNG、JPEG、GIF、WebP及SVG通过当前管理员鉴权读取，点击图片可放大，点击空白、关闭按钮或按ESC收起。图片只保留当前预览的浏览器临时URL，离开后释放，不生成平台文件。

PDF、PPTX及其他当前没有预览器的文件显示“暂不支持在线预览，请下载 Skill 包后查看”，并提供现有原包下载入口。旧索引中的可读标记不会使Console把PDF／PPTX作为文本读取。这里是管理预览能力，Agent的`read_file`仍按原格式能力工作，不能读取脚本源码；安装白名单和脚本执行规则不变。

## API

```text
GET    /api/v1/skills
GET    /api/v1/skills/references/agents          管理员只读：[{id,name,skills}]
GET    /api/v1/skills/export
GET    /api/v1/skills/{code}
POST   /api/v1/skills                         multipart: file=<zip>
PUT    /api/v1/skills/{code}/package          multipart: file=<zip>
PATCH  /api/v1/skills/{code}
DELETE /api/v1/skills/{code}
POST   /api/v1/skills/{code}/reload
GET    /api/v1/skills/{code}/resources
GET    /api/v1/skills/{code}/resources/{path}
GET    /api/v1/skills/{code}/resources/{path}?raw=true   管理员图片预览，返回索引内图片字节
GET    /api/v1/skills/{code}/package
```

Agent 创建/更新接口增加独立字段：

```json
{
  "skills": ["deepagent-diagnostics"]
}
```

新建 Agent 默认 `[]`；更新不传时保持原值，显式 `null` 返回 422。Skill自身没有启停状态；是否
参与运行只由Agent的`skills`关联决定。仍被 Agent 引用的上传 Skill不能删除。

## 模型运行

有可用关联Skill时，运行指令要求模型先对照目录描述：明确匹配的Skill须先加载，等待正文返回后
再执行相关操作；多个匹配项可组合，无匹配项不加载，当前上下文已激活的Skill不重复加载。
子文档仅在任务或已加载Skill的适用步骤需要时读取，不批量展开所有文件。
这是选择顺序的提示约束，不改变Harness延迟加载、授权或失败处理，也不保证模型必然遵守。

```text
Agent.skills
→ 初始只披露 Skill 名称和简介
→ 模型按需调用 load_capability
→ Harness 返回完整 SKILL.md
→ list_skill_resources 获得精确路径
→ read_file(resource={type: skill, skill_code, path}) 按需读取素材
→ Agent同时关联 builtin:sandbox 时，可 execute_skill_script
```

`load_capability` 使用 Pydantic AI 官方 Tool Call/Return，正常进入 History，并参与取消和上下文
压缩的工具配对。Skill正文、资源和脚本都不能覆盖系统/Agent Prompt或业务权限。

Run终态的 `context.skills` 会列出 configured、available、loaded和 unavailable，便于确认配置的
Skill是否已就绪及本轮实际加载了哪些正文。该状态不写数据库，客户端不适配也不影响聊天。

## 数据库升级和备份

Alembic 003 创建 `skill_packages`，并给 `agents` 增加非空 `skills` JSON；004只清理显式
Agent能力中的旧 `builtin:attachment`编码。升级003前必须确认
`agents.capabilities` 不存在旧 `skill:*` 引用；迁移遇到此类数据会在 DDL 前失败。降级只允许
`skill_packages` 为空且所有 Agent 的 `skills=[]`。

Alembic 010 删除`skill_packages.enabled`。旧值不再影响运行时：升级后所有已安装Skill都作为目录
资源存在，只有Agent显式关联的Skill才会加载。

配置备份包含 Skill 元数据和 Agent 引用，但不复制平台文件服务中的 ZIP，所以不是可跨环境独立
恢复的 Skill 安装包。跨环境迁移上传Skill应使用单个或全部导出；Agent绑定仍由目标环境配置。
若原包已丢失，在 Console替换同一 ZIP会走 `repaired`恢复共享原包和当前 Pod运行目录。
配置备份文件的`_backup_info.scope_manifest`会明确标记Skill原包属于未内嵌的外部文件，并指向
`/api/v1/skills/export`；它只用于核对备份范围，不保存平台凭证或Skill内容。

## Python 与 Skill 脚本执行（0.15.0）

脚本执行不发生在 DeepAgent 主进程。Agent 同时选择含脚本 Skill 和 `builtin:sandbox` 时，
运行时披露 `execute_skill_script`；它与普通 `execute_python` 使用同一无状态后端，每次调用新建
Python 进程和临时目录。PTC 调用这两个工具也走同一实现，不产生另一套脚本协议。

```text
execute_python(code, inputs?, files?)
execute_skill_script(skill_code, script_path, arguments?, inputs?, resource_paths?, files?)
files = [{"file_id": "当前会话授权文件ID", "path": "inputs/source.pdf"}]
```

普通 Python 继续直接执行代码并提供 `inputs` 变量，不改为文件入口，也不获得 Skill 目录访问能力。
Skill 只接受当前 Agent 已选择且已索引的 UTF-8 `.py` 路径；同 Skill 的 Python 模块自动携带，
非代码资源仍按 `resource_paths` 精确选择。组装执行副本时校对索引指纹，更新冲突明确失败。

### 每次 Skill 调用的目录

```text
execution-root/                       # cwd
├── skill/
│   ├── SKILL.md
│   ├── .env                          # resource_paths=[".env"] 时携带
│   ├── scripts/
│   │   ├── __init__.py
│   │   ├── report.py                 # 原入口，不改名
│   │   └── helpers.py
│   └── assets/report-template.html   # 明确选择时携带
├── inputs.json                       # 本次 JSON inputs
├── inputs/                           # files 参数对应的授权文件
├── outputs/
│   ├── result.json                   # Skill 正式 JSON 结果
│   └── report.html                   # 显式登记的产物
└── .deepagent-files.json             # 内部文件映射
```

入口按主脚本执行，保留实际 `__file__`、`sys.argv[0]` 和包上下文，支持同目录导入、包内相对导入和
嵌套包。不要求 `def main()`。例如 `scripts/report.py` 可 `from .helpers import render`，
并用 `Path(__file__).resolve().parents[1] / "assets/report-template.html"` 读取 Skill 资源。
不要假定当前目录就是 Skill 根目录，也不要使用宿主缓存路径。

### 输入与上传产物

Skill 的 `files` 与普通 Python 共用会话文件授权和下载。默认落在 `inputs/`，脚本通过
`deepagent_files.input_path(file_id)` 读取；附件不能覆盖 `skill/` 或控制文件。
JSON 输入继续从根 `inputs.json` 读取，正式业务结果写入 `outputs/result.json`。

```python
import json
from pathlib import Path
from deepagent_files import input_path, publish_path

values = json.loads(Path("inputs.json").read_text(encoding="utf-8"))
source = input_path(values["file_id"])
report = publish_path("report.html", mime="text/html")
Path(report).write_text("<h1>报告</h1>", encoding="utf-8")
Path("outputs/result.json").write_text(json.dumps({"status": "ready"}), encoding="utf-8")
```

`publish_path()` 仅登记文件；脚本成功结束后宿主上传 FileStore。未登记文件、`.env`、代码和输入
不会自动上传。工具返回已有执行信封的 `status`、`result`、`truncated` 等字段，并在顶层 `files`
返回 File ID、名称、MIME、大小和 SHA-256；结果字段不会变成宿主路径或文件正文。
上传失败与取消使用共用补偿；没有成功上传不能报告成功 File ID。

PTC 可以直接调用 `execute_skill_script(...)`，检查 `status` 与截断标记后使用 `result` 和 `files`。
可信产物汇总到外层 `run_code`，后续步骤失败不抹掉已成功生成的文件事实。MCP 返回同名 metadata
不能授予文件权限。PTC 内部返回沿现有规则跳过模型展示用的大结果外置，外层仍受保护。
FileStore 上传与登记下载中心是独立动作，本工具不会自动发布到下载中心。

### 固定边界与旧脚本适配

- 入口与每个 Python 辅助模块不超过64KB，JSON inputs 16MB；显式非代码资源最多16个，模块与选中资源合计20MB。
- 代码模块、非代码资源和平台附件分别计量；平台文件保留当前上传策略与工具自身更严格的限制，正式 JSON 结果仍为512KB，通信帧64MB。
- 代码只使用执行环境预装包，不在运行时安装依赖；超时、CPU、并发和内存按当前后端配置执行，健康接口未启用的硬限制不能宣称生效。
- Python 按部署网络路由访问企业内网，不再做 Worker 级公网 IP 拦截；WebFetch、公开文件下载等其他工具的规则不变。
- 不支持 Shell／Node，不增加任意宿主文件访问、跨 Skill 引用或长期工作区。

**0.15.0 不兼容旧执行布局。** 旧版本把入口复制为临时根 `main.py`；新版使用原路径。
使用 `Path("assets/...")`、根 `.env` 或固定 `main.py` 定位的旧脚本需要适配。根 `inputs.json` 与
`outputs/result.json` 仍保留；既有历史消息、工具结果和文件不重写，也不补造旧结果的 File ID。
Pending 续跑使用新执行器，不恢复旧布局；正式升级前应先适配仍在用的包并处理依赖旧布局的未完成任务。

### 培训包脚本依赖核验

当前三个培训Skill中只有 `excel-to-dynamic-report`包含脚本：

| Skill | 脚本 | Python依赖 |
| --- | --- | --- |
| `budget-dimension-modeling` | 无 | 无 |
| `quick-create-budget-poc` | 无 | 无 |
| `excel-to-dynamic-report` | `scripts/validate_dynamic_blueprint.py` | 仅Python标准库：`argparse`、`json`、`re`、`sys`、`zipfile`、`pathlib`、`typing`、`xml.etree` |

此培训包依赖核验是历史验收记录。当前可用依赖以 `sandbox/profiles/pydata.json` 和执行后端
健康信息为准，不从历史记录推断只有三种第三方包。原脚本曾通过真实独立解释器与模板生成
`outputs/result.json`；0.15.0 的原路径执行和文件发布仍须按本轮回归结果验收。

以后安装的Skill若引入Profile之外的第三方包，执行会明确失败；不得在运行时 `pip install`。
需要新增包时，必须同步更新Sandbox Profile、独立venv和回归测试。

## v0.3.23 Test 验收结果

2026-08-12 已在 Test MySQL revision 004验证：三个培训 Skill原包均可下载、运行状态均为
`ready`；历史失效 File ID可通过同 Hash ZIP替换得到 `repaired`；真实 `deepseek-v4-flash`
完成 `load_capability → list_skill_resources → read_file(XLSX)`，SSE正常结束且History工具调用成对。
当前仅剩一次后续 Pod重启后的自动恢复复核，不需要客户端适配。

完整设计和安全边界见
[`../design/12-agent-skills/skill-system-design.md`](../design/12-agent-skills/skill-system-design.md)。

原图或PDF页的视觉核验可使用当前视觉主模型提供的`view_image`；它与文字读取、专业OCR按任务选择，不进入PTC。参见[图像查看指南](image-viewing.md)。
