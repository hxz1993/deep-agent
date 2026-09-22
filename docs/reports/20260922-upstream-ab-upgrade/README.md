# DeepAgent v0.10.16：上游 A/B 适配与回归

2026-09-22。以 v0.10.15（2f51eb6）为基线，只实施上游评估的 A 组稳定升级和 B 组局部适配。

## 依赖基线

| 依赖 | 原版本 | 本次版本 |
|---|---|---|
| Pydantic AI Slim | 2.35.1 | 2.46.0 |
| Pydantic AI Harness | 0.26.0 | 0.32.0 |
| Monty | 0.0.21 | 0.0.23 |
| OpenAI SDK | 3.5.0 | 3.8.0 |

本地解算同时得到 Anthropic 1.7.0、Google GenAI 2.20.0、httpx2 2.7.0、aiohttp 3.14.3；`pip check` 无冲突。
主运行时使用仓库 `.venv/bin/python`；与原有 `.venv-sandbox` 分离。开发库仍为 `data/deep-agent-local.db`，数据库 Head `006`，没有新增迁移。

## 实际改动

| 类别 | 处理 | 兼容边界 |
|---|---|---|
| A：上游稳定修复 | 固定联合依赖版本，继承工具动态定义、并行配对、流式文本边界、Code Mode 规范化和取消清理 | 40 次请求预算、结果读取、MCP 检索和前端回传协议继续使用现有产品实现 |
| B：网页抓取 | 复用上游安全转换器、字符集解码；解码/HTML转换在线程执行，补齐无效 URL、深层 HTML 的可恢复错误 | 保留原图文位置、公开 URL、二进制返回及完整正文转存；不关闭 TLS/SSRF 校验 |
| B：回归中新发现的 ID 约束 | 新版 SDK 禁止 Toolset.id 含冒号；MCP 和 UI 内部 ID 的动态部分使用百分号编码 | 不改变 capability code、MCP 服务名、工具协议名，不重写已有历史 |
| 额外解析边界 | 旧 CPython 对重复未闭合标签可能反复扫描；仅对最后一个 `>` 后重复的标签起始返回可恢复错误 | 不猜测修改正文；病态输入放入有超时清理的独立进程回归 |
| 测试维护 | 时间戳契约测试改用现行有效搜索配置；日志大小按真实管道文本字节验证 | 不恢复旧 native 搜索，不放宽日志 8192 字节上限 |

未启用 C/D 类可选能力：提前执行、第二模型审查、AskUser、持久工作流、新沙箱等。Monty 1.0 beta 未引入。

## 验证记录

- 40 次及 1/2 次边界、并行工具、前端回传、压缩、取消、持久化、工具发现和结果读取纳入完整回归。
- 新增真实 Hybrid Code Mode 回归：Decimal/UUID/datetime 按 Schema 进入 Monty；累计交互上限跨代码片段生效，失败后保留已完成工具审计。
- WebFetch 使用真实回环 HTTP 字节响应验证乱码/非法编码、畸形 URL、深层 HTML、长正文、图片、PDF，以及线程执行。
- MCP/UI 专项 79 项通过；确认服务指令能进入真实 SDK Agent，UI Deferred 保留原协议工具名。
- Console Node 测试 660 项通过。
- 本地 SQLite 的 DeepSeek Flash、Kimi K3：真实调用 `get_current_time`，SSE 工具调用/结果配对、正常完成、历史读回通过，测试 Agent/会话随后清理。
- 使用与 8000 相同的开发代理配置，公网 Example Domain 与 Python 官网抓取成功。独立探针未加载该配置时 TLS 失败，旧版同样复现；没有修改校验规则来绕过。
- `/health`、`/info`、OpenAPI、Console、SQLite 配置读取与子进程沙箱健康检查通过。

完整 Python 回归共 1,943 项，1,890 项通过、53 项按环境条件跳过，耗时 353.4 秒，无失败。Console 660 项全部通过。

浏览器冒烟使用临时 DeepSeek Agent 实际调用 `web_fetch`：页面显示“获取网页内容 · 完成”，正常展示 Example Domain 标题和中文摘要；历史接口读回保留工具名及正文。页面无 Console error，临时 Agent、会话和自动化标签页已清理。8000 保留 SQLite 开发环境运行，`/info` 返回 v0.10.16。

边界：未配置隔离 MySQL/PostgreSQL 的数据库测试、macOS 不支持的 Linux 沙箱安全测试按既有条件跳过；不代表这些环境已经实测。未持有凭据的 Provider 只验证工厂、消息映射与模拟响应，不宣称全部供应商都完成真实网络验收。

## 后续升级注意

- 私有 WebFetch、PTC、媒体映射、代理钩子必须继续对照固定上游版本回归。
- 当前 MCP/FileData 工具未设置 `sequential=True`；若以后使用，需同步 PTC 目录说明：Harness 0.32 对该类工具生成同步函数，不能一律引导 `await`。
- 新依赖需要重新安装，不能只更新业务源码。部署继续使用原发布流程，数据库无需迁移。
