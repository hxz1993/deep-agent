# DeepAgent 一体化控制台

DeepAgent 自带一个无需构建的静态控制台，正式入口为：

```text
http://127.0.0.1:8000/console/
```

根路径 `/` 会跳转到测试工作台。旧的 `/settings.html`、`/models.html` 和 `/admin/` 页面已经移除，相关能力统一进入 Console。

## 功能

- 测试工作台：Agent、会话、SSE 对话、Thinking、工具调用、HITL、附件和模型切换。
- 测试上下文：只允许编辑 App；Space 自动推导、Language 固定为 `zh-cn`，用户和鉴权只读。
- 配置中心：Agent、LLM 模型、MCP 服务、UI 工具、辅助模型和模型目录。
- MCP 服务：URL MCP 可创建、编辑和删除；内建 MCP 由程序管理，控制台不提供删除入口。
- 诊断中心：当前展示已有运行信息和后续诊断能力说明。
- 接口文档：控制台顶栏可直接打开 `/docs`。

## 代码结构

```text
frontend/
├── console/                 # 唯一产品页面
│   ├── index.html
│   ├── workspace-template.html
│   ├── configuration-template.html
│   ├── catalog-template.html
│   ├── chat.js
│   ├── workspace.js
│   ├── configuration.js
│   └── configuration/       # 配置中心脚本、样式与图标
└── vendor/                  # marked、DOMPurify、highlight.js、Lucide
```

Console 使用原生 HTML、CSS 和 JavaScript ES Modules，不需要 npm 构建。

## 运行

在仓库根目录启动后端：

```bash
python3.12 -m deep_agent.main
```

本地连接 Test 配置时使用项目的安全启动入口：

```bash
python3.12 -m deep_agent.local_dev --config ~/.config/deep-agent/test.env --port 8000
```

常用地址：

- Console：`http://127.0.0.1:8000/console/`
- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`

## 测试

```bash
find frontend/console -name '*.js' -print0 | xargs -0 -n1 node --check
node --test tests/frontend/console/*.test.mjs
python3.12 -m unittest tests.unit.test_console_routes -v
```

完整 SSE 协议见 [`docs/guides/sse-protocol.md`](../docs/guides/sse-protocol.md)。UI 工具见 [`docs/guides/ui-tools-external.md`](../docs/guides/ui-tools-external.md)。
