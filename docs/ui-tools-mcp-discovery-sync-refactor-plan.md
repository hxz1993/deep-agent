# MCP UI 工具同步机制评审与后续改造计划

> 状态：待后端评审，当前不实施
> 记录日期：2026-07-20
> 影响仓库：`deep-agent`
> 关联文档：`docs/rfc/0001-ui-tools-mcp-discovery-and-api.md`

## 1. 结论

本轮讨论确认以下决策：

1. DeepAgent 继续使用现有 Pydantic AI Deferred UI 工具机制处理追问表单、确认表单和人工补充信息。
2. 不引入 `operation_id`，不新增待处理业务操作表。普通追问表单只有对话暂停状态，没有需要跨调用恢复的业务操作。
3. 暂不迁移到官方 MCP Elicitation 或 MCP Apps，也不改动当前 RFC 0001 的 Manifest 格式和数据库结构。
4. MCP 同步 UI 工具的能力暂时保留，但后续建议从“Agent 运行时按 TTL 自动发现”调整为“配置保存后同步一次 + 用户手工同步”。
5. 当前阶段只记录方案，不修改 UI 工具发现、同步或运行时逻辑，待与原开发同事确认设计背景后再实施。

目标链路如下：

```text
创建或修改 URL MCP
  -> 保存 MCP 配置
  -> 同步一次 UI 工具定义
  -> 将定义快照写入 ui_tool_definitions

日常聊天
  -> 只读取数据库中的 UI 工具定义
  -> 不连接 MCP 做 UI 发现
  -> 不检查 refresh_seconds

MCP 的 UI 定义发生变化
  -> 管理员点击“同步 UI 工具”
  -> 重新拉取并覆盖数据库快照
```

## 2. 问题背景

DeepAgent 当前存在两类工具：

| 类型 | 执行位置 | 运行语义 |
| --- | --- | --- |
| MCP 业务工具 | MCP Server | 模型调用后由 MCP 执行业务逻辑并返回结果 |
| UI 工具 | DeepAgent 客户端/前端 | 模型调用后返回 Deferred 请求，本轮 Agent Run 结束，等待用户填写后重新启动 Agent Run |

RFC 0001 为外部 UI 工具设计了两种来源：

- MCP Server 通过约定 Tool 或 Resource 发布 UI 工具 Manifest。
- 管理后台通过 UI 工具 API 手工注册定义。

来自 MCP 的 Manifest 不在运行时直接使用，而是先同步到 `ui_tool_definitions` 表，再由 Agent 组装过程读取并转换为 Pydantic AI `ExternalToolset`。

这套设计原本希望解决：MCP Server 可以独立发布 UI Schema，DeepAgent 能自动获取变更。但当前实际使用场景主要是内部自研 MCP，UI 工具变化频率低，同时管理后台已经提供手工 UI 工具配置，因此在聊天主链路中持续检查 UI Manifest 的收益有限。

## 3. 当前实现

### 3.1 MCP UI 配置

MCP 的 `config` JSON 可包含：

```json
{
  "ui_tools": {
    "enabled": true,
    "mode": "tool",
    "refresh_seconds": 300
  }
}
```

缺省值为：

```text
enabled = true
mode = tool
refresh_seconds = 300
```

相关代码：

- `deep_agent/capabilities/mcp/ui_discovery.py::resolve_ui_tools_config`
- `frontend/admin/mcp.js::fillUiToolsFields/readUiToolsConfig`

### 3.2 发现模式

| mode | 当前行为 |
| --- | --- |
| `tool` | 连接 MCP，查找并调用约定工具 `ui_tools_list` |
| `resource` | 连接 MCP，读取 `ui://tools/manifest` |
| `none` | 返回空定义，不执行发现 |

两种发现方式最终都生成同一种 RFC 0001 Manifest，并调用 `UiToolStore.sync_mcp_tools()` 写入数据库。

当前发现协议是 DeepAgent 内部扩展，不是官方 MCP Elicitation 或 MCP Apps 协议。

### 3.3 自动发现触发点

当前不存在后台定时任务。`refresh_seconds=300` 是缓存 TTL，而不是每 300 秒主动执行的调度器。

实际调用链：

```text
POST /chat 或 /chat/stream
  -> build_runner_for_agent
  -> _build_runner_from_spec
  -> prepare_ui_toolset_for_spec
  -> ensure_mcp_ui_tools_discovered
  -> should_refresh_mcp_ui_tools
  -> discover_and_persist_ui_tools（过期时）
```

因此：

