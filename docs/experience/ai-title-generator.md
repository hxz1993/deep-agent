# AI 会话标题生成 — 实现经验

> 本文档提炼自 2026-07 迭代，记录 **会话 `title` 生成、回显、重命名** 的原则、目标、来源与产物，供后续追溯与维护。

相关文档：[`llm-model-modalities.md`](./llm-model-modalities.md)（同批次 LLM 模型元数据能力，独立主题）。

---

## 1. 目标

| 目标 | 说明 |
|------|------|
| **可识别的会话列表** | 侧边栏不再只显示 UUID 截断 |
| **AI 自动标题** | 首轮对话结束后异步生成简短标题 |
| **可靠兜底** | 无标题 LLM 或生成失败时，回退到首条用户文本 |
| **历史兼容** | 迁移前无 `title` 的会话，列表仍可读（`user_prompt` 回退） |
| **用户可控** | 支持手动改名、一键自动重命名（🔄） |
| **不破坏已有标题** | 手动标题、后续轮次 append 不被覆盖 |

---

## 2. 设计原则

### 2.1 前后端职责（决策 A）

| 层 | 行为 |
|----|------|
| **API** | 分返 `title` 与 `user_prompt`，不做拼接 |
| **前端** | 展示 `title \|\| user_prompt \|\| session_id 前缀` |

不在后端把 `user_prompt` 写入 `title` 字段「假装有标题」；空 `title` 即 `null`。

### 2.2 标题 LLM 与对话主模型

| 项 | 原则 |
|----|------|
| 对话主模型 | Agent 配置的 `llm_models` 行 / `AgentRunner.agent.model` |
| 标题模型优先级 | ① Admin「辅助模型」中配置的专用预设 → ② **对话主模型** → ③ 调用方文本兜底 |
| 专用模型 | 配置后始终优先于主模型（便于用便宜/快模型专门起标题） |
| 均未成功 | `generate_title()` 返回 `None` → runner / `PATCH auto` 回退 `first_user_prompt` |

### 2.3 用户文本不做特殊剥离

- `first_user_prompt()` 返回首条 user 消息的**完整文本**（含 DeepCube `<reference>…</user>` 等）。
- **不**做 `<user>` 标签剥离；兜底标题可能是长 context，属预期。

### 2.4 触发与并发

- 首轮交换完成后 `asyncio.create_task(_maybe_generate_title)`，**不阻塞** SSE 响应。
- 仅当 `get_session_title` 为空且为首次交换时自动生成。
- 已有 `title`（含用户手动设置）→ **不覆盖**。

### 2.5 持久化防覆盖（重要 bug 修复）

`conversation.py` 在 `append` 时：

- **仅当** `existing_title is None` 时，才用 `first_user_prompt(messages)` seed `title`。
- 避免后续轮次把新消息的 prompt 覆盖掉已有 AI/手动标题。

---

## 3. 数据来源

### 3.1 标题正文来源（生成时）

| 输入 | 来源 |
|------|------|
| `user_text` | `first_user_prompt(history)` |
| `assistant_text` | `_extract_assistant_text(new_messages)`（首轮仅 `TextPart`） |
| LLM 输出 | 专用标题模型 `Agent.run()` |
| 失败兜底 | `user_text`（runner / `PATCH auto` 一致） |

### 3.2 列表回显来源

| 字段 | 来源 |
|------|------|
| `title` | `chat_sessions.title` 列 |
| `user_prompt` | 从消息历史动态计算（`first_user_prompt`），**非**持久化列 |

### 3.3 标题 LLM 配置（辅助模型 + 集成测试 env）

**生产 / 日常开发**：在 Admin → **辅助模型** 标签页为 `title` 用例选择「模型配置」预设，或保持「主模型（auto）」。

**集成测试 seed**（`tests/helpers/sqlite_stores.py` 的 `seed_auxiliary_model_configs`）仍读取以下环境变量，将专用标题模型写入 `auxiliary_model_configs` 表：

```bash
DEEP_AGENT_TITLE_MODEL=deepseek-v4-flash
DEEP_AGENT_TITLE_MODEL_BASE_URL=https://api.example/v1
DEEP_AGENT_TITLE_MODEL_API_KEY=sk-...
DEEP_AGENT_TITLE_GENERATION_TIMEOUT=10
```

`DEEP_AGENT_TITLE_GENERATION_TIMEOUT` 由 `options.py` 加载，控制单次标题 LLM 超时（秒）。

