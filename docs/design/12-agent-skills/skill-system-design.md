# DeepAgent 线上 Skill 体系设计

> 实施版本：v0.3.18；运行目录收口：v0.3.20；资产读取与脚本协议：v0.3.22
> 运行时：Pydantic AI 2.26 + Pydantic AI Harness 0.18
> 数据库：Alembic 003

## 1. 结论

Skill 已与 ToolPackage 分离：

```text
Agent.capabilities → 工具包授权
Agent.skills       → 任务方法、References 和 Assets 授权
```

ToolPackage 决定模型能调用哪些工具；Skill 只提供如何完成任务的说明和素材。Skill 文档提到某个
MCP 工具，不代表 Agent 自动获得该工具。未被 Agent 选择的 Skill 不进入模型目录，也不能读取资源。

旧 `skills/<name>/__init__.py`、Python `Skill` 对象和 `skill:<name>` ToolPackage 已删除；运行时不保留
旧编码兼容分支。Alembic 003 若发现历史 `agents.capabilities` 中存在 `skill:*`，会在 DDL 前明确失败。

## 2. 产品数据模型

上传安装的当前版本保存在 `skill_packages`：

| 字段 | 语义 |
| --- | --- |
| `code` | 稳定编码，主键 |
| `label/description` | 管理员展示信息 |
| `version` | 包声明版本，可为空；不用于历史固定 |
| `schema_version` | DeepAgent Skill 包格式版本，当前 `1.0` |
| `source` | 当前固定为 `upload`；Builtin 不写表 |
| `enabled` | 全局启停；停用不清理 Agent 引用 |
| `platform_file_id` | 平台文件服务中原始 ZIP 的 ID |
| `file_name/file_size/content_hash` | 原包校验和展示信息 |
| `manifest` | Manifest 与 SKILL frontmatter 的解析结果 |
| `resource_index` | 资源路径、类别、MIME、大小、Hash 与可读状态，不含正文 |
| `contains_scripts` | 包含 `scripts/` 或 `tools/` 的安全提示 |

`agents.skills` 是非空 JSON 列表，默认 `[]`。新建和更新规则：

- 新建只能选择存在且启用的 Builtin/上传 Skill。
- 更新未携带 `skills` 时保持原值；显式 `null` 返回 422。
- Skill 停用后已有引用保留，Agent 运行时给出明确不可用诊断；重新启用后自动恢复。
- 编辑旧 Agent 时允许原样保留已停用引用，但不能把已停用 Skill 新增给其他 Agent。
- 删除仍被 Agent 引用的上传 Skill 返回 409 和 Agent 名称列表。

首版只有当前安装版本，不建 Revision 表。替换包时更新 File ID、Hash、Manifest 和资源索引；相同
Hash 返回 `noop`。Agent 不固定历史版本，也没有数据库内回滚。

## 3. 包格式与安全

```text
skill.zip
├── SKILL.md              必需，含 name/description frontmatter
├── manifest.yaml         可选，code/version/schema_version/label
├── references/           可按需读取的文本、DOCX 或 XLSX 资料
├── assets/               文本、图片或模板资产
└── scripts/ 或 tools/    可保存和列出；资源工具不可读，仅安全 Backend 可执行
```

安装器执行：

- ZIP 最大 10 MiB，解压后最大 50 MiB、256 个文件、8 层目录。
- 拒绝绝对路径、`..`、符号链接、加密 ZIP、重复路径、损坏包和声明超限的压缩包。
- `SKILL.md` 必须是 UTF-8，name 使用小写字母/数字/单连字符并兼容 Harness 目录规则。
- 模型可读文本单文件最大 2 MiB；读取时单次最多 32 KiB。
- References 支持 Markdown/文本/JSON/YAML/CSV/HTML/XML，以及可分页解析的 DOCX/XLSX。
- Assets 还允许常见图片和 DOCX/XLSX/PPTX/PDF 模板；DOCX/XLSX 由解析器返回文本结构，其他
  二进制只展示索引，不做 Base64 注入。
