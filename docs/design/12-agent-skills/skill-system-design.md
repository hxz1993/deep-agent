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

## 4. 渐进披露

Skill 不应在每轮请求中完整塞入所有长文档和资产：

1. 初始只向模型提供已授权 Skill 的名称、简短说明和适用条件。
2. 模型或确定性路由判断相关后，再读取完整 `SKILL.md`。
3. References 和 Assets 按引用加载，避免重复占用上下文。
4. Skill 自带的大型工具集继续使用工具检索；少量工具可直接披露。

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

- 初始只披露摘要。
- 按需加载 Instructions、References 和 Assets。
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

实施入口统一登记在 [`../TODO.md`](../TODO.md) 的 `SKILL-001`。
