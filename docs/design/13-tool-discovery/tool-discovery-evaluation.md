# 工具检索评估与验收方案

## 1. 为什么必须先评估

工具检索的错误不是普通搜索结果不好看：漏掉必需工具会让模型误以为能力不存在，错放写入工具会
增加误调用风险。因此不能只用三五个演示 Prompt 判断算法可用，也不能只看 Token 降幅。

第一优先级是所需工具 Recall，其次才是 Schema Token、延迟和 Precision。

## 2. 评估数据集

从真实 DeepFOS 场景构建版本化数据集，每条记录至少包含：

```json
{
  "id": "finance-query-001",
  "query": "查询当前财务模型 2026 年预算工作版的数据",
  "context_ref_types": ["deeptable.selection"],
  "authorized_packages": ["mcp:deepfos-mcp-test", "ui:deeptable"],
  "required_tools": [
    "deepfos_platform_element_search",
    "deepcube_query_cube_data"
  ],
  "optional_tools": ["deepcube_resolve_members"],
  "forbidden_tools": ["deepcube_save_cube_data"],
  "notes": "只读查询，不应提升写入工具"
}
```

覆盖类型：

- 中文自然语言。
- 英文协议名。
- 中英混合。
- 同义表达和口语表达。
- 明确只读与明确写入。
- 同一领域下 query/save/delete 等易混动作。
- 需要两个或多个工具串联。
- 多 MCP 包同名或相似工具。
- 无对应工具。
- 包含噪声 Context Reference Label。
- 工具重命名、描述变更和 Catalog Hash 更新。

数据集应来源于脱敏后的真实用例与人工设计的高风险对照，不直接保存用户敏感数据。

## 3. 对照方案

至少比较：

1. 全量披露：当前基线。
2. Pydantic 默认 Keyword Overlap。
3. 中文感知 BM25。
4. 多语言 Embedding。
5. BM25 + Embedding RRF 混合。
6. 可选 LLM Selector。
7. 支持时的 Provider Native Tool Search。

同一 Tool Catalog、授权范围和查询集运行，避免把目录差异误当成算法差异。

## 4. 离线指标

### 4.1 必须指标

- `Required Recall@K`：所有必需工具是否进入 Top K。
- `Tool Recall@K`：必需工具逐项召回率。
- `Precision@K`：命中结果中相关工具比例。
- `Forbidden Exposure Rate`：明确不应披露或未授权工具的比例。
- `No-match Accuracy`：确实无工具时是否避免乱召回。
- `Schema Token Reduction`：相对全量披露减少的 Tool Schema Token。
- `Search Latency`：P50/P95。

### 4.2 初始建议门槛

以下是试点门槛，不是永久承诺：

- 标注集 `Required Recall@5 >= 95%`。
- 未授权工具暴露率必须为 0。
- 高风险写入/删除工具的错误自动提升率为 0；只有模型主动搜索且授权允许时才可披露。
- 大型 MCP 请求 Tool Schema Token 相对全量降低至少 70%。
- 端到端任务成功率不得出现显著回退。
- Search P95 根据部署环境实测确定，不在设计阶段拍固定毫秒数。

False Negative 的代价通常高于多给 1～3 个候选，因此调参先保证 Recall，再压 Precision。

## 5. 端到端指标

使用 DeepSeek 和至少一种私有 OpenAI-compatible 模型运行真实 Agent：

- 首轮是否直接命中所需工具。
- 模型调用 `search_tools` 的次数。
- 从用户请求到第一次业务工具调用的模型回合数。
- 完成任务的总模型请求数。
- Provider 实际 input/output Token。
- 预算估算中的 Tool Schema Token。
- 工具参数重试次数。
- 误选工具、越权尝试和不存在工具调用。
- SSE、历史、Checkpoint 和上下文压缩是否保持配对。

不能只比较第一次请求 Token。检索若增加两次模型往返，总 Token 和延迟可能反而更高。

## 6. 专项用例

### 6.1 附件条件过滤

- 无附件：附件包三个工具均不可见。
- 当前请求上传附件：三个工具可见。
- 历史存在合法附件：后续 Run 三个工具可见。
- Checkpoint 只有附件摘要但原始历史无合法引用：不可见。
- 跨用户、跨会话 `file_id`：不能因检索或历史摘要变为可用。

### 6.2 MCP 语义检索

- “查询预算数据”命中 query，不自动命中 save。
- “把预算值写入工作版”命中 save，并包含必要定位工具。
- “找财务模型”命中平台元素搜索。
- “列出维度和成员”命中 dimensions + members。
- 使用中文别名、英文协议词和中英混合均可召回。
- 工具描述故意含共同领域词时，动作语义仍能区分。

### 6.3 渐进披露

- 首轮自动预选命中后无需先调用 `search_tools`。
- 自动预选不足时，模型搜索并在下一请求看到真实 Tool Schema。
- 同一 Run 中已发现工具保持可见。
- 新用户轮次不会自动继承上一 Run 的全部发现结果。
- 模型猜测未提升工具名时执行被拒绝。
- MCP 目录更新后旧 Catalog Hash 不生效。

### 6.4 上下文和历史

- Tool Search Call/Result 与业务 Tool Call/Result 历史均可恢复。
- 上下文压缩不拆散搜索和工具配对。
- Checkpoint 不把旧 Run 的全部发现工具变成新 Run 授权。
- Tool Schema Token 估算使用本轮最终披露集合。

## 7. 灰度策略

1. 先以 Shadow 模式运行 Retriever，只记录建议工具，不改变实际全量披露。
2. 对比真实业务调用与 Retriever Top K，修正元数据和召回。
3. 在开发/测试环境对少量 Agent 启用“自动预选 + 搜索”。
4. 观察任务成功、Token、额外回合和检索失败。
5. 达到门槛后按 MCP 包或 Agent 灰度，不直接全局打开。
6. 保留快速回退到全量披露的运维开关；开关应是发布/全局配置，不让业务用户理解算法细节。

## 8. 测试产物和清理

- 离线评估数据集属于可维护测试资产，应脱敏后纳入仓库。
- 临时 Embedding 模型、索引、向量和日志标记 `TMP to delete`，不提交。
- 测试不能把 Test 环境 Token、Cookie、Header 或完整业务 Schema 写入报告。
- 浏览器验收后关闭 Browser/Context 并清理自动化进程。