- `scripts/`、`tools/` 只记为脚本资源；Console 展示路径和安全 Backend 状态，模型不能读取脚本
  正文。只有满足第 10 节安全门槛时才可执行。

Skill 内容始终是低于系统 Prompt 和 Agent Prompt 的数据，不能覆盖权限或上层约束。真正写入或
产生副作用的参数仍由实际业务工具重新校验。

## 4. 存储与事务补偿

```text
Console 上传 ZIP
→ DeepAgent 在内存/临时目录校验
→ 平台文件服务以 INF 类型保存原 ZIP
→ skill_packages 保存 File ID 与解析索引
```

数据库不保存二进制或解压正文。平台文件服务和 `skill_packages` 是事实源；每个 Pod 只维护一份
可重建的当前运行副本：

```text
runtime-skills/
├── budget-dimension-modeling/
├── excel-to-dynamic-report/
└── quick-create-budget-poc/
```

服务启动时清理数据库已删除/停用的孤儿目录，并按 `platform_file_id` 下载缺失或 Hash 不一致的
当前包。安装和替换直接复用本次已校验的上传字节原子更新 `<code>/`；启用和管理员手工重载会
重新物化，停用和删除立即清理目录。普通 Agent Run 只读取本地目录，不访问平台文件服务、
不重新解压，也不保留按 Hash 区分的历史版本。Hash 仅用于完整性和判断运行副本是否过期。

平台 ZIP 是系统级配置文件，不属于业务 Space。平台上传使用 `INF` 文件类型；Space 级用户上传
类型 `UPL` 不得用于 Skill，否则无 Space 回读会得到不可用 File ID。安装、替换、服务启动恢复、
管理员下载和 Run 兜底下载均保留调用身份但主动去掉 `app/space/language`业务上下文，确保
同一 File ID 始终按系统级语义访问。数据库只保存 File ID、Hash 和包元数据，不保存请求上下文、
Token、Cookie 或完整 Header。

单个 Skill 同步失败不会阻止应用启动，也不会阻塞其他 Skill 或普通回答。运行目录意外丢失时，
本进程只兜底同步一次；失败后保持错误状态，直到重载、启停或服务重启，不在每轮 Run 重复下载。
多 Pod 共享平台原包，但各自维护一份当前运行目录。

事务边界：

- 安装：平台上传成功但数据库插入失败时，尽力删除新文件。
- 更新：新文件上传并完成数据库切换后再尽力删除旧文件；旧文件清理失败只记录告警。
- 删除：先确认无 Agent 引用并删除配置，再尽力删除平台文件；清理失败不恢复已删除配置。

配置备份包含 `skill_packages` 和 `agents.skills`，但只保存 File ID 与元数据，不包含平台 ZIP，因而
不是跨环境自包含的 Skill 迁移包。

## 5. Harness 渐进披露

每次 Run 只装配当前 Agent 选择且启用的 Skill：

```text
Agent.skills
→ 解析 Builtin/当前 Pod 已就绪的上传包
→ Harness Skills 初始目录（name + description）
→ 模型调用 load_capability(id=...)
→ Harness 返回完整 SKILL.md
→ References/Assets 继续按需 List/Read
```

DeepAgent 直接使用 Harness 公开 `Skills` Capability，不自研加载状态机。`load_capability` 使用
Pydantic AI 官方 typed Call/Return，进入原始 History，并与 Tool Search、普通工具一样受 Cancel、
压缩安全边界和历史恢复约束。正文被摘要移除后，模型仍可按 Harness 历史语义重新加载。

DeepAgent 补两个受 Agent 授权约束的只读工具：

- `list_skill_resources(skill_code)`：列出 Reference、Asset 与 Script 的路径、MIME、大小、Hash、
  读取格式和当前可执行状态；不会返回任何正文。
- `read_skill_resource(skill_code, path, offset, limit, sheet)`：文本按 0-based 字符分页；DOCX 与
  XLSX 复用会话附件解析内核，按 1-based 逻辑块或物理行分页。XLSX未指定 `sheet` 时选择第一个
  可见工作表。

