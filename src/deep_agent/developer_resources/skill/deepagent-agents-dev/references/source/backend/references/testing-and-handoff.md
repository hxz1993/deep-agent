# 测试与交接

纯文案、颜色、布局改动：必要静态检查 + 一次受影响桌面流程冒烟即可，不默认测试移动端或重跑全量。下列矩阵按受影响协议/行为选取；安装规则、关联、事务或运行装配变更才需要对应后端回归。

## 分层测试

先运行与改动相关且范围最小的一层：

1. 纯逻辑、解析器或校验器测试。
2. 使用临时 SQLite 的 Store/API 测试。
3. SSE、MCP 子进程、请求头、持久化或 Deferred 集成测试。
4. 数据库结构变化时，执行 Alembic 升级/降级和目标数据库方言分支测试。
5. 使用隔离配置或已批准的共享配置，在 8000 端口测试 API/SSE。
6. 内置 Console 的浏览器验证。
7. 由独立前端仓库负责的前端联调。

示例如下；使用前先核实当前模块名：

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.unit.test_context_compaction
PYTHONPATH=src .venv/bin/python -m unittest tests.unit.test_tool_package_registry
PYTHONPATH=src .venv/bin/python -m unittest tests.unit.test_ui_tools_rfc
PYTHONPATH=src:. .venv/bin/python -m unittest discover -s tests -t . -p 'test_*.py'
node --test tests/frontend/console/*.test.mjs
```

## 专项测试矩阵

### 工具包

- 显式的能力/Skill 列表，包括新 Agent 的空列表场景。
- 一个包恰好生成一个 Toolset。
- 运行时工具、Sandbox、附件、Skills、每个选中的 MCP、内置 UI 包和手动 UI 包，都只暴露各自预期的稳定工具名；时间工具默认可用，旧 builtin:general 引用不重复注册。
- 空的或无效的 UI 包在诊断中可见，但不可选择或运行；目录资源没有第二层启用开关。

### 上下文与压缩

- 原始 Message Event 始终不变。
- Tool Call/Result 配对保持完整。
- 截断点和大型测试数据的 Token 计算保持线性，不要在每个边界或每轮重新统计全部历史。
- CPU 准备阶段和 Provider 无响应期间，仍约每 10 秒发出一次 `context_compaction_progress`。
- 单次结构化摘要请求保持在模型窗口内；替换旧锚点，不要递归重复旧锚点。
- 覆盖自动触发、低于触发阈值时手动尝试、noop、超时、条件性 Provider 重试/失败，以及陈旧状态/CAS 冲突。
- 成功及重启后，最新完成的 Compaction Event、模型 Surface、Event API 和 context-status 保持一致。
- Context/Attachment/Tool 证据可以定位原始 Event Part；模型、摘要和证据中均不含元数据。

### UI 工具延迟执行（Deferred）

- 先无效后有效的调用，覆盖 ID 变化、相同和缺失的情况。
- Pending 状态的实时 SSE、持久化 Conversation 历史、刷新、用户结果和模型恢复。
- Deferred 前后混有普通工具时，历史不被破坏。
- Call在原Run、Result在Resume Run，真实持久Part无widget也能配对；事实先后、重复通知、多UI与部分返回、跨轮复用ID不配错。刷新前后计数／顺序／选择不变，人工等待不作工具执行耗时。

### PTC与发现

- 完整规则和既有示例仅保留在run_code说明；搜索短提示、通知新增工具名，已有通知不改写，发现去重不丢失。
- 捕获实际SDK请求验证多批／重复发现、原生调用、合法Monty、失败后原生回退、暂停后新Runner续跑；授权、禁止探测／盲重试、顺序写入、非回滚和载荷保密约束每次请求仍可见。
- 固定发现序列比较说明长度及同一估算器的请求预算，字符或预算减少不宣称为实际计费Token／费用下降。规则是模型指令，工具调用成功不证明模型遵守了所有使用建议。

### MCP

- 检查失败时保留旧配置和快照。
- 请求头和请求上下文转发。
- Run 范围内的重连和错误隔离。
- 重命名以原子方式迁移 Agent 的包引用，改变后续调用的前缀，并保持历史调用不变。
- 内置项的删除/重命名保护，以及旧版 stdio 限制。

### 混合文本与原生输出

- allow_text_output默认false，开启结构化时保持严格JSON路径；mixed正常文字无output_info/成功output事实，结构化字符串/null/false仍带身份。
- 完整响应检查后才发布mixed正文；非法Schema、混调或多个输出的候选文字不泄漏。JSON伴随文字时只接受一次JSON。
- 每Run仅加载一次当前Schema与文本策略，Deferred／跨进程恢复使用当前配置和真实记录的剩余预算；旧快照缺失／损坏／只有引用均不影响真实历史展示，且旧事件不改写。
- 顶层strict boolean、省略/null、草稿/删除定义、policy-only并发、摘要失效、正式ZIP新旧安装和013迁移分别验；配置表bool投影不等于加载完整Schema。
- 用户指定重复次数逐案满足；平台分支验收与业务客户端真实填写、保存、回读分开报告，不拿平台通过代替BOM闭环。

### 结构化上下文引用

- 文本+refs、附件+refs、仅 refs、多个 refs，以及分支行为。
- 在写入前拒绝重复 key、额外封装字段、深度或大小超限，以及非 JSON 数据。
- 历史经过重启和往返读写后仍保留 payload/metadata；模型投影排除 metadata。
- Deferred+refs 返回 422。

## 测试有效性

- 断言用户可见行为，不要只断言辅助函数内部实现。
- 排查 SSE/历史泄漏时，不要 mock 事件映射。
- 断言实时 Delta 和 `final` 不会重复内容，且刷新后的 `/history` 包含持久化的最终状态。
- 对会话操作，同时断言所有者成功、非所有者被拒绝。
- 使用 echo MCP 证明请求头已转发。
- 包集成时，检查实际解释器和模块路径。
- 除非能证明是测试前已存在的问题，否则将泄漏的任务、会话、socket、浏览器或数据库连接视为失败。
- 不要对共享 Test 环境执行迁移和破坏性数据测试。
- 完整回归在源码和测试定稿后启动；运行中修文件或改断言后，该轮不能宣称验证最终版本。比较起止源码哈希，区分唯一测试方法与重复发现次数；失败轮、skip和未执行原因分别报告，不用增加skip消除失败。
- 私有脚本调用unittest discover时，确保仓库根和src都在导入路径（如在仓库根设PYTHONPATH=src:.）；直接脚本目录不等于仓库根。涉及spawn的统计入口使用if __name__ == '__main__'保护，不能把测试入口重执行误诊为产品子进程缺陷。
- 真实MySQL／PostgreSQL使用明确隔离库与容器，SQLite或方言编译不替代实库；默认全量的数据库skip单列，补跑成功只归入对应专项，不重复累计。
- 模型并发传输验证包括主请求与标题同时返回、失败前后消息持久化、断线／取消及端到端History；传输解码错误与Schema拒绝分开归因，不自动重放整个Run或业务工具。

## 跨仓库交接

对于 DeepCube/Python/DeepTable 前端，向其负责人提供：

- 后端 URL，以及是否需要保持运行；
- 精确的请求体和不含秘密的请求头；
- 预期的 SSE Delta/final 与刷新后 Conversation 的序列；
- 仅在仍有需要时提供活动测试会话 ID；
- 后端提交/版本和数据库修订号；
- 已知的回退和错误行为。

发布交接先说明旧历史读取和旧Pending升级续跑是否已验证，再明确新版Pending降旧Runner的反向限制。数据库Revision、运行代码版本及Agent ZIP schema_version是三件事，数据库相同不保证旧Runner能续跑。只读恢复不触发执行；待交互任务的取消走工具协议，不把“处理Pending”解释为删除对话。

用户要求累计审计或发版本时，以指定Tag到当前工作区（含相关未提交文件）为范围，核对依赖／迁移差异，明确沿用证据和本轮实测。按授权提交中文说明、推送分支和注释Tag，回读远端分支及Tag解引用指向同一提交；不强推或覆盖同名Tag，不把推送视作目标已部署。普通文案修改仍不默认触发发布或全矩阵。

交接材料默认交当前用户；用户已说明自行转发时，不沿用旧的企业微信收件人或代发安排。外部通知不能作为开发完成的默认步骤。

外部前端负责人启动、修改并测试其自己的仓库。DeepAgent 仅负责服务端契约和内置 Console。

## 完成检查清单

- 与改动风险相称的定向测试或简单冒烟通过；只有新失败或未解风险才扩大测试。
- 修改过的 JavaScript 通过 `node --check`；涉及的 Console Node 测试按需通过，不为纯 UI 改动重跑全部。
- 涉及时，迁移图检查和一次性环境的升级/降级测试通过。
- `git diff --check` 通过。
- 除非明确要求保留，否则浏览器、上下文、代理和测试进程均已关闭。
- 测试会话、Agent及Skill只按本轮清理授权处理；无清理授权则保留并标记，不能把“测试结束”当成删除授权。需要删除时先按[资源清理](agent-resource-lifecycle.md)核对会话级联影响、正式引用与私有备份。
- `git status --short` 仅包含预期改动和本地保留数据。
- diff 或测试数据中不包含秘密或客户数据。
- 仅在明确要求时提交/推送。