---

## 4. 架构与链路

### 4.1 首轮自动标题

```
POST /chat/stream → AgentRunner.run → 首轮完成
  → asyncio.create_task(_maybe_generate_title)
       ├─ _is_first_exchange?
       ├─ existing title 为空?
       ├─ resolve_title_model(dedicated → main_model / spec)  # 单次解析
       ├─ generate_title(user, assistant, …)
       ├─ 失败 → user_text
       └─ update_session_title
```

### 4.2 手动 / 自动重命名

```
PATCH /api/v1/sessions/{id}?agent_id=...
  {"title": "新名称"}     → 直接写 DB
  {"auto": true}          → resolve AgentSpec → generate_title(spec=…) → 失败则 user_prompt
```

前端：`app.js` 侧边栏 🔄 → `PATCH {"auto": true}`；双击/编辑 → `PATCH {"title": ...}`。

### 4.3 LLM 调用方式（实现要点）

**必须使用 Pydantic AI `Agent.run()`**，不要用 `model.complete()`（`OpenAIChatModel` 无此 API）：

```python
agent = Agent(model, system_prompt=prompt, output_type=str)
result = await asyncio.wait_for(agent.run(user_content), timeout=timeout)
title = _clean_title(result.output)
```

---

## 5. 产物清单

### 5.1 后端

| 路径 | 说明 |
|------|------|
| `alembic/versions/014_chat_session_title.py` | `chat_sessions.title` 迁移 |
| `alembic/versions/015_auxiliary_model_configs.py` | `auxiliary_model_configs` 表 |
| `deep_agent/db/models.py` | `ChatSession.title`、`AuxiliaryModelConfig` |
| `deep_agent/db/stores/auxiliary_model/*.py` | 辅助模型 CRUD |
| `deep_agent/db/stores/chat_session/*.py` | `get/update_session_title`、列表带 `title` |
| `deep_agent/db/conversation.py` | append 时条件 seed title |
| `deep_agent/db/session_list.py` | `SessionListItem.title` / `user_prompt` |
| `deep_agent/agent/auxiliary_models.py` | 用例 catalog、`resolve_llm_model_for_use_case` |
| `deep_agent/agent/title_generator.py` | `generate_title`、`resolve_title_model`、`_clean_title` |
| `deep_agent/agent/history.py` | `first_user_prompt`、`_extract_assistant_text` |
| `deep_agent/agent/runner.py` | `_maybe_generate_title`、首轮触发 |
| `deep_agent/api/v1/auxiliary_models.py` | `GET/PATCH /auxiliary-models` |
| `deep_agent/api/v1/sessions.py` | 列表/详情返回 `title`+`user_prompt`；`PATCH` 重命名 |

### 5.2 前端

| 路径 | 说明 |
|------|------|
| `frontend/console/chat.js` | `title \|\| user_prompt` 展示；🔄 自动重命名；内联编辑标题 |
| `frontend/console/configuration/auxiliary-models.js` | Console「辅助模型」配置 UI |

### 5.3 测试

| 路径 | 说明 |
|------|------|
| `tests/unit/test_title_generator.py` | `_clean_title`、无主模型时 None、主模型回退 |
| `tests/integration/test_session_title.py` | 历史无 title 回显、手动不覆盖、无 LLM 兜底、live AI 标题 |
| `tests/integration/helpers/session_seed.py` | fixture 灌库 |
| `tests/integration/helpers/title_assertions.py` | 标题合理性断言 |
| `tests/integration/helpers/live_llm.py` | `skip_unless_title_llm` |
| `tests/helpers/sqlite_stores.py` | `ensure_title_uses_main_model`、`seed_auxiliary_model_configs` |
| `tests/integration/helpers/chat.py` | `mock_request_disconnect`、流式 AsyncClient 辅助 |
| `tests/run.ps1` | failfast + 进程级超时封装（单测默认 30s，全量加大） |
| `tests/fixtures/sessions/legacy_no_title.json` | 无 title 历史会话 |
| `tests/integration/fixtures/deepcube_tiered_rebate_session.json` | DeepCube 快路径（3 条消息） |
| `tests/fixtures/sessions/deepcube_tiered_rebate_full.json` | DeepCube 完整会话（~12 条，**可选**，默认不入库） |
| `tests/integration/helpers/export_session_fixture.py` | 从 DB 导出上述 full fixture |