- 没有聊天请求时不会自动发现。
- 超过 300 秒后不会立即执行；下一次组装 Agent 时才检查。
- 需要同步时，该次聊天请求会等待 MCP 初始化、`tools/list` 或 Resource 读取、Manifest 解析和数据库写入。
- 单次发现使用 `DISCOVERY_TIMEOUT_SEC=10`，可能给首轮聊天增加最多约 10 秒等待。

### 3.4 空结果的重复发现

当前 TTL 通过已同步 UI 工具的 `discovered_at` 计算。判断逻辑为：

```python
if not cached:
    return True
```

如果 MCP：

- 没有实现 `ui_tools_list`；
- 合法返回空 `tools`；
- 已同步工具全部被禁用；
- 首次发现失败且没有缓存；

数据库中就没有可用于记录“最近检查时间”的启用记录。下次组装 Agent 时仍会立即再次发现，`refresh_seconds` 对空结果不生效。

### 3.5 Builtin MCP

后端明确禁止 `transport=builtin` 的 MCP 参与 RFC 0001 UI 工具发现：

- `mcp_rows_for_ui_discovery()` 跳过 Builtin。
- `fetch_ui_tools_manifest()` 对 Builtin 返回空列表。
- `discover_and_persist_ui_tools()` 对 Builtin返回 `0/0/0`。
- `build_discovery_toolset()` 只接受 URL transport。

这不代表平台不能有内置 UI 工具。内置 UI 工具由 `capabilities/ui/builtin.py` 直接注册，不经过 MCP Manifest 同步。

前端目前没有完全遵守该规则：

- Builtin 行仍显示“刷新 UI”。
- Builtin 编辑弹窗仍显示 UI 工具发现配置。
- 前端将任意 `0/0/0` 结果猜测为“内置 MCP 不参与 UI 工具发现”。URL MCP 没有 Manifest 时也会得到错误提示。

### 3.6 手工同步

管理页面的“刷新 UI”调用：

```http
POST /api/v1/mcp/{name}/ui-tools/refresh
```

同步规则是全量快照：

- Manifest 中存在的定义执行新增或更新。
- 数据库中原有、但新 Manifest 不再返回的 MCP 来源定义会被删除。
- 调用异常时，Agent 运行时自动发现会使用旧缓存；管理端手工刷新使用 `use_cache_on_error=false`，返回错误且不执行空清单同步。

需要特别注意：关闭 `enabled` 或选择 `mode=none` 后，`fetch_ui_tools_manifest()` 返回空列表。若此时仍手工调用刷新接口，空列表会进入 `sync_mcp_tools()`，可能删除此前由该 MCP 同步的定义。

### 3.7 普通 MCP 工具列表可能暴露发现工具

RFC 0001 约定 `ui_tools_list` 只供平台发现，不暴露给模型。但当前正常 URL MCP Toolset 的构建链路没有发现明确的工具过滤器。

如果 MCP Server 将 `ui_tools_list` 放入标准 `tools/list`，除发现连接外，正常 Agent MCP Toolset 也可能向模型暴露带前缀的 `<mcp_name>__ui_tools_list`。实施后续改造前应补一条真实工具列表测试确认该行为。

## 4. 关闭“启用 UI 发现”的准确效果

将 MCP 的 `ui_tools.enabled` 设置为 `false` 后：

1. `mcp_rows_for_ui_discovery()` 会跳过该 MCP。
2. Agent 组装时不会主动连接该 MCP 获取 UI Manifest。
3. 已经写入 `ui_tool_definitions` 的 MCP 来源 UI 工具不会自动删除。
4. 已同步且仍为 enabled 的 UI 工具仍可能继续被 Agent 加载和使用。
5. 普通 MCP 业务工具不受影响。

因此，“关闭启用 UI 发现”可以作为当前临时规避措施，用于停止聊天运行时的主动发现。

但关闭后应避免点击“刷新 UI”，原因见 3.6。若目标是不再使用已经同步的 UI 工具，还需要在 UI 工具管理中单独停用或删除对应定义。

## 5. 后续建议方案

### 5.1 后端

#### 5.1.1 从 Agent 运行主链路移除发现

调整 `prepare_ui_toolset_for_spec()`：

```text
当前：
  ensure_mcp_ui_tools_discovered()
  load_effective_ui_tools()

调整后：
  load_effective_ui_tools()
```

聊天只读取数据库快照，不再检查 `refresh_seconds`，也不因 UI 工具发现连接外部 MCP。

候选清理项：

- 删除或停止调用 `ensure_mcp_ui_tools_discovered()`。
- 删除或停止调用 `should_refresh_mcp_ui_tools()`。
- `refresh_seconds` 保留兼容解析但不再生效，后续版本再清理。

