# 前端本地代理与鉴权联调

## 目录

- [两类联调](#两类联调)
- [代理 Header](#代理-header)
- [本机 deep-agent 8000](#本机-deep-agent-8000)
- [验证顺序](#验证顺序)
- [安全与清理](#安全与清理)

## 两类联调

### 前端本地，后端走 Test/Alpha

- 本地页面运行在仓库端口，例如 `element-python3` 的 3008。
- `/deepfos-server/**` 代理到目标环境。
- token/cookie 仅由 dev server 环境变量注入；页面 storage 和源码中不保存。
- 页面 path/query 与环境页面保持一致，只替换 origin。

### 前端本地，deep-agent 走本机 8000

- deep-agent 专用前缀代理到 `http://127.0.0.1:8000`。
- 其他 `/deepfos-server/**` 仍代理 Test，以取得元素、财务模型、文件等平台数据。
- 专用规则必须位于通用 `/deepfos-server` 规则之前。

## 代理 Header

前端页面显式构造：

```text
app, space, language
```

dev proxy 可从进程环境注入：

```text
DEEPFOS_DEV_TOKEN
DEEPFOS_DEV_TOKEN_COOKIE
DEEPFOS_DEV_APP
DEEPFOS_DEV_SPACE
DEEPFOS_DEV_LANGUAGE
```

优先从页面 `sign` 解码最新 `appId/space` 覆盖静态 fallback。不要打印 token 原值。

生产/Test 网关如何生成 `user` 属于后端与网关契约；前端只验证最终请求身份是否正确。不要在前端 Body、Query、源码或通用 dev proxy 中补造 `user`。

直连本机 8000 不经过平台网关。当前后端本地联调应由后端负责人通过 `python -m deep_agent.local_dev` 和仓库外私有 profile 提供可信身份；前端专用代理只负责同源路径重写，不再注入 `DEEPFOS_DEV_USER`、token 或 cookie。

## 本机 deep-agent 8000

目标重写：

```text
/deepfos-server/deep-agent-server1-0/api/v1/chat/stream
→ http://127.0.0.1:8000/api/v1/chat/stream
```

代理核心配置形态：

```js
{
  context: ["/deepfos-server/deep-agent-server1-0"],
  target: "http://127.0.0.1:8000",
  changeOrigin: true,
  pathRewrite: {
    "^/deepfos-server/deep-agent-server1-0": "",
  },
}
```

实际字段名按仓库的 Webpack/Vite/Rspack 封装调整。不要为了联调覆盖其他服务的通用代理。

Test HTTPS 页面不能直接 fetch 本机 HTTP：浏览器会受 mixed content、CORS 和 Cookie 域限制。跑通方式是打开本地前端 origin，让浏览器只请求同源本地路径，再由本地代理分别转发 Test 和 8000。

后端本地身份 profile 属于后端任务，由后端负责人提供实际路径和内容；本地 trusted identity 与远端平台 token 是不同条件，按目标 Profile 需要配置。前端任务不读取、不修改、不复制这些值；只确认 8000 已由后端负责人以 `deep_agent.local_dev` 启动。不要用普通部署启动命令替代本地身份适配器，否则直连请求缺少可信用户 Header。

## 只读预览与可写联调

后端 `local_dev --read-only-preview` 可以连接已处于当前 Revision 的数据库，跳过迁移、Skill 对账、事件写入器、沙箱和 Gateway 初始化，并施加数据库只读保护。它只允许 GET/HEAD/OPTIONS，写请求返回 403 和 `X-Deep-Agent-Read-Only: 1`；不支持 WebSocket。

此入口适合配置和诊断页面验收，不是聊天后端。启动方式由后端任务维护；前端不要为了完成聊天冒烟将已有 Test 只读进程替换为可写进程。需要聊天或配置写入时先确认既有授权与可写的隔离环境。普通可写 Profile 启动与追加只读标记不是同一种安全边界。

## 验证顺序

1. 确认 8000 `/docs` 或 `/openapi.json` 为 200。
2. 启动前端，确认真实页面而非仅首页 HTML 为 200。
3. 通过前端 origin 请求代理后的 deep-agent `/docs` 或一个只读接口。
4. 检查 Network：请求 URL 仍是同源 `/deepfos-server/...`，没有浏览器直连 8000。
5. 仅在可写环境且本次涉及聊天时，发一条真实聊天，确认 SSE 首个 `session` 和最终 `final`；只读预览验证目标 GET 页面和拒写提示即可。
6. 如果验证用户隔离，只确认后端识别到预期用户且 owner-only 行为正确；不要输出 user/token/cookie 原值。

只验证 3008 监听或首页 HTML 200 不代表鉴权、平台 API 和 Agent 代理可用。

## 安全与清理

- token 文件使用 `*.local` 并确保被 gitignore；不要提交。
- 临时网关、SSE、请求体和输出文件命名含 `TMP to delete`。
- 页面 localStorage/sessionStorage 不保存 token。
- 测试结束关闭浏览器，停止前端和临时代理，检查 3008/临时端口不再监听。
- 用户明确要求保留服务时才保留，并说明保留的 PID/端口；不要停止对方任务维护的 8000。