---

## 6. 测试策略

### 6.1 原则

| 项 | 约定 |
|----|------|
| 框架 | **unittest**（仓库主入口），非 pytest |
| 对话主模型 | 集成测试 **mock**（`TestModel` / `patch _resolve_model`） |
| 标题 LLM | **真实调用**；未 seed 专用模型（`DEEP_AGENT_TITLE_MODEL` 未设）则 `@skip` |
| 运行目录 | 始终在**仓库根** |

### 6.2 Live 用例前置

```bash
DEEP_AGENT_TITLE_MODEL=...
DEEP_AGENT_TITLE_MODEL_API_KEY=...
DEEP_AGENT_TITLE_MODEL_BASE_URL=...   # 视提供商而定
```

### 6.3 运行命令

```bash
# 单元
venv\Scripts\python.exe -m unittest tests.unit.test_title_generator -v

# 集成（无 LLM 环境会 skip live 用例）
venv\Scripts\python.exe -m unittest tests.integration.test_session_title -v
```

PowerShell 带超时（排查单文件用默认 30s；**全量含 live 标题约 4 分钟**，需加大）：

```powershell
.\tests\run.ps1 -Target tests.integration.test_session_title -Verbose
.\tests\run.ps1 -Target tests -TimeoutSec 300
```

流式相关集成测试见 [`tests/integration/README.md`](../../tests/integration/README.md)：`/chat/stream` 须 `httpx.AsyncClient` + `mock_request_disconnect()`，**勿**用 `TestClient.stream()`（会无限阻塞）。

### 6.4 用例矩阵（集成）

| 用例 | 说明 |
|------|------|
| `test_legacy_session_without_title_returns_user_prompt` | API 分返；前端回退逻辑的数据基础 |
| `test_manual_title_is_not_overwritten_by_new_chat_turn` | append 不覆盖手动标题 |
| `test_auto_rename_uses_main_model_when_title_model_unconfigured` | 无专用模型时用主模型生成 |
| `test_auto_rename_falls_back_to_user_prompt_without_any_model` | LLM 全失败 → user_prompt |
| `test_first_chat_turn_generates_ai_title` | live，首轮异步生成 |
| `test_deepcube_fixture_auto_rename_uses_live_title_llm` | live，DeepCube fixture + `PATCH auto` |
| `test_deepcube_full_fixture_auto_rename_live_smoke` | live，完整 DeepCube 会话（文件缺失则 skip） |

### 6.5 准备 DeepCube 完整 fixture（可选）

`test_deepcube_full_fixture_auto_rename_live_smoke` 依赖本地文件  
`tests/fixtures/sessions/deepcube_tiered_rebate_full.json`。**未准备时自动 skip**，不影响 CI。

与快路径 `tests/integration/fixtures/deepcube_tiered_rebate_session.json`（仅 3 条消息）不同，完整版应包含同一场「阶梯返利」对话的全部 `ModelMessage`（通常 sequence `0`–`11`）。

**步骤：**

1. 在本地 UI 走完一整轮 DeepCube 阶梯返利对话（与快路径 fixture 同场景），记下 `session_id` 与 `agent_id`。
2. 在仓库根导出（使用当前 `.env` 数据库）：

```bash
venv\Scripts\python.exe -m tests.integration.helpers.export_session_fixture ^
  --session-id <你的-session-id> ^
  --agent-id <你的-agent-uuid> ^
  --output tests/fixtures/sessions/deepcube_tiered_rebate_full.json
```

3. 配置 `DEEP_AGENT_TITLE_MODEL*` 后单独跑 smoke：

```bash
venv\Scripts\python.exe -m unittest ^
  tests.integration.test_session_title.SessionTitleIntegrationTests.test_deepcube_full_fixture_auto_rename_live_smoke -v
```

**JSON 格式**（与 `legacy_no_title.json` 相同）：

```json
{
  "session_id": "...",
  "agent_id": "...",
  "title": null,
  "messages": [{ "sequence": 0, "payload": { "kind": "request", "parts": [...] } }, ...]
}
```

`payload` 为 DB `chat_messages.payload` 原样导出；含真实业务 prompt 时**默认勿提交 Git**，仅本机回归用。

### 6.6 非标题集成测试须隔离后台标题任务

