# models.dev 全量接入与过滤口径

> 统计日期：2026-07-29
> 数据源：`https://models.dev/catalog.json`
> 说明：models.dev 会持续更新，本文数字用于说明当前接入比例和过滤链路，后续刷新结果可能变化。

## 1. 结论

DeepAgent 请求 models.dev 完整组合目录，不在 HTTP/API 层传递 Lab、Provider、Model 或数量过滤条件。
当前上游不是约 5,000 个独立标准模型，而是：

```text
标准模型 Models             279 个
Provider                    173 个
Provider × Model 组合       5,810 条
```

DeepAgent 先下载约 3.50 MB 的完整 `catalog.json`，再在进程内按 Provider 白名单、废弃状态、
文本输入输出能力和 Token 窗口完整性逐层过滤，最终得到 165 条 `model_catalog` 记录。

```text
GET models.dev/catalog.json（完整目录，约 3.50 MB）
│
├─ models：279 个标准模型
└─ providers：173 个 Provider，共 5,810 个 Provider Model
       │
       ▼
Provider 白名单过滤
5,810 → 198
       │
       ▼
过滤 deprecated
198 → 182
       │
       ▼
过滤非文本聊天模型
182 → 168
       │
       ▼
过滤无有效 Token 窗口的模型
168 → 165
       │
       ▼
完整校验
       │
       ▼
单事务替换 model_catalog
最终 165 行
```

## 2. 上游数据结构

`catalog.json` 顶层包含：

```json
{
  "models": {},
  "providers": {}
}
```

### 2.1 Models

`models` 保存与接入渠道无关的标准模型，键通常为：

```text
<lab_id>/<model_id>
```

例如：

```text
anthropic/claude-sonnet-4-5
openai/gpt-5
google/gemini-2.5-pro
```

Lab 不是 `catalog.json` 中独立的顶层对象。DeepAgent 从标准模型 ID 的 `/` 前缀取得 `lab_id`，
并可选读取 `https://models.dev/labs` 补充 Lab 名称和描述。Labs 页面不可用不会阻断刷新。

标准模型提供名称、系列、描述、上下文窗口、最大输出、模态、能力、发布时间、知识截止时间和
Benchmark 等通用属性。

### 2.2 Providers

每个 Provider 下嵌套它实际提供的模型：

```json
{
  "providers": {
    "anthropic": {
      "id": "anthropic",
      "name": "Anthropic",
      "models": {
        "claude-sonnet-4-5": {
          "id": "claude-sonnet-4-5"
        }
      }
    }
  }
}
```

Provider Model 可能重复声明或覆盖标准模型属性，例如实际模型编码、上下文、最大输出、能力、
模态和 API 信息。因此最终数据粒度不是一个标准模型，而是：

```text
一个标准 Model 通过一个 Provider 提供的可调用组合
```

## 3. 完整过滤链路

### 3.1 获取完整目录

必需请求：

```http
GET https://models.dev/catalog.json
```

请求不携带筛选参数。响应必须是 JSON 对象，并同时包含 `models` 和 `providers` 对象；否则本次
刷新失败，旧数据库目录保持不变。

可选请求：

```http
GET https://models.dev/labs
```

只用于增强 Lab 名称和描述，不参与核心模型关系判断。

### 3.2 建立标准模型索引

DeepAgent 先对 279 个标准模型按 `canonical_model_id` 建立索引。这一步不做 Lab 白名单过滤，
索引用于为后续 Provider Model 补充或校准：

- 标准模型名称和 Lab 归属。
- 上下文窗口和最大输出。
- 输入、输出模态。
- 推理、工具调用、结构化输出等能力。
- 发布时间和扩展元数据。

### 3.3 Provider 白名单

173 个上游 Provider 中，首版只保留 9 个代码白名单渠道：

| Provider | 白名单过滤后组合数 |
| --- | ---: |
| Alibaba | 51 |
| Anthropic | 15 |
| DeepSeek | 4 |
| Google | 41 |
| MiniMax | 7 |
| Moonshot AI | 10 |
| OpenAI | 47 |
| xAI | 10 |
| Zhipu AI | 13 |
| 合计 | **198** |

这一层完成：

```text
5,810 → 198
```

