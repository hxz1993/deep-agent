# DeepAgent 线上 Skill 体系设计

## 1. 当前实现

DeepAgent 当前会扫描仓库 `skills/<name>/`，导入模块级 `SKILL = Skill(...)`，再转换成：

```text
skill:<name> ToolPackage
  → 一份 FunctionToolset
  → 可选 Instructions
  → 工具目录元数据
```

目前只有 `calculator` 最小示例。它能证明 Skill 可以按 Agent 整包启用，并把 Instructions 与
Python 工具一起交给 Pydantic AI，但还不是完整的线上 Skill 系统：没有安装、版本、来源、更新、
References、Assets、渐进披露和第三方包安全边界。

## 2. 概念边界

| 概念 | 作用 |
| --- | --- |
| ToolPackage | DeepAgent 的配置和授权单位，决定 Agent 允许使用哪组能力 |
| Toolset | Pydantic AI 的运行对象，包含模型可见工具和可选 Instructions |
| Skill | 可复用的任务方法与素材包，可贡献 Instructions、References、Assets 和可选 Toolset |
| Tool Search | 大工具集的运行时发现机制，不等于 Skill 安装或授权 |

Skill 可以最终映射成一个 `skill:<name>` ToolPackage，但不能因此把 Skill 简化成普通函数集合。
没有执行工具的纯知识/流程 Skill 也应当成立；有工具的 Skill 仍必须服从 Agent 授权和运行隔离。

## 3. 建议的 Skill 内容

为了兼容常见的目录式第三方 Skill，第一版设计应允许一个 Skill 包含：

```text
skill-package/
├── SKILL.md              # 名称、摘要、适用条件和主要 Instructions
├── manifest.yaml         # 稳定编码、版本、来源、兼容性和权限声明
├── references/           # 按需读取的长参考资料
├── assets/               # 模板、示例或静态资源
└── tools/ 或 scripts/    # 可选执行能力；不能默认信任和直接运行
```

`SKILL.md` 可以作为跨产品可迁移的内容入口，但线上 DeepAgent仍需要自己的 manifest 和数据库
登记，以表达租户、版本、来源、签名/摘要、启用状态、权限、资源限额和安装状态。YAML 是包格式，
数据库是线上管理和查询事实源，两者职责不同。

## 4. Harness 0.18 与渐进披露

Harness 0.18 提供 `Skills` Capability：扫描每个一级目录的 `SKILL.md`，首次只让模型看到 Skill
名称和描述；模型选择后再加载 Markdown 正文。这正好可以作为 DeepAgent Skill Instructions 的
基础实现，避免自行维护第二套“发现 Skill → 注入正文”的状态机。

它的边界也很明确：

- 不读取 `references/` 或 `assets/`。
- 不执行 `scripts/`，也没有 `read_skill_resource` 或 `run_skill_script`。
- 不负责安装、版本、租户权限、来源信任、签名和更新回滚。
- Harness `Capability` 是运行时延迟内容；DeepAgent `ToolPackage` 仍是产品配置与授权单位。

因此推荐的关系是：

```text
Agent.capabilities 选择 skill:<name>
  → DeepAgent 校验安装状态、版本和权限
  → Harness Skills 延迟披露 SKILL.md 正文
  → DeepAgent ToolPackage 提供该 Skill 已授权的 Toolset
  → DeepAgent 资源工具按需读取 references/assets
  → scripts 只能进入 Sandbox/受控 Backend
```

当前 `SKILL = Skill(...)` 把 Instructions 和 FunctionToolset 绑在 Python 模块里，适合作为最小示例，
但不能作为第三方线上 Skill 格式。后续重构应让目录式内容成为事实源，Python 工具成为可选、显式
授权的执行扩展；迁移期可以保留现有 Python Skill 适配器，不再增加新的这种格式。

### 4.1 渐进披露层级

Skill 不应在每轮请求中完整塞入所有长文档和资产：

1. Harness 初始只向模型提供已授权 Skill 的名称和简短说明。
2. 模型选择后由 Harness 加载完整 `SKILL.md` 正文。
3. References 和 Assets 由 DeepAgent 的只读资源工具按引用加载，避免重复占用上下文。
4. Skill 自带的大型工具集复用 Tool Search；少量工具可直接披露。

检索只在 Agent 已授权的 `skill:*` 包内工作。渐进披露解决上下文成本，不替代管理员授权、用户
权限或脚本执行隔离。

## 5. 线上安装与安全