#### 5.1.2 保留同步实现和接口

保留：

- `fetch_ui_tools_manifest()`。
- `discover_and_persist_ui_tools()`。
- `POST /api/v1/mcp/{name}/ui-tools/refresh`。
- `tool/resource/none` 模式。
- RFC 0001 Manifest 和 canonical name。
- `ui_tool_definitions` 数据结构。

#### 5.1.3 注册式同步

为了控制改动范围，建议由管理前端在 URL MCP 创建或修改成功后立即调用现有刷新接口，而不是修改 MCP CRUD 响应结构。

```text
POST/PATCH MCP 成功
  -> POST /mcp/{name}/ui-tools/refresh
```

同步失败不回滚已经保存的 MCP：

```text
MCP 保存成功，但 UI 工具同步失败
```

普通 MCP 业务工具仍可继续使用。

如果未来存在不经过管理前端、直接调用 API 注册 MCP 的稳定需求，再将“保存后同步”下沉为后端服务层行为，并设计包含同步结果的响应结构。

#### 5.1.4 Builtin 返回明确语义

手工同步 Builtin 时不再返回无语义的 `0/0/0`。建议返回明确的 400/422 业务错误，说明 Builtin UI 工具由 DeepAgent 代码注册，不参与 RFC 0001 同步。

#### 5.1.5 明确空清单和失败

保留全量同步语义：

- 合法 Manifest `tools: []`：视为服务端主动清空定义，可删除旧快照。
- 连接失败、工具不存在、Resource 不存在、Manifest 非法：视为同步失败，不删除旧快照。

当前“工具不存在”被转换为空列表，需要调整为明确的“未实现发现协议”结果，避免误删旧定义。

可为 `UiToolsRefreshResponse` 增加不涉及 DDL 的状态字段，例如：

```text
synced
empty
unsupported
skipped
failed
```

前端不再根据数字组合猜测结果。

#### 5.1.6 过滤平台发现工具

确认并保证 `ui_tools_list` 不进入模型可调用的正常 MCP 工具列表。发现专用连接仍可调用该工具，业务 Agent Toolset 应过滤保留名称。

### 5.2 前端

#### 5.2.1 保存后同步

URL MCP 创建或修改成功后：

1. 先保留 MCP 保存成功状态。
2. 若 `ui_tools.enabled=true` 且 mode 不是 `none`，调用同步接口一次。
3. 分开反馈保存结果和同步结果。

示例：

```text
MCP 保存成功，已同步 3 个 UI 工具。
MCP 保存成功，但该服务未提供 UI 工具清单。
MCP 保存成功，但 UI 工具同步失败：<后端错误>。
```

#### 5.2.2 手工按钮

- “刷新 UI”改为“同步 UI 工具”。
- 仅 URL MCP 且启用同步时展示。
- Builtin 不展示按钮。
- mode=none 时不展示或禁用，并说明原因。

#### 5.2.3 移除刷新间隔

删除“刷新间隔（秒）”输入，不再向新配置写入 `refresh_seconds`。读取历史配置时忽略该字段，不需要数据迁移。

#### 5.2.4 调整配置文案

建议将：

```text
UI 工具发现（RFC 0001）
启用 UI 发现
发现模式
```

调整为：

```text
MCP UI 工具同步
启用 UI 工具同步
同步来源
```

同步来源说明：

```text
MCP 工具：调用 ui_tools_list 获取定义
MCP 资源：读取 ui://tools/manifest 获取定义
不启用：不从该 MCP 同步 UI 工具
```

不在普通用户文案中强调内部 RFC 编号。

#### 5.2.5 Builtin 编辑页面

Builtin 不展示整块 MCP UI 同步设置。Builtin UI 工具的来源和管理方式如需说明，放在只读帮助文字中，不提供无效控件。

## 6. 不在本次后续改造范围内

以下内容已经讨论，但暂不处理：

- 不改 Pydantic AI Deferred 工具和 `deferred_tool_results` 续跑链路。
- 不引入业务 `operation_id`。
- 不新增 pending interaction 表。
- 不处理历史 pending 表单依赖当前 UI 工具定义的问题。
- 不处理历史请求 App/Space 与后续回答 App/Space 不一致问题。
- 不调整 `UiToolDefinition.agent_ids` 的反向 Agent 关联。
- 不迁移官方 MCP Elicitation。
- 不迁移 MCP Apps。
- 不修改复杂表单或电子表格 UI 能力。

## 7. 影响面

预计涉及文件：