本地 `.env` 若已配置 `DEEP_AGENT_TITLE_MODEL*`，`make_sqlite_stores()` 会通过 `seed_auxiliary_model_configs` 将专用标题模型写入测试库；其它 chat / HITL / MCP 流式集成测试在首轮对话后仍会触发 `AgentRunner._maybe_generate_title`（后台真实 LLM 调用）。后果包括：

- teardown 卡在 `title_generation_timeout`（默认 30s）
- HITL 续聊、history 断言被异步标题任务干扰

**约定**（仅 `test_session_title.py` 等标题专用文件除外）：

1. 在 `setUp` / 用例开头调用 `tests/helpers/sqlite_stores.py` 的 `ensure_title_uses_main_model(stores)`（将 `title` 用例设回 auto）。
2. 对易受影响用例再 `patch("deep_agent.agent.runner.AgentRunner._maybe_generate_title", new_callable=AsyncMock)`，并在 `tearDown` / `finally` 中 `stop()`。
3. 流式用例配合 `mock_request_disconnect()`，见 integration README §3。

标题专用 live 用例（`test_first_chat_turn_generates_ai_title` 等）**不要**调用 `ensure_title_uses_main_model`，应通过 `_apply_title_env_to_store()` / `seed_auxiliary_model_configs` 显式启用专用标题模型。

---

## 7. 运维与使用

### 7.1 启用 AI 标题

1. **推荐**：Admin → **辅助模型** → 为「标题生成」选择便宜/快的模型预设  
2. **或**：保持 auto，自动回退到各 Agent 绑定的**对话主模型**  
3. 新会话首轮对话结束后，侧边栏标题异步更新

### 7.2 仅文本兜底（无 AI）

专用模型与主模型均不可用，或 LLM 调用失败 → 回退首条用户文本；历史会话靠 `user_prompt` 显示。

### 7.3 手动改标题

- UI：会话列表内联编辑，或聊天区标题编辑  
- API：`PATCH /api/v1/sessions/{id}` + `{"title": "..."}`

### 7.4 重新 AI 命名

- UI：会话行 🔄  
- API：`PATCH` + `{"auto": true}`（基于**当前**全量 history，非仅首轮）

---

## 8. 陷阱与已知限制

| 项 | 说明 |
|----|------|
| `_clean_title` 顺序 | 先截断再加 `...`，**之后**不要再 `rstrip` 掉省略号 |
| 首轮仅 tool_call | `assistant_text` 可能为空 → 传入 `assistant_text or user_text` |
| DeepCube 长 prompt 兜底 | 无 LLM 时兜底标题=整段 user 消息，可能很长 |
| `event_persister.flush` | 集成测试需传 `agent_id`，否则读不到会话 |
| 异步标题 | 首轮 SSE 结束后标题可能稍晚写入，测试用 `wait_for_session_title` 轮询 |
| `.env` 配了标题模型 | 非标题集成测试须 `ensure_title_uses_main_model(stores)` + 必要时 patch `_maybe_generate_title`，见 §6.6 |
| 全量测试超时 | 含 live 标题时约 270 用例 / ~4 分钟；`run.ps1` 默认 30s 或 180s 会误杀，用 `-TimeoutSec 300` |
| `TestClient.stream` | SSE 集成测试会挂死；改用 `AsyncClient`，见 integration README |
| `_extract_assistant_text` 重复 | `title_generator.py` 与 `history.py` 各有一份；**runner 使用 `history` 版** |

---

## 9. 维护检查清单

- [ ] 改 `title_generator` 后跑 `tests/unit/test_title_generator.py`
- [ ] 改 sessions API / conversation 后跑 `tests/integration/test_session_title.py`
- [ ] 改前端展示逻辑时确认 `title \|\| user_prompt` 仍成立
- [ ] 新环境部署后执行迁移 `014`，确认 `chat_sessions.title` 存在
- [ ] Live 验证：Admin 或 `DEEP_AGENT_TITLE_MODEL*` seed 后跑 skip 以外的集成用例
- [ ] 新增 chat 流式集成测试：确认已禁用后台标题任务（§6.6），且未使用 `TestClient.stream`

---

## 10. 后续可选优化

- [ ] 标题生成失败监控 / 指标
- [ ] `DEEP_AGENT_TITLE_MAX_LENGTH` 可配置化（当前硬编码 30）
- [ ] `auto` 重命名时可选只用首轮消息而非全量 history
- [ ] 从 DeepCube prompt 中提取简短 `<user>` 段（**当前明确不做**）

---

*最后更新：2026-07-13*
