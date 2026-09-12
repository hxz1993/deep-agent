# Agent 安装包本地交付与迁移记录

验收日期：2026-09-13。以下为 **0.9.11 开发阶段**的验收记录；本功能随后按用户要求归入 **0.10.0** 并提交发布，最后一轮仅进行桌面 UI 冒烟。完整笛卡尔积 **25,600/25,600 通过，0 失败、0 跳过**；累计 1,167,360 次断言，耗时 1,326.29 秒，运行前后源码指纹一致。JSON/CSV 数量与完整编号集合已独立核对。逐例结果见 [主报告](README.md)；此页记录环境、迁移和其他回归。

## 查看入口

- [独立安装验收 Console](http://127.0.0.1:8002/console/#/configuration)：包含“矩阵验收 · DeepCube 新建”“矩阵验收 · 电子表格 新建”“矩阵验收 · 导出再上传”“矩阵验收 · 共享工具观察”。可查看安装、新建、覆盖、共享资源及单工具范围。
- “安装包真实模型与Skill验收”：保留已执行的会话和 Python 工具结果，模型已清空。详见 [真实对话证据](live-conversation.md)。
- [日常 Console](http://127.0.0.1:8000/console/#/configuration)：保留原有 6 个 Agent。API 8000、渠道网关 8010、独立验收 API 8002 已重启并核对监听；两个 API `/health` 均返回 `status=ok`。

独立环境使用 `data/agent-package-acceptance/acceptance.db`、`files/`、`runtime-skills/`，配置位于用户本机配置目录的 `agent-package-acceptance.env`。矩阵每例另建独立空库、文件及运行目录，不向日常库写入组合数据。独立验收环境保留用于用户查看。

## 功能入口

| 范围 | 实现位置 |
| --- | --- |
| 包格式、ZIP 安全与自带加载 | `src/deep_agent/agent_packages/archive.py` |
| 预检、新建/覆盖、共享影响与结果 | `src/deep_agent/agent_packages/service.py` |
| Skill 目录回滚与清理 | `src/deep_agent/agent_packages/runtime_transaction.py` |
| 管理员接口 | `src/deep_agent/api/v1/agent_packages.py` |
| 单工具范围、运行装配 | `src/deep_agent/capabilities/ui/selection.py`、`agent/factory.py` |
| 安装/导出 Console | `console/configuration/agent-packages.js`、`agent-package-export.js` |
| 数据库迁移 | `migrations/versions/005_nullable_agent_model.py` |

自带 ZIP 唯一位置为 `src/deep_agent/agent_packages/builtin/`，旧 `deploy/agent-packages/` 副本已移除。两包为暂行版，整包包含提示词、上传 Skill 和 UI 定义；不在启动时安装。迁移前后 ZIP SHA256 一致：

| ZIP | SHA256 |
| --- | --- |
| deepcube-assistant.zip | `93aab6b75c056b1ad09cca6aec6e9a59374b479d2145b43cfc493c6b866d8bf9` |
| deeptable-assistant.zip | `efcabf13a8a7501992a2099af96dc333993dd0397adf9d5534ce0e8f7ed53c3d` |

接口与格式说明见 [安装包指南](../../guides/agent-packages.md)。静态缓存标识为 `20260912-45`。

## 本地清理与 005

按本次确认范围，通过正常接口删除 7 个安装验收 Agent、2 个相关会话，以及确认已无其他引用的 3 个测试 Skill、1 个 UI 工具及其包。保留：DeepCube助手、乳鸽测试、企微助手、日志展示验收、榕树工单助手、电子表格智能助手。未清空数据库，未新增备份，未从线上复制配置。

未发布 `005` 合并以下结构调整：允许 `agents.model_id=NULL`、移除 Agent 温度、增加 `ui_tool_selection`。旧 Agent 的 `{}` 表示原有整包关联。明确的工具名单控制本 Agent 范围：目标环境额外的 C 保留给其他 Agent，本次安装不自动包含 C。

本地库已是 `005`，因此停 API/网关后显式执行该迁移的幂等补齐，仅新增缺少的结构；未重置 Alembic revision。迁移前后逐表核对原有字段数据一致，6 个 Agent 的选择范围均补为 `{}`。复核 `model_id` 可空、`ui_tool_selection` 非空、温度列不存在、revision 仍为 `005`。

隔离 SQLite 验证 `004→005`、重复修复、部分 DDL 后再次修复、数据保留及降级限制。降级遇空模型或非空工具范围明确拒绝；不能丢失范围后扩大权限。MySQL/PostgreSQL DDL 方言编译验证通过；本机没有可用隔离实例，**真实 MySQL/PostgreSQL 数据库测试未执行**。

## 回归证据

| 验证 | 结果 |
| --- | --- |
| 完整 Console 测试 | 547 通过 |
| 完整后端 unit discovery | 执行 1,446：1,438 通过、8 跳过、0 失败，290.021 秒 |
| 8 项跳过原因 | 7 项 Linux 专属软隔离；1 项有限 RLIMIT_AS 在 macOS 不支持。未计为通过 |
| 安装、包格式、实际自带包和 HTTP 专项 | 48 项及 42 个子案例通过，0 跳过；包含失败注入、并发与提交结果不确定 |
| 安装生命周期补充专项 | 8 项通过，覆盖升级往返、历史会话/HITL、真实备份范围 roundtrip、删除/禁用后的预检失效、并发覆盖、资源删除及 Provider 存在但无可用模型 |
| ZIP 精确边界专项 | 3 项通过；包括嵌套文件的累计预算和 CRC 损坏。边界测试注入小预算，另核对正式 50/200 MiB 与 2048 配置值 |
| Skill 发布并发专项 | 29 项通过，含安装与对话、reload、资源读取、启动同步的真实并发，以及取消时等待文件线程完成 |
| 浏览器 | 两种来源、新建/覆盖、ZIP 往返、重名、共享更新、缺失、范围编辑和阻断错误通过；4 主题 × 2 宽度 × 4 步骤的 32 组正文无溢出，错误状态额外 8 组无溢出 |
| 真实模型与本地 Skill | 首轮成功，4.85 秒；`load_capability → execute_python`，结果 194 与独立计算一致 |

复跑命令：

```sh
node --test tests/frontend/console/*.test.mjs
PYTHONPATH=src python3.12 -m unittest discover -s tests/unit -p "test_*.py"
PYTHONPATH=src python3.12 -m unittest tests.unit.test_sandbox_subprocess_guardrails tests.unit.test_sandbox_subprocess_dev -v
```

第三条用于逐项核对平台相关跳过原因。

浏览器截图及步骤见 [浏览器报告](browser/README.md)。真实模型只调用公开 DeepSeek，未连接公司 MCP 或测试环境；临时模型和 Provider 已删除，凭据未写入报告或脚本。

补充专项入口：`tests/unit/test_agent_package_lifecycle_regressions.py`；UI 复制与范围前端定向 7 项通过。

专项审查修复了三个实际边界：提交确认异常必须核对完整配置而非只核对 Agent；成功后的临时目录清理失败不能伪装成安装失败；旧 Skill 请求不得在覆盖后重新发布旧运行目录。当前实例确认资源可加载后才返回安装成功。

自动化浏览器已关闭；逐例临时数据库、文件及运行目录由测试隔离清理。保留日常和独立验收服务，运行日志使用 `TMP to delete` 标记，停服务后可删除。
