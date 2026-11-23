# 本地运行与 API 调试

**仅有源码且任务明确允许启动/修改本地服务时使用。**远程 OP 配置只需[连接与授权](../../../01-connect-and-permissions.md)，不必安装后端开发环境。

## 核对现状

在用户给定的仓库根检查 Git 状态、分支、HEAD、README、AGENTS，确认现有 Python 环境、监听进程、数据库和附件目录。已有8000/8010时先检查 `/health`、`/info` 和 `/openapi.json`，不要重复启动，也不要杀无关进程。

本包带有[解释器检查脚本](../../../../scripts/inspect_runtime.py)；用项目自己的解释器执行，输出包版本和模块来源，不输出凭据。不要把检查本机环境与远端部署检查混淆。

## 需要启动时

由用户/部署负责人提供仓库外的私有 Profile，不能假定某个维护者的路径或共享测试库已存在。通常在已具备依赖的仓库根执行：

```bash
PYTHONPATH=src .venv/bin/python -m deep_agent.combined_runtime \
  --local-config '<已授权私有Profile路径>' --api-port 8000
```

此入口先启动API、健康后再起8010渠道网关，不额外起第二个Gateway。重启等旧监督进程完全退出；不得仅等待端口空闲。保持现有数据库、附件和会话，破坏性测试另用明确隔离的临时库。部署入口是 `deep_agent.main:app`，本地身份适配不能暴露到公网。

普通启动会执行Alembic升级，启动前核对当前Head与数据库；数据库变更独立确认。只读共享资料可用 `deep_agent.local_dev --read-only-preview`，但此模式不能验收聊天、安装、连接测试或删除，不应绕开它发写请求。

## 接口核验

- `/health` / `/info` / `/openapi.json`：只证明对应入口可读。
- `/api/v1/admin/access`：验证当前身份是否具备管理权限。
- 读取Agent/模型配置：验证配置接口和数据库。
- 一次隔离Chat及History回读：验证Provider、SSE和持久化。
- 附件实际上传/下载：验证文件链路；平台File ID不等于匿名下载授权。

用户已启动的回环local-dev可以已具备本地身份，不必另外配置Token；以实际 `/admin/access` 为准。生产/OP请求使用平台授权流程，不能伪造 `user` Header。

Chat 的 Body、附件、`context_refs` 和 Deferred 字段以目标OpenAPI为准；参考[客户端调用契约](../../../05-chat-and-session.md)。不要同传 `context_refs` 和 `deferred_tool_results`。

采集SSE原文应放私有目录，标记 `TMP to delete`，不输出完整业务内容或请求头。使用[汇总脚本](../../../../scripts/summarize_sse.py)只汇总事件；前端可用[结构化事件汇总](../../../../scripts/parse_deep_agent_sse.py)。通知帧数量不等于真实工具执行次数。

需要手动压缩时，使用目标OpenAPI中的 `/api/v1/sessions/{session_id}/compact`；不通过发新提问模拟压缩，不改写原消息。详见[上下文](context-compaction-and-references.md)。
