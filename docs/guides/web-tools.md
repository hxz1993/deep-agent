# 网络搜索与网页抓取

更新：2026-11-22。本地实现说明，不代表 Alpha 已部署。

## 配置入口

在 Console → 全局配置 → 常规 → 网络搜索中，先选择搜索服务，再选择网页抓取方式。Agent 仍需显式关联 `builtin:web`。

| 搜索服务 | 凭据 | 网页抓取 |
| --- | --- | --- |
| DeepSeek | 沿用已配置的同类型供应商，或单独 API Key | 跟随搜索／系统内置 |
| Tavily、Exa、You.com | 单独 API Key | 系统内置 |
| DuckDuckGo | 无需 Key | 系统内置 |
| 暂不配置 | 无 | 系统内置；搜索调用提示未配置 |

默认使用系统内置。跟随搜索只保存选择，不复制供应商或 Key；实际调用解析同一供应商最新的地址和凭据。切换到不支持跟随的搜索服务时恢复系统内置。界面只有搜索连接测试，不单独测试 Fetch；搜索成功不证明任意目标网页都可抓取。

## 工具行为

- `web_search` 按所选供应商装配参数，并将原生 JSON 或 DDGS 结果列表交给模型。DeepAgent 默认请求 5 条，模型可指定条数。来源和耗时作为轨迹元数据记录。
- `web_fetch` 跟随 DeepSeek 时只传 `url`，直接返回原生 `title` 和 `content` 等字段。系统内置支持 `url` 和可选的 `max_content_length`，模型可以只取一段预览。默认保留全文，下载上限使用 SDK 默认的 50 MiB。系统内置将 HTML 转为保留来源链接的 Markdown，JSON 正文保留原格式。超大工具结果沿用外置和 `read_tool_result` 续读。
- `view_image(items)`：已合并原网络图片查看，归属“文件”运行工具。支持图片及PDF选页、附件/Skill/已授权公网来源；只提供给当前视觉主模型，不进入PTC。正文图片链接不等于看过图片。详见[图像查看](image-viewing.md)。

| DeepSeek 参数 | 用途 |
| --- | --- |
| `query` | 必填，1～128 个 Unicode 字符。可用 DeepSeek 支持的 `site:` 等语法。 |
| `count` | 模型可指定 1～50 条，DeepAgent 默认请求 5 条。 |
| `need_content` | 模型可请求随结果提供可用的网页正文，默认关闭。 |
| `ori_question` | 模型可填写 1～128 字符的原始问题。省略时优先使用本轮 128 字符以内的用户文本。更长的问题由模型提炼与本次搜索相关的部分。 |
| `localization` | 可选 IP 地址，供 DeepSeek 判断搜索应偏向哪个地区。例如同一句「附近的博物馆」，地区线索不同会影响排序。普通搜索省略此参数，DeepAgent 不会自动填入访问者或服务器 IP。 |

参见 [DeepSeek Search 接口](https://api-docs.deepseek.com/api/search/)。

| 供应商 | 模型可选择的原生参数示例 |
| --- | --- |
| [Tavily](https://docs.tavily.com/api-reference/endpoint/search) | `max_results`、`search_depth`、`include_raw_content`、域名及日期过滤 |
| [Exa](https://exa.ai/docs/reference/search) | `numResults`、`type`、`contents`、`includeDomains` |
| [You.com](https://you.com/docs/api-reference/search/v1-search) | `count`、`extraction`、`freshness`、语言和域名过滤 |
| [DuckDuckGo](https://github.com/deedy5/ddgs) | DDGS 的 `max_results`、`region`、`safesearch`、`timelimit`、`page` |

供应商失败会保留脱敏错误，不自动换供应商或退回系统内置；瞬时网络、429等错误仅在现有有界时间和次数内重试。带凭据、私网、回环及元数据URL不能作为抓取目标。云服务内部的重定向/DNS无法由本地核验，不声称与本地固定IP下载完全等价。

系统内置的PDF和图片路径继续保留；跟随模式下，明确的PDF／图片文件扩展名仍走原本地二进制路径。无扩展名图片探测不切到云Fetch。云Fetch不能抓取的页面应按失败处理，不能伪造正文。

## API与历史兼容

仍使用 `GET / PUT /global-settings/web-search` 和 `POST /global-settings/web-search/test`，路径前缀为 `/api/v1`；结构版本仍为1，无数据库迁移。

```json
{
  "version": 1,
  "value": {
    "mode": "provider",
    "provider": "deepseek",
    "credential_source": "llm_provider",
    "llm_provider_id": "<已配置的供应商ID>",
    "fetch_mode": "follow_search"
  }
}
```

`fetch_mode` 为 `system` 或 `follow_search`。旧记录未配置时按 `system` 读取且不主动写库；旧客户端保存时省略该字段，会保留已保存的选择。第三方搜索或未配置搜索时统一为系统内置。

历史 `model` 继续兼容接收，但忽略且不再存储；响应的兼容字段为 `null`。DeepSeek 测试和实际调用均迁移到独立Search端点，自定义聊天网关不一定支持新路径，需要自行验证。官方基址和 `/v1` 基址可正常拼接，自定义网关保留原域名及路径前缀，不绕过网关直连官方。

旧 Kimi 搜索配置读取时显示为不可用，网页抓取使用系统内置。重新选择搜索供应商后更新原配置行。Kimi 模型连接与网络搜索配置独立。

结果事件根据真实 `web_fetch.mode` 标注本地或供应商执行，供应商正文可用性以目标网页的实际响应为准。参见 [DeepSeek Fetch](https://api-docs.deepseek.com/api/fetch/)。

旧版`web_image_view`的事件、URL引用和预览继续可读；新请求仅披露`view_image`。Web能力包现在仅包含`web_search`与`web_fetch`，公网查看仍需此包授权，不因工具迁移扩大访问权限。
