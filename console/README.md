# DeepAgent 一体化控制台

DeepAgent 自带一个无需构建的静态控制台，正式入口为：

```text
http://127.0.0.1:8000/console/
```

根路径 `/` 会跳转到测试工作台。旧的 `/settings.html`、`/models.html` 和 `/admin/` 页面已经移除，相关能力统一进入 Console。

## 功能

- 测试工作台：Agent、会话、SSE 对话、Thinking、工具调用、HITL、附件和模型切换。
- 测试 Header：只允许编辑 App；Space 自动推导、Language 固定为 `zh-cn`，用户和鉴权只读。
- 配置中心：Agent、Provider与模型、Skill、内置工具包、MCP 服务、UI 工具包与工具、渠道、全局配置和模型目录。
- MCP 服务：URL MCP 可创建、编辑和删除；内建 MCP 由程序管理，控制台不提供删除入口。
- 诊断中心：管理员查询当前实例 API／企微网关诊断日志，按时间、来源、等级、关键词和 Session／Run 筛选，展开结构化记录并复制标识；不提供下载、探测或实时订阅。
- 接口文档：控制台左侧导航可直接打开 `/docs`。

工作台在会话栏顶部用下拉框选择 Agent，右侧保留刷新操作，不提供独立 Agent 列表模式。
对话标题栏右侧集中提供测试 Header 和会话详情入口。右侧详情使用“概览 / 当前 / 上下文”页签，
存在渠道关联或会话来源时增加“渠道”；轨迹复用同一详情栏。上下文预算读取后端
`context-status`，当前回合用量来自 `final.usage`，Console 不实现独立 Token 估算算法。

## 代码结构

```text
console/
├── index.html               # 唯一产品页面
├── workspace-template.html
├── configuration-template.html
├── catalog-template.html
├── api.js                   # 同源 API 与服务前缀解析
├── chat.js                  # SSE、会话、附件和 HITL 编排
├── workspace.js
├── configuration.js
├── configuration/           # 配置中心脚本、样式与图标
├── assets/                  # 产品静态资源
└── vendor/                  # marked、DOMPurify、highlight.js、Lucide 等
```

Console 使用原生 HTML、CSS 和 JavaScript ES Modules，不需要 npm 构建，随同一份后端源码目录发布。
后端将 `console/` 挂载到 `/console/`，将 `console/vendor/` 挂载到 `/vendor/`，浏览器地址保持不变。
`console/` 是唯一前端源码目录，旧 `frontend/` 已移除。前端测试仍放在 `tests/frontend/console/`，
它是测试分类目录，不是产品源码的兼容入口。

交互原型统一保存在 [`Console UI 设计课题`](../docs/design/17-console-ui/README.md)，包括
统一侧边栏和 0825 单层导航方案；`console/` 不放原型，避免随正式静态路由对外发布。

压缩过程由后端状态和 SSE 驱动，自动与主动压缩复用消息区中的独立过程条；普通历史聊天默认隐藏
完成标记，轨迹保留实际发生位置。前端只展示后端预算和压缩结果，不实现压缩策略或编辑摘要正文。
具体交互与模块边界以 [`AGENTS.md`](AGENTS.md) 为准。

停止生成使用 `POST /api/v1/sessions/{session_id}/cancel`。接口接受取消后，Console 继续读取
当前 SSE，直到收到服务端真实 `cancelled`。当前页面保留已经流出的回答和工具过程，直接标记
“已停止”，不立即重载整段历史；重新打开会话时再通过持久化的
`assistant.status=interrupted` 恢复相同状态。停止失败不会 Abort 流或在本地伪造成功；页面卸载
和网络清理仍可单独关闭本地请求。

## 运行

所有命令从仓库根目录执行；日常本地开发复用稳定 SQLite Profile：

```bash
PYTHONPATH=src python3.12 -m deep_agent.local_dev --config ~/.config/deep-agent/sqlite.env --port 8000
```

本地连接 Test 配置时使用项目的安全启动入口：

```bash
PYTHONPATH=src python3.12 -m deep_agent.local_dev --config ~/.config/deep-agent/test.env --port 8000
```

页面固定请求当前同源后端。单独启动静态文件服务器只适合查看静态资源，不能替代完整 Console 联调。
本地与线上都使用仓库根 `PYTHONPATH=src` 和同一份 `console/` 资源，不依赖旧目录或符号链接。
依赖安装和发布方式见 [`项目结构`](../docs/guides/project-structure.md) 与 [`部署目录`](../deploy/README.md)。

常用地址：

- Console：`http://127.0.0.1:8000/console/`
- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`

## 测试

```bash
rg --files console -g '*.js' -g '!console/vendor/**' -0 | xargs -0 -n1 node --check
node --test tests/frontend/console/*.test.mjs
PYTHONPATH=src python3.12 -m unittest tests.unit.test_console_routes -v
```

完整 SSE 协议见 [`docs/guides/sse-protocol.md`](../docs/guides/sse-protocol.md)。UI 工具见 [`docs/guides/ui-tools-external.md`](../docs/guides/ui-tools-external.md)。