第三方 Skill 安装至少需要：

- 稳定包编码、语义版本、来源 URL/仓库和内容摘要。
- 安装、更新、停用、卸载以及版本回滚状态。
- manifest、文件路径、压缩包体积和内容类型校验。
- Instructions、References 与 Assets 的大小和敏感信息限制。
- Python、Shell、网络和文件能力的显式权限声明与人工/策略审核。
- 第三方脚本进入 Sandbox 或受控 Backend，禁止以导入 Skill 为由在主服务进程任意执行。
- 更新后不静默扩大权限；Agent 对 Skill 的选择和版本变化可追溯。

第一版不承诺任何互联网上的 Skill 都能无审查安装。优先兼容内容结构，再逐步开放受控执行能力。

### 5.1 Agent 如何选择 Skill

Skill 先安装到当前 DeepAgent 管理范围，再由每个 Agent 像选择其他 ToolPackage 一样显式选择
`skill:<code>`。未被 Agent 选择的 Skill 不进入该 Agent 的名称目录，也不能被渐进加载；被选择只代表
授权可用，模型仍根据本轮任务决定是否加载其正文。第一版不要求管理员逐个选择 Skill 内的文件、
Reference 或工具，也不默认把所有已安装 Skill 开放给所有 Agent。

Agent 配置只引用稳定的 `skill:<code>`，默认解析该 Skill 当前启用的内容。是否允许 Agent 固定到某个
历史版本属于后续可复现性增强，不应在第一版安装协议尚未跑通时提前增加配置复杂度。

### 5.2 版本与数据库结构不是同一个决定

线上 Skill 需要记录当前内容的版本、包格式版本和内容 Hash，但这并不等于第一版必须建立独立的
`skill_package_revisions` 表。数据库首版可以只维护当前已安装内容；只有产品明确需要历史回滚、
灰度发布、旧 Run 精确复现或 Agent 固定版本时，才增加不可变 Revision 记录。

因此实施前需在迁移设计中二选一并以真实需求验收：

1. **当前版本模型**：一张 Skill 包表保存当前 `version/schema_version/content_hash/storage_uri`，更新时
   替换当前内容；实现最小，但不提供数据库内回滚。
2. **不可变 Revision 模型**：稳定 Skill 身份指向一个活动 Revision，每次发布形成不可变快照；支持
   回滚和精确复现，但多一套状态、清理和升级语义。

在回滚和版本固定需求未确认前，本文不锁定第二张 Revision 表，也不为“其他平台有版本”直接推导
DeepAgent 必须采用相同物理表结构。

## 6. 建议实施阶段

### A. 协议与只读目录

- 定义 manifest、`SKILL.md`、References 和 Assets 约束。
- 将现有 Python Skill 适配到统一目录模型。
- Console 展示来源、版本、素材和是否包含执行工具。

### B. 本地/管理员安装

- 管理员上传或指定受信来源。
- 完成校验、版本登记、启停和更新。
- Agent 继续整包选择 `skill:<name>`。

### C. 渐进披露

- 使用 Harness `Skills` 初始只披露名称和摘要，选择后加载 Instructions。
- DeepAgent 增加受权限约束的 References/Assets 读取能力，不把长素材一次性注入。
- 与 MCP Tool Search 共用观测口径，但不混用授权逻辑。

### D. 第三方生态

- 来源信任、签名、审核和兼容性报告。
- 受控脚本执行和依赖隔离。
- 安装失败、更新回滚和运维审计。

## 7. 本轮不做

- 不新增 Skill 数据库表或安装 API。
- 不把当前 Python 包扫描伪装成完整第三方 Skill 支持。
- 不允许 Skill 绕过 ToolPackage、用户权限或 Sandbox。
- 不要求 Agent 管理员逐个选择 Skill 内的文件或工具。

实施入口统一登记在 [`../TODO.md`](../TODO.md) 的 `SKILL-001`。建议下一轮先完成 A+C 的只读
目录和 Instructions 渐进披露，再设计数据库安装、脚本执行和第三方生态；不要一次性把所有安全
问题绑成一个版本。

框架采用边界以
[`../14-framework-capabilities/framework-capabilities-baseline.md`](../14-framework-capabilities/framework-capabilities-baseline.md)
为准：Harness `Skills` 是下一步应直接复用的渐进披露内核；`CapabilityCreation`、主服务
`FileSystem`/`Shell` 和默认 `CodeMode` 已明确不作为线上 Skill 安装或脚本执行方案。