共过滤 5,612 条，约占上游组合的 96.6%。Bedrock、Vertex、Azure、OpenRouter、各类 Gateway、
Coding Plan、Token Plan 和长尾聚合渠道均不进入首版目录。

### 3.4 匹配标准 Model

对保留的 Provider Model，根据上游 ID 和 Provider 模型编码匹配标准 Model。匹配后深度合并：

```text
最终有效属性 = 标准 Model 属性 + Provider Model 覆盖属性
```

Provider Model 对上下文、输出、模态或能力的明确声明优先于标准模型默认值；同时保留标准值，
用于区分“模型通用属性”和“渠道实际生效属性”。

### 3.5 过滤废弃模型

以下模型跳过：

```json
{
  "status": "deprecated"
}
```

当前统计：

```text
198 → 182
过滤 16 条
```

### 3.6 过滤非文本聊天模型

必须同时满足：

```text
input modalities 包含 text
output modalities 包含 text
```

因此纯图片生成、纯 TTS、只接受音频或只输出音频/图片的产品不会进入聊天模型目录。支持
`text + image + pdf` 输入并输出文本的多模态模型仍会保留。

当前统计：

```text
182 → 168
过滤 14 条
```

### 3.7 Token 窗口校验

标准模型和 Provider 生效值都必须能得到正整数：

```text
context_window_tokens
max_output_tokens
```

Provider 未声明时继承标准模型值。缺失、为零或非正整数的候选模型跳过。

当前统计：

```text
168 → 165
过滤 3 条
```

此外还校验：

```text
max_output_tokens <= context_window_tokens
```

本次数据中没有出现输出上限超过上下文或复合主键重复。此类结构性异常会阻断整次刷新，而不是
悄悄跳过，以避免用不完整目录替换旧数据。

### 3.8 转换运行格式

目录为模型配置生成 DeepAgent/Pydantic AI 可使用的调用格式：

| models.dev Provider | DeepAgent 运行格式 |
| --- | --- |
| OpenAI | `openai:<model>` |
| Anthropic | `anthropic:<model>` |
| Google | `google:<model>` |
| DeepSeek | `deepseek:<model>` |
| Alibaba、Moonshot、Zhipu、xAI | `openai:<model>` + Provider Base URL |
| MiniMax | `anthropic:<model>` + Provider Base URL |

这一步只是协议、模型编码和默认 Base URL 映射，不会联网试调模型，不会检查 API Key，也不代表
Provider 的账号或网络当前可用。

## 4. 当前最终数据

最终结果：

```text
model_catalog 行数        165
标准模型数                165
Provider 数               9
Lab 数                    9
```

| Provider | 最终模型数 |
| --- | ---: |
| Alibaba | 50 |
| Anthropic | 13 |
| DeepSeek | 4 |
| Google | 29 |
| MiniMax | 7 |
| Moonshot AI | 10 |
| OpenAI | 33 |
| xAI | 6 |
| Zhipu AI | 13 |
| 合计 | **165** |

当前各 Lab 的模型数与同名 Provider 完全相同，说明白名单过滤后基本只保留了九个 Lab 的原厂渠道：

```text
165 个标准模型
=
165 个 Provider × Model 组合
```

平均每个标准模型只有一个 Provider。因此首版应理解为“常用原厂/直连模型目录”；数据结构仍保留
Lab、Model、Provider 三层关系，但当前尚未体现同一标准模型通过 Bedrock、Vertex、Azure 等多个
第三方渠道提供的场景。

## 5. 数据库替换与失败保护

全部候选完成标准化和校验后，才执行：

```text
开启一个数据库事务
→ DELETE model_catalog
→ 批量 INSERT 165 条
→ COMMIT
```

以下任一步失败都不会提交：

- 获取 `catalog.json` 失败。
- JSON 结构非法。
- 标准化或完整性校验失败。
- 出现重复组合或异常 Token 上限。
- 数据库删除或批量写入失败。

因此不会出现“旧目录已经删除、新目录只写入一半”的状态。目录刷新也不会自动修改已有
`llm_models`；管理员需要显式选择目录模型或执行“从模型目录更新”，运行时唯一事实源仍然是
`llm_models`。
