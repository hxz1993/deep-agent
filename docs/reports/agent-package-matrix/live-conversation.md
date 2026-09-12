# 安装包真实模型与本地 Skill 验收

结果：通过。2026-09-13（Asia/Shanghai），隔离 API `http://127.0.0.1:8002`。

通过正式接口创建临时模型配置、上传安装合成 Agent ZIP，随后完成一轮真实 `/api/v1/chat` 调用。模型为公开 DeepSeek 的 `deepseek-flash`，总耗时 4.85 秒，无第二轮重试。源 `data/deep-agent-local.db` 仅通过 SQLite `mode=ro` 读取模型配置；没有读取原 Agent 提示词或修改原库。

## 实际工具证据

Agent 仅关联 `builtin:sandbox` 和上传 Skill `package-live-arithmetic`，没有 MCP 能力。Skill ZIP 完整包含 `SKILL.md`、`scripts/calculate.py`、`references/check.md` 和 `assets/values.json`。核对持久化会话事件，工具调用顺序如下：

1. `load_capability`：实际加载 `package-live-arithmetic` 正文与脚本说明。
2. `execute_python`：运行数组加权求和脚本，返回 `status=ok`、`exit_code=0`；本地 `subprocess-dev` 沙箱执行耗时 798 ms。

```python
values = [3, 5, 8, 13, 21]
weighted_total = sum((i + 1) * value for i, value in enumerate(values))
```

独立复算：`1×3 + 2×5 + 3×8 + 4×13 + 5×21 = 194`。

真实工具 stdout：

```text
{'values': [3, 5, 8, 13, 21], 'weighted_total': 194}
```

模型最终回答与工具结构化结果中的 `weighted_total=194` 一致。

## 保留的验收入口

- Agent：`8068c1a3-4198-4b4c-8c84-c2861ff0b8ba`，名称“安装包真实模型与Skill验收”。
- 会话：`67a3db4b538e4ff6afd7e32b412e5abd`。
- [Agent 配置接口](http://127.0.0.1:8002/api/v1/agents/8068c1a3-4198-4b4c-8c84-c2861ff0b8ba)
- [会话事件接口](http://127.0.0.1:8002/api/v1/sessions/67a3db4b538e4ff6afd7e32b412e5abd/events?agent_id=8068c1a3-4198-4b4c-8c84-c2861ff0b8ba&limit=500)

验收后已通过正常 API 将该 Agent 的模型置空、删除临时模型和 Provider，并再次 GET 确认清理完成。合成 Agent、上传 Skill、会话与工具结果保留在隔离环境；原本地数据库未改动。凭据未写入脚本、报告或安装包。

事件定位：

| 序号 | 工具 | 类型 | Event ID |
| --- | --- | --- | --- |
| 6 | load_capability | tool-call | `794e5525da0b5a4cb223a4ca83f18b3a` |
| 7 | load_capability | tool-return | `bba7428242e55c4590e733f0516c400b` |
| 9 | execute_python | tool-call | `8635014366355db7ab4364357e0e4e7a` |
| 10 | execute_python | tool-return | `0278c8db4aff5e0a8d09c3b20152e3ed` |
