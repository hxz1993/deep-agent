# 企业微信 Channel 部署与使用

DeepAgent 0.7.0 支持企微智能机器人 WebSocket 长连接。API 与 Gateway 使用同一代码和镜像，但
Gateway 是可选独立进程：不使用企微的环境无需启动，也不会建立外部连接。

## 前置条件

- 数据库已由 API 升级到 Alembic `002`。
- 已安装 `requirements.txt` 中固定的 `wecom-aibot-python-sdk==1.0.2`。
- API 与 Gateway 可以访问同一数据库，Gateway 可以访问 DeepAgent API 和企微公网地址。
- 生产环境生成高熵内部 Token，并只通过 Secret/私有环境变量注入。

## 启动

API：

```bash
DEEP_AGENT_CHANNEL_GATEWAY_URL=http://channel-gateway:8010 \
DEEP_AGENT_CHANNEL_INTERNAL_TOKEN='<private-token>' \
uvicorn deep_agent.main:app --host 0.0.0.0 --port 8000
```

Gateway：

```bash
DEEP_AGENT_CHANNEL_API_URL=http://deep-agent:8000 \
DEEP_AGENT_CHANNEL_INTERNAL_TOKEN='<same-private-token>' \
python -m deep_agent.channel_gateway --host 0.0.0.0 --port 8010
```

Gateway 不运行迁移；Revision 不是 `002` 会失败关闭。0.7.0 一个环境只能启动一个 Gateway 副本，
否则相同 Bot 的长连接会互相顶替。

可选私有运行配置：

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `DEEP_AGENT_CHANNEL_GATEWAY_URL` | 空 | API 访问 Gateway；空表示未部署 |
| `DEEP_AGENT_CHANNEL_API_URL` | `http://127.0.0.1:8000` | Gateway 访问 API |
| `DEEP_AGENT_CHANNEL_INTERNAL_TOKEN` | 无 | 双向内部鉴权，API与Gateway必须一致 |
| `DEEP_AGENT_CHANNEL_POLL_INTERVAL` | `3` | 配置轮询秒数 |
| `DEEP_AGENT_CHANNEL_STREAM_INTERVAL` | `0.35` | 企微流式更新最小间隔 |
| `DEEP_AGENT_CHANNEL_TEST_TIMEOUT` | `10` | 连接测试等待秒数 |
| `DEEP_AGENT_CHANNEL_PLATFORM_AUTHORIZATION` | 空 | Gateway调用平台时的私有Authorization |
| `DEEP_AGENT_CHANNEL_PLATFORM_COOKIE` | 空 | Gateway调用平台时的私有Cookie |

Token、Cookie、Bot Secret 不得写入源码、日志、文档或测试夹具。

## Console 配置

在“配置中心 → 渠道”创建企微账号：填写渠道名称，选择 Agent，并填写 Bot ID、Secret、会话用户。
“会话用户”直接输入平台 User ID，对应 `session_user_id`；只有企业管理员可以配置。
它决定 DeepAgent Session 的归属，不负责签发平台权限或 Token。平台凭据仍由私有运行环境提供，
应与实际业务访问账号匹配；渠道不配置 App、Space 或 Language。
Secret 按0.7.0产品约定明文入库，
但管理接口只返回是否已配置，不返回原值；编辑时留空即保持原配置。配置备份仍必须按敏感文件保护。

渠道是否启用只在列表 Switch 中维护，不放在新建或编辑弹框中。新建渠道默认关闭，管理员确认配置后
再从列表启用。

状态含义：

- `未部署`：API 没有配置 Gateway URL，保存配置不受影响。
- `运行时不可达`：已配置 URL，但 Gateway 无响应。
- `已关闭`：账号未启用，不建立长连接。
- `连接中 / 已连接 / 重连中 / 连接冲突 / 连接失败`：Gateway 的实时状态。

连接测试对启用账号读取当前认证状态；未启用账号会临时连接后断开，可能顶掉同一 Bot 的其他连接。

## 会话和故障处理

- 私聊按 `account + userid` 复用 Session；群聊按 `account + chatid` 复用 Session。
- Account 切换 Agent 或会话用户后，下一条消息创建新 Session；旧历史及其用户归属保留。
- 删除 DeepAgent Session 后，下一条企微消息自动重建绑定。
- 重复 `msgid` 不会重复执行；Gateway 重启会把未完成 Run 标为 interrupted。只有明确在调用 Agent
  之前失败的记录允许重新认领；HTTP 已发出而结果未知时，即使没有拿到 Run ID 也不自动重跑。
- 企微不负责给模型补历史；上下文始终以 DeepAgent Event Store 为准。

Gateway 只向企微输出模型 Markdown 文本；thinking、工具参数、轨迹和内部错误不会外发。
