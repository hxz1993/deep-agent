# 工具检索评估与验收

## 1. Shadow 的定位

Shadow 是脱敏离线测试，不是生产功能。测试资产包含：用户问题、授权工具目录、必需工具、可选工具和
不应自动预选的高风险工具。评估器直接运行当前 `lexical_v2`，不连接数据库、MCP、Provider 或 Embedding，
不自动训练和修改参数。

## 2. 首版指标

- Required Recall@5：100%。
- Recall@3：不低于 90%。
- 未授权工具召回：0。
- 只读问题错误自动预选写入/删除工具：0。
- 24 工具目录首轮 Schema Token 相对全量下降至少 70%。

Token 下降必须和端到端任务成功、模型请求次数、首次业务工具延迟一起判断，不能只比较第一轮输入。

## 3. 必测场景

- 中文、英文、中英混合、Snake/Camel 协议名及去Server前缀的原始名称。
- MCP标准 `title`、中文全拼/首字母、单个错字回退及短查询禁用模糊匹配。
- 纯英文Schema面对中文查询不得依赖内置翻译词典产生虚假命中。
- 查询、写入、删除等相近动作。
- 多 MCP 全局 Top 3。
- 无正相关工具时不补齐任意候选。
- 第三方 MCP 只有 name、description、inputSchema 也能建档。
- 小型 MCP 全量直出且没有 `search_tools`。
- 搜索命中后下一轮出现原始 Schema，并由原 MCP Toolset 执行。
- 无附件、当前附件、历史附件和跨会话附件。
- Local/Native Tool Search 的 SSE、历史、取消和压缩配对。
- Retriever 异常时恢复已授权全量工具。

## 4. 当前脱敏基线

仓库内 14 条用例覆盖 25 个通用 DeepFOS风格工具，包含真实双语描述、MCP标准中文 `title`、
协议名、拼音和错字回退；不再把“中文问题应凭硬编码词典命中纯英文Schema”作为验收条件。
当前门槛为 Recall@5 100%、Recall@3不低于90%、高风险误选为0且Schema Token估算下降不低于70%。
该小样本只作为合入门槛，不能替代上线后的真实任务观测。

## 5. 测试数据安全

- 不保存 Test Token、Cookie、Header、真实用户问题或完整业务 Schema。
- 临时报告必须标记 `TMP_to_delete`。
- 浏览器验收结束关闭 Browser/Context 并清理自动化进程。
