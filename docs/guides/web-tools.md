# 网络搜索与网页抓取

更新：2026-10-16。本地实现说明，不代表 Alpha 已部署。

## 配置入口

在 Console → 全局配置 → 常规 → 网络搜索中，先选择搜索服务，再选择网页抓取方式。Agent 仍需显式关联 `builtin:web`。

| 搜索服务 | 凭据 | 网页抓取 |
| --- | --- | --- |
| DeepSeek、Kimi | 沿用已配置的同类型供应商，或单独 API Key | 跟随搜索／系统内置 |
| Tavily、Exa、You.com | 单独 API Key | 系统内置 |
| DuckDuckGo | 无需 Key | 系统内置 |
| 暂不配置 | 无 | 系统内置；搜索调用提示未配置 |

默认使用系统内置。跟随搜索只保存选择，不复制供应商或 Key；实际调用解析同一供应商最新的地址和凭据。切换到不支持跟随的搜索服务时恢复系统内置。界面只有搜索连接测试，不单独测试 Fetch；搜索成功不证明任意目标网页都可抓取。

## 工具行为

- `web_search(query)`：直接调用检索服务，返回来源。DeepSeek 使用独立 `/v1/tools/search`，固定请求前5条，不使用聊天模型；搜索词为1～128个Unicode字符，过长会要求调整，不截断查询。有效空结果返回“未找到结果”。Kimi 保留独立 Search API。
- `web_fetch(url)`：系统内置直接获取公开页面；跟随模式调用 DeepSeek 或 Kimi 的 `/v1/tools/fetch`。两家统一返回请求URL、标题和Markdown正文，附来源、供应商及耗时。不提前截断正文，超大内容由现有工具结果外置与 `read_tool_result` 续读处理。
- `view_image(items)`：已合并原网络图片查看，归属“文件”运行工具。支持图片及PDF选页、附件/Skill/已授权公网来源；只提供给当前视觉主模型，不进入PTC。正文图片链接不等于看过图片。详见[图像查看](image-viewing.md)。

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

当前请求构建位于 `web/provider_api.py`，DeepSeek读取 `content`，Kimi读取 `markdown`；不得把 `summary` 当作网页原文。结果事件根据真实 `web_fetch.mode` 标注本地或供应商执行，调用开始时不猜测执行方式。

## 实测限制

2026-10-16 的同页实测中，Kimi保留图片Markdown占位，图片地址可被改写且替代文字未原样保留；DeepSeek没有返回该页的图片链接。两家首页抓取只包含局部正文，不能作为整站目录。Kimi对Python JSON文档返回 `403 security_risk`，而文档中心及其自身接口文档成功，需按目标站点分别判断。

详细来源：[DeepSeek Fetch](https://api-docs.deepseek.com/api/fetch/)、[Kimi Fetch](https://platform.kimi.com/docs/api/tools-fetch)。验收数据见[本轮报告](../design/21-web-tools/web-search-fetch-20261016-validation.md)。

旧版`web_image_view`的事件、URL引用和预览继续可读；新请求仅披露`view_image`。Web能力包现在仅包含`web_search`与`web_fetch`，公网查看仍需此包授权，不因工具迁移扩大访问权限。