没有可用资源时不披露这两个工具；未选择、停用和未知 Skill 均不可访问。PDF、PPTX、图片等
当前不解析的二进制返回 `binary_resource_not_readable`；Script可以列出但资源读取工具始终拒绝。
路径错误使用 `ModelRetry`，索引内文件缺失、损坏或解析失败使用 `ToolFailed`。运行时根据路径、
MIME和后缀派生读取模式，v0.3.20已安装的 XLSX无需重装。

上传 Skill 文件暂时不可用时，本轮把它从 Harness Library 中移除，并给模型一条短说明，禁止
假装已经加载。`final.context.skills` 返回 configured、available、loaded 和 unavailable；这是
当前 Run 的只读诊断，不改变数据库中的 Agent Skill 配置。

## 6. Builtin Skill

Builtin Skill 位于 `deep_agent/builtin_skills/`，随代码发布、不写数据库、在 `/skills` 与上传包合并
展示且只读：

- `deepagent-diagnostics`：排查会话、模型、工具、压缩和取消；不读取服务器日志、不执行 Shell。
- `deepfos-readonly-analysis`：指导 DeepFOS 只读问数、成员确认和结果核验；仍需 Agent 独立启用
  对应 MCP ToolPackage。

两个包都包含 SKILL.md、References 与 JSON Asset，用于回归名称目录、正文加载、资源分页、MCP
授权隔离、Cancel 和压缩。

## 7. API 与 Console

API：

```text
GET    /api/v1/skills
GET    /api/v1/skills/{code}
POST   /api/v1/skills                         multipart ZIP
PUT    /api/v1/skills/{code}/package          multipart ZIP
PATCH  /api/v1/skills/{code}
DELETE /api/v1/skills/{code}
POST   /api/v1/skills/{code}/reload
GET    /api/v1/skills/{code}/resources
GET    /api/v1/skills/{code}/resources/{path}
GET    /api/v1/skills/{code}/package
```

`GET /skills` 和详情额外返回当前 Pod 派生的 `runtime_status/runtime_error_code/runtime_synced_at`，
不写入数据库。重载只重新下载和替换本地目录，不修改 Skill 配置或创建版本。

Console 配置中心提供独立 Skill 页面：列表、详情、资源树、Manifest、安装、替换、启停、重载、下载和删除；
Builtin 标识为“代码内置、只读”。Agent 编辑弹窗将 Skill 与工具包分区，保存独立编码列表。
聊天中 `load_capability` 显示为默认折叠的“正在加载 Skill”，资源与脚本工具按普通 Tool Call/Result
展示。Skill页区分文本、Word、Excel可读和当前不可读资源；脚本只列路径和数量，不展示运行时
可执行性判断。

DeepCube、DeepTable 不需要新增 SSE 或 History 协议适配。

## 8. Library 包与真实业务 Skill

安装接口始终坚持“一个 ZIP 对应一个 Skill”。如果 ZIP 根下同时包含多个 `SKILL.md`，返回 422 并
提示拆分；不把 Harness 的 Library 目录概念变成数据库中的批量安装单位。

v0.3.20 使用 `DeepFOS-Skill培训包-20260811.zip` 做真实格式验收，拆为三个不提交 Git 的包：

- `budget-dimension-modeling`（预算维度建模）。
- `excel-to-dynamic-report`（动态表生成），保留 XLSX Asset 和 Python Script；v0.3.22 可读取 XLSX，
  脚本仅完成受控执行协议，生产仍等待安全 Backend。
- `quick-create-budget-poc`（预算概念验证快速创建），保留 Markdown Reference 和 HTML Asset，
  跨 Skill 依赖改为显式 `load_capability(id=...)`。

运行时这些 Skill 位于同一上传 Library 根目录下并列存放；Agent 仍按编码逐个授权，资源工具不得
通过相对路径跨到相邻 Skill。

## 9. 迁移、测试和发布边界

Alembic 003：