```text
deep_agent/capabilities/ui/registry.py
deep_agent/capabilities/mcp/ui_discovery.py
deep_agent/capabilities/mcp/toolset_builder.py（仅当补 ui_tools_list 过滤）
deep_agent/api/v1/mcp.py
deep_agent/schemas/ui_tools.py（仅当增加明确同步状态）
frontend/admin/index.html
frontend/admin/mcp.js
frontend/console/configuration.js
frontend/console/configuration-core.js
tests/unit/test_ui_tools_rfc.py
tests/unit/test_mcp_resolver.py
tests/integration/（同步接口和 Agent 运行链测试）
tests/frontend/console/（配置交互测试）
```

不需要：

- DDL。
- Alembic。
- 修改 DeepCube SDK。
- 修改 mcp-biz-go。
- 修改现有会话表和消息表。

## 8. 测试计划

### 8.1 后端

1. 构建 Agent 时不调用 `fetch_ui_tools_manifest()`。
2. `refresh_seconds` 过期不触发聊天运行时发现。
3. 数据库已有 UI 工具时，Agent 正常加载并生成 Deferred 请求。
4. 手工 tool 模式同步成功。
5. 手工 resource 模式同步成功。
6. 合法空 Manifest 按设计清空旧定义。
7. MCP 未实现发现协议时返回 unsupported，保留旧定义。
8. MCP 调用错误或 Manifest 非法时返回 failed，保留旧定义。
9. Builtin 手工同步返回明确业务错误。
10. `ui_tools_list` 不出现在模型可调用工具列表中。
11. 普通 MCP 业务工具列表和调用行为不受影响。

### 8.2 前端

1. URL MCP 创建后自动同步一次。
2. URL MCP修改后自动同步一次。
3. MCP 保存成功、同步失败时弹窗不关闭失败状态且错误可见。
4. 手工“同步 UI 工具”显示真实 discovered/upserted/removed。
5. 未实现 Manifest 时不再提示“内置 MCP”。
6. Builtin 不显示 UI 同步配置和按钮。
7. 不再显示刷新间隔。
8. 历史带 `refresh_seconds` 配置能够正常编辑。
9. UI 工具管理列表能看到同步后的新增、更新和删除结果。

### 8.3 回归

1. Agent、模型、MCP、UI 工具和辅助模型 CRUD。
2. DeepCube 助手正常聊天。
3. 合法与非法追问表单。
4. Pending 表单刷新恢复。
5. MCP Header inherit/isolated 策略。
6. Builtin MCP 正常加载和调用。

## 9. 实施顺序

1. 与原开发同事确认 RFC 0001 的真实使用方，以及是否有 MCP 实现了 resource 模式。
2. 确认是否允许合法空 Manifest 删除旧定义。
3. 补充当前运行时自动发现和空结果重复发现的缺陷测试。
4. 从 Agent 组装链路移除自动发现。
5. 保留并收敛手工同步接口返回语义。
6. 实现前端保存后同步。
7. 隐藏 Builtin 无效配置。
8. 删除前端刷新间隔。
9. 过滤 `ui_tools_list` 模型可见性。
10. 完成目标测试、完整单元测试、集成测试和真实 URL MCP 验证。
11. 在 Test 环境验证后再提交发布。

## 10. 待同事确认

1. 目前是否存在真实 MCP Server 实现 `ui_tools_list`？具体是哪一个？
2. 是否存在真实 MCP Server 使用 `ui://tools/manifest` resource 模式？
3. 当时将发现放进 Agent 组装主链路，是为了支持 MCP 独立发布后自动热更新，还是仅为了省去首次手工同步？
4. 是否期望 MCP 合法返回空 Manifest 时删除全部旧定义？
5. 是否允许删除 `refresh_seconds`，改成只在保存和手工操作时同步？
6. `ui_tools_list` 是否曾在真实模型工具列表中出现？如果没有，是 MCP 服务端做了隐藏，还是客户端另有未定位的过滤逻辑？
7. UI 工具的长期方向是继续维护 RFC 0001，还是仅作为过渡方案，后续评估官方 Elicitation/MCP Apps？

## 11. 当前临时操作建议

在正式改造前，如果某 URL MCP 不需要同步 UI 工具：

1. 编辑 MCP，关闭“启用 UI 发现”。
2. 不点击该 MCP 的“刷新 UI”。
3. 如果此前已经同步了 UI 工具，根据需要在 UI 工具管理中单独停用或删除。
4. 验证普通 MCP 业务工具仍可调用。

这个临时配置能够停止 Agent 运行时的主动发现，但不会改变当前代码结构，也不会自动清理已有 UI 工具快照。
