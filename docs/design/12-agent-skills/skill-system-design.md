# DeepAgent 线上 Skill 体系设计

> 实施版本：v0.3.18；运行目录收口：v0.3.20
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
├── references/           可按需读取的文本资料
├── assets/               文本、图片或模板资产
└── scripts/ 或 tools/    可保存，当前禁止模型读取和执行
```

安装器执行：

- ZIP 最大 10 MiB，解压后最大 50 MiB、256 个文件、8 层目录。
- 拒绝绝对路径、`..`、符号链接、加密 ZIP、重复路径、损坏包和声明超限的压缩包。
- `SKILL.md` 必须是 UTF-8，name 使用小写字母/数字/单连字符并兼容 Harness 目录规则。
- 模型可读文本单文件最大 2 MiB；读取时单次最多 32 KiB。
- References 支持 Markdown/文本/JSON/YAML/CSV/HTML/XML。
- Assets 还允许常见图片和 DOCX/XLSX/PPTX/PDF 模板；二进制只展示索引，不做 Base64 注入。
- `scripts/`、`tools/` 只记为脚本资源；Console 明确标识“不可执行”，运行时目录和资源工具均隐藏。

Skill 内容始终是低于系统 Prompt 和 Agent Prompt 的数据，不能覆盖权限或上层约束。真正写入或
产生副作用的参数仍由实际业务工具重新校验。

## 4. 存储与事务补偿

```text
Console 上传 ZIP
→ DeepAgent 在内存/临时目录校验
→ 平台文件服务以 UPL 类型保存原 ZIP
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

DeepAgent 只补两个受 Agent 授权约束的只读工具：

- `list_skill_resources(skill_code)`：列出 Reference/Asset 路径、MIME、大小、可读状态和 Hash；仅返回
  脚本数量与不可读/不可执行标识，不暴露脚本路径或内容。
- `read_skill_resource(skill_code, path, offset, limit)`：读取索引内的 UTF-8 文本并分页。

没有可用资源时不披露这两个工具；未选择、停用和未知 Skill 均不可访问。二进制返回
`binary_resource_not_readable`，脚本不出现在列表。

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
聊天中 `load_capability` 显示为默认折叠的“正在加载 Skill”，资源工具按普通只读工具展示。

DeepCube、DeepTable 不需要新增 SSE 或 History 协议适配。

## 8. Library 包与真实业务 Skill

安装接口始终坚持“一个 ZIP 对应一个 Skill”。如果 ZIP 根下同时包含多个 `SKILL.md`，返回 422 并
提示拆分；不把 Harness 的 Library 目录概念变成数据库中的批量安装单位。

v0.3.20 使用 `DeepFOS-Skill培训包-20260811.zip` 做真实格式验收，拆为三个不提交 Git 的包：

- `budget-dimension-modeling`（预算维度建模）。
- `excel-to-dynamic-report`（动态表生成），保留 XLSX Asset 和 Python Script，但当前不可读/执行。
- `quick-create-budget-poc`（预算概念验证快速创建），保留 Markdown Reference 和 HTML Asset，
  跨 Skill 依赖改为显式 `load_capability(id=...)`。

运行时这些 Skill 位于同一上传 Library 根目录下并列存放；Agent 仍按编码逐个授权，资源工具不得
通过相对路径跨到相邻 Skill。

## 9. 迁移、测试和发布边界

Alembic 003：

- upgrade 在 DDL 前拒绝旧 `skill:*` 引用。
- downgrade 仅在 `skill_packages` 为空且所有 `agents.skills=[]` 时允许。
- SQLite 做真实 `002 → 003 → 002 → 003`；MySQL/PostgreSQL 做方言和 DDL 路径测试。

v0.3.20 本地开发继续使用隔离 SQLite，Alembic不新增 revision。发布后再在 Test 003执行：

1. 备份当前 Test 003配置数据库。
2. 安装三个真实业务 Skill，验证平台原包、数据库元数据和当前 Pod运行状态均正常。
3. 新建临时验收 Agent，确认名称目录、`load_capability`、文本资源 List/Read 和跨 Skill加载。
4. 确认 XLSX和脚本只能识别存在，不会读取、执行或伪造结果。
5. 确认首轮同步完成后，后续 Run不再下载平台文件。

## 10. 后续，不属于 v0.3.20

- 不可变 Skill Revision、回滚和 Agent 固定版本。
- 第三方签名、来源信任、Marketplace 和自动升级。
- 可迁移的自包含 Skill 导出包。
- 脚本查看、执行、依赖安装和网络权限。
- Skill 内逐文件授权。

这些能力只有在出现真实产品需求和隔离执行环境后再打开，不在当前结构中预埋半套实现。