- upgrade 在 DDL 前拒绝旧 `skill:*` 引用。
- downgrade 仅在 `skill_packages` 为空且所有 `agents.skills=[]` 时允许。
- SQLite 做真实 `002 → 003 → 002 → 003`；MySQL/PostgreSQL 做方言和 DDL 路径测试。

v0.3.22 继续使用 Alembic 003，不新增 revision。发布后在 Test 003执行：

1. 备份当前 Test 003配置数据库。
2. 安装三个真实业务 Skill，验证平台原包、数据库元数据和当前 Pod运行状态均正常。
3. 新建临时验收 Agent，确认名称目录、`load_capability`、文本资源 List/Read 和跨 Skill加载。
4. 确认 XLSX可按工作表和物理行读取；非生产 Backend 下模型仍看不到脚本执行工具。
5. 确认首轮同步完成后，后续 Run不再下载平台文件。

## 10. v0.3.22 资产读取与脚本协议

Skill脚本不是 Harness `Skills` 的内置能力。DeepAgent只在 `builtin:sandbox` 中增加条件式
`execute_skill_script`，并复用现有 `PythonExecutionBackend`：

```text
Agent选择目标Skill + builtin:sandbox
→ 当前Skill包含已索引UTF-8 .py
→ 启动时Backend health.production_ready=true
→ 模型才看到 execute_skill_script
→ 同Skill脚本 + inputs.json + 显式arguments + 最多16个授权资源
→ 隔离工作目录执行
→ 只读取 outputs/result.json 作为正式 result
→ 临时目录随调用销毁
```

请求不接受任意宿主路径、跨Skill资源、Shell脚本、依赖安装、网络配置或持久化目录。Script最大
64 KiB，JSON inputs最大2 MiB，资源合计最大2 MiB。没有结果文件时仍返回受限stdout/stderr和
退出状态；其他输出文件不保留或返回。`execute_python(code, inputs)`的模型参数和返回结构不变。

`subprocess-dev`实现同一内部 `expression | script` 协议，供单元测试和本地真实业务包验证，但其
health始终为 `security_level=none, production_ready=false`，所以生产运行时不会披露脚本工具，
也不存在跳过安全检查的开关。Skill管理API和Console完全不计算或展示脚本可执行性；运行时只在
实际组装Agent工具时执行安全门槛。脚本源码始终不向模型展示。

三个培训Skill中仅 `excel-to-dynamic-report`含 `validate_dynamic_blueprint.py`。该脚本只import
`argparse/json/re/sys/zipfile/pathlib/typing/xml.etree`等Python标准库，不依赖当前Sandbox未安装的
`openpyxl`或 `PyYAML`。它已使用真实 `.venv-sandbox`解释器、真实XLSX Asset和固定结果路径跑通；
不完整蓝图返回非零退出码时，`outputs/result.json`中的结构化校验问题仍会保留。这证明执行协议和
现有依赖兼容，不改变 `subprocess-dev`非生产安全的结论。

当前Sandbox Profile只承诺 `numpy/pandas/pydantic`。后续Skill脚本若依赖其他三方包，必须显式
更新Profile、独立venv、安全Backend镜像和测试；不允许运行时 `pip install`或依赖宿主主进程环境。

v0.3.23 只有在目标K8s节点使用最终securityContext/cgroup/seccomp和固定自编go-judge制品运行
完整攻击探针，且报告 `selection_eligible=true` 后才开始。届时安全Backend不可用必须返回
`BACKEND_UNAVAILABLE`，绝不回退 `subprocess-dev`。

## 11. 后续边界

- 不可变 Skill Revision、回滚和 Agent 固定版本。
- 第三方签名、来源信任、Marketplace 和自动升级。
- 可迁移的自包含 Skill 导出包。
- go-judge安全Backend和K8s节点签字（见v0.3.23门槛）。
- Skill脚本依赖扩展、网络权限和输出文件Artifact。
- Skill 内逐文件授权。

这些能力只有在出现真实产品需求和隔离执行环境后再打开，不在当前结构中预埋半套实现。
