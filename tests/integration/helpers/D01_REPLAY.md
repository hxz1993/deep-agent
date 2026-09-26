# D01 独立闭环效果评测

`d01_replay.py` 使用真实 DeepAgent Runner、真实上下文压缩 capability、真实历史检索工具和 Event Store；业务工具为隔离的合成报表/工作簿函数，绝不调用线上业务 MCP。默认走 `run_stream + EventPersister`，可用 `--runner-mode non_stream` 单独验证非流式协议。

基线来自 `git archive f4673b047cdf069810c0deefc431fd1d9f9ed5dc`，位于 `output/TMP_to_delete-d01-baseline`。所有测试库、SSE、合成历史和隐藏 gold 都放在 `output/TMP_to_delete-d01-*`，可在正式归档后清理。稳定 SQLite 仅以 `mode=ro` 读取指定已启用模型配置，凭据不会写入报告或测试库。

## 冻结与盲测

先运行 `--freeze --dataset <独立目录>`；已有 manifest 时拒绝覆盖。八个场景包含中部证据、尾部证据、最新修正、只读权限、版本变化、分页查询、工具发现、单轮多工具。D01–D04 是 tuning，D05–D08 是 holdout。`inputs` 可以进入被测模型；`private-gold` 在 Runner 执行结束后才加载，只由确定性 scorer 使用。实现者只接收聚合指标与不含答案的协议错误，不查看 gold 或标注的会话结果。修改协议后要生成新批次，不能混用之前结果。

该夹具是压缩压力下的合成闭环，不等价于完整电子表格产品 UI 验收，也不等价于恢复的真实 Test 会话。`--window 24000` 是所有臂相同的测试压力窗口，不代表供应商实际上下文上限。

## 运行

在仓库根使用已安装依赖的 `.venv/bin/python`：

```bash
# 零网络：验证 Runner 双压缩、工具回读、写入和持久化接线；不是模型效果分数。
.venv/bin/python tests/integration/helpers/d01_replay.py --model fixture --cases D01 --repeats 1 --output output/TMP_to_delete-d01-results/protocol-smoke-NEW

# Flash 基线：八场景，每场景三次，80% 同触发。每次使用新的 output。
.venv/bin/python tests/integration/helpers/d01_replay.py --model deepseek-flash --repeats 3 --trigger .8 --output output/TMP_to_delete-d01-results/baseline-flash-80-NEW

# Candidate 仅替换源码根，保留同一数据集、窗口、输出预算和触发比例。
.venv/bin/python tests/integration/helpers/d01_replay.py --source-root . --arm candidate --model deepseek-flash --repeats 3 --trigger .8 --output output/TMP_to_delete-d01-results/candidate-flash-80-NEW
```

80% 的两臂先完成，再另起 85% / 90% 批次。跨 Provider 可以 `--model kimi-k2.6`；本机 GPT 若是断开的 OAuth 配置，该 runner 会拒绝读取 OAuth 凭据，不能写成测试通过。`--concurrency` 默认 2，每阶段超时 360 秒。

## 结果与判定

每个 case/repeat 使用独立 SQLite。`protocol.json` 记录源码根、基线 SHA、driver、源码内容和数据集哈希及参数；`source-files.sha256.json` 保存逐源码文件哈希；`metrics.json` 只含汇总；`result.private.json` 含隐藏逐字段评分、请求用量/耗时、工具调用、完整压缩状态与结构化错误；合成 SSE 和事件历史用于协议复核。

业务正确性按六个精确字段、授权、禁止重复写入评分；协议正确性另看普通场景至少两次、长轮场景至少三次摘要以及对应 completed 压缩事件，不能以八段标题齐全替代。记录请求工具次数（含历史检索/读取和工具发现）、工具 schema 字符量、模型请求数、输入/输出/cache token 和总耗时。供应商没有提供金额账单，因此不推断货币成本。失败运行不得从分母中删除；仅准备好的 fixture 或无网络 smoke 不计作真实模型通过。

非流式早期 smoke 暴露了基线 `ToolReturnPart.timestamp` 在增量持久化与 SDK 正式消息间不同导致 `compaction_tail_mismatch`；相关 TMP 批次保留为诊断，不混入流式 A/B 正式批次。

运行 `d01_report.py <运行目录...> --output output/Report-D01-context-policy-evaluation` 可生成只含匿名指标的汇总；它不会打开 gold 或 private payload。候选批次是显式源码快照，不代表最终 release；最终代码变动后需另跑冻结验收。

`--freeze --fresh-holdout` 保留 D01–D04 调参输入，为 D05–D08 生成新的私有数值种子；种子和答案只在冻结目录保存。已反馈过结果的 v3 作为开发集，不再声称未见终验。`d01_blind_export.py` 为独立评分者导出无方案标签的合成答案包，标签映射单独保存；实现者不应读取评分包中的 gold。`d01_diagnose.py` 只导出预算、内容尺寸、后缀工作单元和错误分类，供实现者定位重复压缩，不导出业务正文或答案。

压力构造先按历史预算添加完整历史轮次，使估算历史达到触发值约1.08倍，再让真实 Runner 加入指令和工具 schema 计算完整请求。因此本试验覆盖大工具结果导致越过触发线后的恢复，不能把24k测试窗口或85%/90%的小样本结果直接当作生产容量、成本或最优阈值估计；完整请求与供应商实际Token都保留用于复核。

## 补充审计与原版助手专项

`d01_authentic_assistant.py` 独立加载内置电子表格 V91、DeepCube V34 ZIP 中原版提示词及全部 UI JSON Schema；只跑本地冻结确认返回、压缩后历史回读和配置/条件续作。真实业务 MCP、浏览器 deferred dispatcher、完整 Skill 链路不在这个 smoke 的覆盖内。此专项使用 48k/4096 profile，不混入 24k/2048 主矩阵。

`d01_output_audit.py` 对冻结输出追加统一 JSON 提取审计，处理多个 JSON 对象或附带元数据的回答，保留原始 metrics，生成待独立盲评的匿名变更包。不得只重判某一臂。`d01_duplicate_reads.py` 仅按同一 Run、工具名、规范化参数、完整成功返回内容哈希统计完全重复请求；排除历史工具、失败重试及可能必要的工作簿刷新，不推断无意义查询。

最终脱敏聚合与覆盖限制在 `docs/reports/20260926-d01-context-policy-evaluation/`。隐藏 fixture/gold、匿名映射、数据库及完整运行输出仅保存在 `output/TMP_to_delete-d01-*`，不纳入发布。
