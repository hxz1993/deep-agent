# 前端联调回归与问题交接

纯文案、颜色、布局改动：必要静态检查 + 一次受影响桌面流程冒烟即可，不默认测试移动端或重跑全量。下列矩阵按受影响协议/行为选取；安装规则、关联、事务或运行装配变更才需要对应后端回归。

## 目录

- [最小回归矩阵](#最小回归矩阵)
- [问题归因](#问题归因)
- [后端交接模板](#后端交接模板)
- [提交前检查](#提交前检查)

## 最小回归矩阵

### 请求与会话

- 新会话收到 `session`，后续消息沿用同 ID。
- 列表分页、打开历史、重命名、删除均正常。
- 缺用户 Header 时显示 401，不循环重试。
- 其他用户/NULL owner 会话的 get/history/patch/delete/resume 返回 404，页面不绕过。
- `message` 保持纯用户正文；`context_refs` 不进入可见 user prompt 和会话标题。
- 历史 `items[kind=user_message].context_refs` 能恢复上下文 Chip；旧 XML 按产品确定的兼容策略展示。
- 新会话、快照变化和历史恢复后的首次续聊会发送引用；普通追问和仅发生自动压缩时不重复发送相同 signature。

### SSE

- thinking、text、tool_call、tool_result 的实时草稿顺序正确；正常 `final` 保留直播结果且不额外读取 History。
- 打开、切换、刷新、重连或异常断流恢复时，History 与直播使用同一页面消息/segment 结构和 renderer，最终内容一致。
- `session_title` 即时更新页头和列表，Network 中没有标题轮询或普通聊天 Event API 请求。
- `final.completed`、`final.pending`、`cancelled`、`error` 都结束 loading。
- 同一Session、Run/Turn及模型消息等局部调用上下文内，相同 `tool_call_id` 的重复通知不产生重复卡片；不同轮次/消息可能复用ID，不能跨上下文全局去重。原生结果另按 `output_id` 去重。
- token/请求等使用实际final.usage/run_metrics；缺失未知，管理员取证可核对持久诊断真值，不从History估算。
- Plan 只由当前 Run 的 `write_plan` 更新；`final/pending/cancelled/error` 后均隐藏，重新打开 History 不恢复计划面板。

### 原生输出与业务采用

按[原生输出参考](frontend-structured-output.md)覆盖当前Run配置装配、首轮、续轮稀疏patch、失败后成功、重复/同ID冲突（含final-only）、false/0/null、断流只读恢复、旧新历史及业务拒绝。平台接受、业务采用、人审与保存分开判断；正式保存还须独立回读。

每个格式失败即使同Turn修好也记录attempt/output_id与原始叶路径；不自动新开格式repair、不用最终成功掩盖失败候选。Schema合法但UUID抄错、引用不全或hash过期另记业务拒绝。客户端POST、Provider请求、格式attempt和业务动作分开统计。用户明确要求重复次数/目标样例时逐案满足，不能将一次或旧回执当两次。

### BOM人审与真实保存（仅该产品）

- 覆盖“确认→输入或编辑本行备注自动撤回→再次确认”，包括这组记录在同一批提交。按作用域和顺序折叠备注事件，撤回能引用本批中更早的确认，不能只查之前持久历史。
- 新增或编辑本批备注时，保留未受该备注实际影响的行确认；本行方案、值或说明变化按产品规则失效，不将所有全局备注一律视作所有行变化。
- 缺用量等必需值时保留待确认；明确补值后只改变指定行。编号精确查询使用映射字段实际值，不借相邻客户号。
- 保存前后分别核对动作数、目标记录ID、真实写入值及复用物料未被改写。确定性内核直连测试与实际HTTPS浏览器操作分开标记；旧版本矩阵不能冒充当前版本重跑。
- 刷新/加载会话只读恢复，比较请求数与原执行，不自动重发保存。页面仍在等待时先读原execution/journal和真实记录，不因等待较久就重建执行。
- 下载回执仅在最后一轮导入结果提供；不在上传、核对、计划或历史入口重复增加。已经经过的步骤按真实完成事实显示完成，不因进入下一轮遗留灰色状态。

### 混合文字/JSON（仅明确启用时）

- allow_text_output默认false；原严格业务链路及必需JSON结果不因升级改变。
- 纯文字完成无output_info/成功output事件，History为assistant_message；JSON按output_info及structured_output识别，包含Schema合法字符串/null/false的区分。
- text后仍完整收流；同响应文字+合法JSON只采用一次JSON。失败候选的文字不进入普通回复；mixed首正文晚于首Token不直接判卡住。
- UI暂停后的执行装配当前策略并保留剩余预算；正常用例保持配置一致，策略变更用隔离Agent测试。旧带快照历史和新无快照历史都按真实结果展示，不重新校验；断线只读恢复不自动重跑。
- 同Run伴随assistant_message即使phase=final，也不挡住唯一有效原生JSON；覆盖文字在结果前／后、刷新和断流。输出身份缺失、归属错误或多结果冲突仍明确拒绝，不能为通过伴随文字用例取消这些检查。
- 普通文字不能改变业务基线、人审或保存状态；测试报告区分隔离平台分支验证、真实业务校验及实际写入回读。

### 自动上下文压缩

- `context_compaction_progress` 过程事件更新独立状态行，不污染 Conversation Store、助手正文和 segments。
- 首个事件延迟显示、后续 phase 原位更新；用户回看历史时不抢自动滚动位置。
- `final.context.compaction.compaction_event_saved=true` 后在当前 Run 原触发位置显示“已自动压缩上下文”。
- 未保存、摘要失败或 `sliding_window_fallback` 只短暂展示，不产生错误 Toast。
- 刷新或打开 History 不恢复压缩标记，原始可见消息不折叠、不减少。
- `cancelled/error`、切换/新建会话和组件卸载会清理压缩状态及计时器。

### HITL

- 合法 clarify：实时表单可见，确认后续跑完成。
- 非法到合法重试：实时只显示最终合法表单，无空占位/`fields.map` 崩溃。
- 刷新历史：仍只保留最终合法表单。
- `field`、`id`、textarea、`option.name/description` 均能渲染，提交 values key 稳定。
- 取消后续跑结束，不继续生成第二张表单。
- Confirm／Choice的Call可属于暂停前Run，Result属于Resume Run；使用真实待交互身份关联，刷新前后只一条记录。覆盖多个UI工具、部分返回、重复帧、同ID跨轮复用；不把人工等待标成工具执行耗时，也不要求业务客户端自行解析原始Events。

### config_fill

- 合法 JSON 渲染配置表单，点击后写入正确页面字段。
- 非法 JSON 显示解析错误，不静默修复。
- 流式未闭合时显示准备态。
- 填充后可撤回；历史重开仍能操作。

### DeepCube页面与附件（仅该产品）

- 全局开关关闭时不显示、不挂载 Agent 功能。
- 只有 DeepCube 模式显示；普通 Python 模式不加载助手边栏。
- `/llm-models` 只使用服务端返回的当前可选 preset；不按模型名称或客户端 `enabled` 猜测。
- `/uploads/capabilities` 的 MIME、大小、数量生效；`kinds=[]` 时禁用附件入口。
- 文本模型允许环境已开放的文档并在上传前拒绝图片；视觉模型可组合图片与文档。
- 选择、拖拽、粘贴、混合批次、部分失败、预览、移除、切模型和历史附件恢复正常；Chat Body 仅含 `{id,name,mime}`。

### DeepCube 会话质量

- 优先批量执行仓库 `/debug-demo` 隐藏开场题，不用临时编造只验证 200 的问题。
- 每题记录：会话 ID、去重后的工具调用序列、`final.status`、`final.usage`、历史 context refs、输出代码/配置是否合法、是否过度澄清、是否重复查询。
- 重点覆盖：奖金+范围混合任务、P2 参数、毛利率、连续计算、阶梯返利 clarify、费用分摊、纯配置、范围填充、元素说明、解释和修复。
- 变量能力至少覆盖一次唯一变量元素直查：确认调用 `deepfos_variable_get` 后停止；查询说明保留原始 `reference`，页面绑定、数据范围和脚本改用同一个本地编码；未把定义配置误报成当前有效值，且用户变量只代表当前请求用户。
- 提示词与 Skill 拆分时，回读 Agent `skills`、Skill 版本/content hash/resources；用同一批业务题比较脚本、配置、解释和修复质量，不以 HTTP 200 代替质量验收。
- 质量结论区分协议成功和业务质量：HTTP/SSE 成功但成员选择、工具停止时机、配置完整度或脚本语义错误，仍判为失败或需优化。

## 按既有会话取证

用户提供环境和 Session ID 时，先走 [管理员只读诊断流程](frontend-session-diagnostics.md)，读取跨用户元信息、History、完整 Events 与关联日志，再做下述归因；不向原会话发送消息做试探。普通接口的跨用户 404 不等于管理员诊断接口不可用。

## 问题归因

前端问题常见证据：

- Network/SSE 数据正确，但页面状态、顺序、表单、历史或 loading 错误。
- 同一合法 payload 在前端归一化后字段丢失。
- 页面请求漏 `credentials`、app/space/language 或路径重写错误。
- 实时与历史使用不同 renderer 导致表现漂移。

后端/协议问题常见证据：

- SSE 发出无法解析或不满足约定的 tool args。
- 非法 retry attempt 被发到实时流或持久化历史。
- owner 校验、Header 透传、session resume、deferred ID 在服务端失败。
- 同样 Header 的只读 API 已失败，页面还未进入渲染阶段。

环境/代理问题常见证据：

- 本地页面 HTML 200，但真实业务 API 401/404/502。
- 浏览器请求直接指向 8000，被 mixed content/CORS 阻止。
- deep-agent 专用规则被通用 `/deepfos-server` 规则抢先匹配。
- app/space 错误，或后端本地 `deep_agent.local_dev` 身份 profile 未配置/未生效。

## 后端交接模板

只提供必要证据，不修改后端：

```text
环境：Test / 本机 8000
前端仓库与分支：...
页面 URL：隐藏 sign/token 后的路径
会话 ID：...
请求：METHOD + PATH，Body 仅列非敏感字段
期望事件：session → ... → final
实际事件：...
上下文：context_refs 是否发送；context_compaction_progress phase；final.context.compaction；History 是否未恢复压缩标记
实时页面：...
刷新历史：...
前端源码验证：文件/函数/行号
已排除：代理、鉴权、前端解析或其他项
建议后端检查：协议点，不给未经证实的根因
```

不要打印 token/cookie；用户 UUID 只在确认身份链路确有必要时传给负责后端的任务。

## 提交前检查

- `git diff --check`。
- 目标 Jest/类型校验通过；高风险交互再做真实页面。
- `git status --short` 不含 token.local、代理、浏览器目录、SSE、截图和构建产物。
- 保留用户指定原会话、复测历史与验收证据。临时资源标记TMP to delete；仅在本轮清理授权范围内处理，删除优先Trash，不为清理丢弃待核实执行。
- 关闭 Playwright/Chrome，停止自己启动的前端和临时代理。
- 不停止后端任务要求保留的 8000。
- 中文 commit message；只有用户明确要求才 commit/push。
- 升级验收区分旧历史读取、旧Pending升新版、新Pending降旧版三种方向；代码发布、目标/info、实际页面资源和业务回读分别核验。不要把本地平台通过等同于线上业务客户端通过，未重跑的历史矩阵标明沿用证据。
- 外部通知按当前用户指令；用户说明自行转发后，交付只回当前会话，不沿用旧收件人或代发步骤。
