# LLM 模型 input_modalities 元数据 — 实现经验

> 本文档提炼自 2026-07 迭代对话，记录 **LLM 模型输入模态（`input_modalities`）** 的解析、刷新、离线兜底与 Admin 操作入口，供后续维护与追溯。

相关文档：[`ai-title-generator.md`](./ai-title-generator.md)（同批次会话标题能力，独立主题）。

---

## 1. 目标

| 目标 | 说明 |
|------|------|
| **持久化模型能力** | `llm_models` 表存储 `input_modalities` + `capabilities_source` |
| **可刷新** | 支持单条 / 全量从外部元数据源刷新写入 DB |
| **离线可用** | 无法访问公网时，仍能靠仓库内 JSON 兜底解析 |
| **兼容现有配置** | test 等环境 **裸 `model` 名 + `base_url`** 无需改成 `provider:model` 也能刷新 |
| **可运维** | Admin「模型配置」页可一键全量刷新、按行刷新，并展示模态与来源 |

---

## 2. 设计原则

### 2.1 数据分层（优先级从高到低）

1. **用户覆盖（`capabilities_source=user`）**  
   - 通过 `PATCH /llm-models/{id}` 设置 `input_modalities` 时自动标记为 `user`  
   - **全量刷新默认跳过**；单条 `refresh-metadata` 使用 `force=True` 可覆盖

2. **models.dev（在线，`capabilities_source=models_dev`）**  
   - 公网部署首选；拉取 [models.dev](https://models.dev) 官方 catalog

3. **内置 bundled（`capabilities_source=bundled`）**  
   - 仓库内 `modality_data.json`；**离线部署主兜底**，由维护者 Git 提交更新

4. **名称启发式（`capabilities_source=heuristic`）**  
   - `_name_heuristic()` 子串规则；仅在前三层均未命中时使用

### 2.2 模型字符串与 lookup key

- **不强制** `provider:model` 格式；对话调用本身支持裸名 + `base_url`（OpenAI 兼容路径）。
- 刷新 / 解析时通过 `build_model_lookup_keys(model, base_url)` 生成候选 key：
  - 原串、`:` → `/`、裸名
  - 常见 provider 前缀（`openai`、`deepseek`、`moonshotai` 等）
  - 从 `base_url` 推断 provider（如 `api.moonshot.cn` → `moonshotai/kimi-k2.6`）
- Provider 别名：`moonshot` → `moonshotai` / `moonshotai-cn`；`zhipu` → `zhipuai`

### 2.3 两个 JSON 文件职责分离

| 文件 | 职责 | 维护方式 |
|------|------|----------|
| `modality_data.json` | **离线大兜底**；refresh 链第 3 层（bundled） | **人工 Git 维护**；可用 models.dev 校对后提交 |
| `models_dev_snapshot.json` | `models_dev.py` 在线拉取失败时的小快照 | 可选；测试 mock 用 |

**不要混淆**：`modality_data.json` 不是 models.dev 自动导出；权威校对源是 `https://models.dev/api.json`。

### 2.4 API 回显 vs 自动解析

- `GET /llm-models` **原样返回 DB 字段**；空库回显 `[]`，与 model 字符串格式无关。
- 附件能力等运行时逻辑应优先读 DB 已刷新的 `input_modalities`（`runner` 传 DB 值给 resolver 仍为待完善点）。

---

## 3. 数据来源

### 3.1 models.dev（在线）

- **Catalog URL**：`https://models.dev/api.json`
- 模型条目结构：`modalities.input` → `["text", "image", ...]`；`limit.context` → 上下文长度
- 实现：`deep_agent/agent/models/models_dev.py`  
  - `fetch_models_dev_catalog()` 下载并进程内缓存  
  - `lookup_modalities_in_catalog()` 按 lookup key 查找

> **历史说明**：曾误用 `GET /api/v1/models/{key}`（返回 HTML），已改为 `api.json`。

### 3.2 modality_data.json（离线 bundled）

- 路径：`deep_agent/agent/models/modality_data.json`
- 当前条目：常见 OpenAI / Anthropic / Google / DeepSeek / Kimi 等 + **test 环境三档模型**
- 字段：目前代码**仅消费** `input_modalities`；`max_context` 预留未用
- 更新流程建议：
  1. 用 `api.json` 核对目标模型 `modalities.input`
  2. 修改 JSON（含裸名别名，如 `kimi-k2.6` 与 `moonshotai/kimi-k2.6`）
  3. Git 提交

### 3.3 test 环境参考（裸名配置）

| name | model | base_url | 期望 modalities（models.dev / bundled） |
|------|-------|----------|----------------------------------------|
| deepseek-v4-flash | `deepseek-v4-flash` | `https://api.deepseek.com` | `["text"]` |
| kimi-k2.6 | `kimi-k2.6` | `https://api.moonshot.cn` | `["text", "image", "video"]` |
| kimi-k2.7-code-highspeed | `kimi-k2.7-code-highspeed` | `https://api.moonshot.cn` | `["text", "image", "video"]` |

---

## 4. 刷新链路

```
POST /api/v1/llm-models/refresh-metadata          # 全量，skip user 行
POST /api/v1/llm-models/{id}/refresh-metadata     # 单条，force=True

refresh_model_modalities(row)
  ├─ 1. fetch_models_dev_modalities(model, base_url=row.base_url)  → models_dev
  ├─ 2. _resolve_from_bundle(model, base_url)                      → bundled
  └─ 3. _name_heuristic(model)                                      → heuristic
        → store.update_metadata(input_modalities, capabilities_source)
```

运行时解析（不刷新 DB）：`resolve_input_modalities()` — DB 持久化值（user/models_dev/bundled）→ bundled → heuristic。

---

## 5. 产物清单

### 5.1 后端

| 路径 | 说明 |
|------|------|
| `alembic/versions/014_chat_session_title_and_llm_modalities.py` | 表字段迁移 |
| `deep_agent/db/models.py` | `LlmModel.input_modalities`, `capabilities_source` |
| `deep_agent/schemas/llm_model.py` | API schema |
| `deep_agent/db/stores/llm_model/base.py` | `update_metadata`；PATCH modalities → `user` |
| `deep_agent/api/v1/llm_models.py` | CRUD + refresh + `GET /llm-models/catalog` |
| `deep_agent/agent/models/model_lookup.py` | lookup key 与 base_url 推断 |
| `deep_agent/agent/models/models_dev.py` | models.dev `api.json` 客户端 |
| `deep_agent/agent/models/modality_resolver.py` | 解析链 + `refresh_*` |
| `deep_agent/agent/models/modality_data.json` | **离线 bundled 数据（Git 维护）** |
| `deep_agent/agent/models/models_dev_snapshot.json` | 在线失败小兜底 |
| `deep_agent/agent/models/catalog.py` | models.dev + bundled **目录浏览**（筛选 / 分页 / 按 provider 懒解析） |
| `deep_agent/schemas/model_catalog.py` | `GET /llm-models/catalog` 响应 schema |
| `deep_agent/agent/attachments.py` | `model_supports_image_input()` 调用 resolver |

### 5.2 前端

| 路径 | 说明 |
|------|------|
| `frontend/console/catalog-template.html` + `configuration/catalog.js` | **模型模态目录**：浏览 models.dev / 内置 JSON；筛选 provider / 模态；分页；「刷新」带 `refresh=true` 清缓存 |
| `frontend/console/configuration-template.html` | 模型表：输入模态 / 来源列；「刷新全部 Meta」按钮 |
| `frontend/console/configuration/llm-models.js` | 全量 / 单条 refresh；列表展示 |
| `frontend/console/configuration/configuration.css` | `panel-toolbar-actions` 样式 |

入口：

- `/console/#/configuration` → **模型目录**：按原生模态挑选模型。
- `/console/#/configuration` → **模型**：管理已保存预设与刷新 Meta。

### 5.3 测试

| 路径 | 说明 |
|------|------|
| `tests/unit/test_modality_resolver.py` | bundled 解析、test 环境裸名 refresh |
| `tests/unit/test_models_dev.py` | catalog lookup |
| `tests/unit/test_catalog.py` | 目录聚合、按 provider 懒解析、refresh 清缓存 |
| `tests/integration/test_llm_model_modalities.py` | CRUD、四层 refresh、batch skip user |
| `tests/integration/test_llm_model_catalog.py` | `GET /llm-models/catalog` 筛选 / 分页 / 已配置标记 / 回退 |
| `tests/integration/helpers/models_dev.py` | models.dev mock |

---

## 6. 模型模态目录（选型浏览）

与 Admin「已保存预设」不同，**目录页**面向选型：展示 models.dev 在线 catalog 与仓库内 `modality_data.json` 的全量原生 `input_modalities`。

| 项 | 说明 |
|----|------|
| **页面** | `frontend/console/catalog-template.html` |
| **API** | `GET /api/v1/llm-models/catalog` |
| **数据源** | `source=auto`（优先 models.dev，失败回退内置 JSON） / `online` / `bundled` |
| **筛选** | `provider`、`modality`、`q`（子串搜索） |
| **分页** | `offset` + `limit`（默认 100，最大 500） |
| **刷新** | `refresh=true` 清除进程内 models.dev 与 catalog 解析缓存 |
| **已配置** | 仅当 DB 预设 `model` 为完整 `provider:model-id` 时，在对应行显示预设名称 |
| **内存** | models.dev 按 **provider 懒解析** 并缓存；Provider 下拉来自 catalog 顶层 key，无需先解析全部 model |

```bash
curl "http://127.0.0.1:8000/api/v1/llm-models/catalog?source=auto&modality=image&limit=20"
curl "http://127.0.0.1:8000/api/v1/llm-models/catalog?source=online&refresh=true"
```

---

## 7. 运维操作

### Admin UI

- **刷新全部 Meta**：`POST /api/v1/llm-models/refresh-metadata`（跳过 `user` 行）
- **单行刷新 Meta**：`POST /api/v1/llm-models/{id}/refresh-metadata`（强制覆盖）

### curl

```bash
# 全量
curl -X POST http://127.0.0.1:8000/api/v1/llm-models/refresh-metadata

# 单条
curl -X POST http://127.0.0.1:8000/api/v1/llm-models/{model_id}/refresh-metadata
```

### 来源标签（Admin 展示）

| `capabilities_source` | 含义 |
|-----------------------|------|
| `user` | 手动 PATCH |
| `models_dev` | models.dev 在线 catalog |
| `bundled` | `modality_data.json` |
| `heuristic` | 名称规则兜底 |
| （空） | 尚未刷新 |

---

## 8. 已知限制与后续

| 项 | 说明 |
|----|------|
| `max_context` | JSON 中有字段，业务代码尚未使用 |
| 运行时附件判断 | ``runner._vision_capable`` 读取 ``AgentSpec`` 中的 DB ``input_modalities``（构建 Runner 时快照）；Admin 刷新后下轮请求生效 |
| `modality_data.json` 覆盖率 | 仅覆盖常用模型；未在 models.dev 的 legacy ID 仍为人工值 |
| models.dev 缓存 | 进程内内存缓存；目录页「刷新」或 `refresh=true` 可清；测试用 `clear_models_dev_cache()` |

---

## 9. 维护检查清单

- [ ] 新增常用模型：在 `modality_data.json` 增加 `provider/model` + 裸名别名
- [ ] 公网环境验证 refresh 后 `capabilities_source=models_dev`
- [ ] 离线环境验证 refresh 后 `capabilities_source=bundled` 且 modalities 合理
- [ ] 用户手动配置后确认全量刷新不会覆盖（`user` 行保持）
- [ ] 跑测试：`tests/unit/test_modality_resolver.py`、`tests/unit/test_models_dev.py`、`tests/unit/test_catalog.py`、`tests/integration/test_llm_model_modalities.py`、`tests/integration/test_llm_model_catalog.py`

---

*最后更新：2026-07-14*
