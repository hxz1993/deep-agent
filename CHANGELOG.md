# DeepAgent 更新记录

本文件记录 DeepAgent 的产品版本变化，按新版本到旧版本排列。当前代码版本以
`deep_agent.__version__` 为准；数据库结构版本以 Alembic head 为准，两者互不替代。

## v0.4.19

- Console附件选择器增加`.log`；动态上传能力加载后仍保留该扩展名入口。
- `.log`被浏览器识别为空MIME、`application/octet-stream`或常见日志MIME时，上传链路仅针对该
  后缀规范化为`text/plain`，并由统一`read_file`按行分页读取；其他二进制格式仍然拒绝。
- Alembic Head保持`006`，聊天、SSE与History协议不变。

## v0.4.18

- 新增直接披露的系统工具`export_file_to_download_center`：模型可把新生成的UTF-8文本上传为平台DL文件
  并登记到当前用户下载中心，也可复用当前会话附件或大工具Artifact的File ID，只登记不重复上传。
- 已有文件权限仅从当前Session原始History恢复；猜测File ID不会访问平台。下载中心登记失败时只清理
  本次新上传文件，不删除已有来源文件。
- 下载中心的`fileDescription`按平台真实语义保存完整文件名，不再当作自由文本说明，确保Markdown、
  HTML等文件下载后保留原扩展名。
- 工具保持普通Tool Call/Result和现有Final协议，不增加Artifact表、聊天文件卡或数据库迁移；Alembic
  Head仍为`006`。

## v0.4.17

- Console移除用户消息下方的分支编辑入口，在原位置改为复制图标；复制内容仅为用户气泡的可见正文，
  不包含附件、Context Reference或隐藏元数据。
- 复制成功后图标短暂切换为勾选并显示提示，Clipboard不可用或写入失败时给出明确错误反馈。
  重试、历史分支、SSE和后端协议保持不变，Alembic Head仍为`006`。
- Planning仅识别当前启用的三个工具；删除旧工具名和仅凭`source=planning`触发的历史兼容判断，避免
  已下线工具继续进入计划快照或Console计划面板。

## v0.4.16

- Harness Planning从六个工具收敛为`write_plan`、`read_plan`和`update_task_statuses`；旧History
  中的六种规划工具继续识别，不做消息迁移。
- 通过Harness公开`guidance`明确稳定任务ID、批量状态更新、Deferred新Run重新评估，以及最终正文
  输出前完成真实状态收尾；不修改框架源码或业务Agent Prompt。
- Console在Run正常完成或进入UI Pending后清除计划浮层，取消和失败仍保留最后快照；计划浮层去除
  外部阴影。DeepCube和DeepTable客户端本轮不调整，Alembic Head仍为`006`。

## v0.4.15

- 简化URL MCP工具重试耗尽收口：删除同一Run内按工具名缓存失败状态的额外分支，最后一次
  `ModelRetry`直接转换为Pydantic AI原生`ToolFailed`。
- 最终失败Tool Result保留MCP提供的模型可见业务校验原因，并提示模型停止当前工具调用、解释失败并
  等待用户下一条消息；网络、超时和连接异常仍使用脱敏结果。
- SSE、History和数据库结构不变，Alembic Head仍为`006`。

## v0.4.14

- URL MCP工具在最后一次可纠正重试仍被拒绝时，转换为脱敏的失败Tool Result，不再让
  Pydantic AI的`exceeded max retries`异常直接中断整个Run。
- 模型收到失败结果后可正常解释并结束；失败Tool Call、Result和最终回答继续进入History，用户可在
  下一轮重新尝试。同一Run再次调用已耗尽工具时不再访问远端MCP。
- 不提高全局重试次数，不改变超时、取消、SSE、History结构或数据库；Alembic保持`006`。

## v0.4.13

- 兼容Go MCP工具执行超时返回的`context deadline exceeded`，将其从Pydantic AI的自动
  `ModelRetry`重新分类为失败Tool Result，避免单个MCP超时耗尽重试并中断整个Run。
- 支持MCP侧稳定的`CANVAS_ERROR`文本契约；仅`retryable=false`视为终止性失败，修改结果未知时
  提示模型不要直接重放操作，应先读取当前Canvas快照。可纠正业务错误和正式取消保持原语义。
- 不改变MCP协议、Canvas超时时间、SSE、History或数据库结构；Alembic保持`006`。

## v0.4.12

- Console 启动时先通过平台综合权限接口确认当前用户是否为企业管理员；普通用户及固定超级用户
  `admin` 均显示整页无权限提示，不再加载工作台、配置中心或诊断数据。
- Agent 配置、模型、MCP、Skill、UI 工具、全局设置和管理诊断等 API 统一增加企业管理员门禁；
  聊天、会话、附件、取消、压缩和按固定 ID读取 Agent 等业务使用接口保持开放。
- 权限结果按用户和凭证摘要缓存30秒；平台401返回未认证，超时、连接失败和异常响应按503失败关闭，
  不记录或返回原始Token/Cookie。Alembic保持`006`。

## v0.4.11

- 修复URL MCP真实工具调用超时被Pydantic AI包装为`ModelRetry`后绕过DeepAgent错误隔离的问题；
  请求超时、连接中断和5xx不可用现在形成脱敏的失败Tool Result，模型可继续换工具或解释失败。
- 参数、对象选择和普通业务校验仍使用`ModelRetry`；正式取消、工具协议、SSE和History结构不变。
  Alembic保持`006`。

## v0.4.10

- 更新内置DeepCube MCP包；发布流水线继续先注入内部Wheel，`requirements.txt`只声明`deepcube[mcp]`，不向公开PyPI查找内部版本。
- 固化DeepCube包内11个短工具名的契约测试；DeepAgent继续在外层增加`deepcube_`前缀，模型最终看到的协议名不变。
- 数据库表命名标准化登记为暂缓项，不为命名整齐扩大当前迁移和滚动发布风险。Alembic保持`006`。

## v0.4.9

- 将MCP小目录直接披露上限从3个调整为5个：全局候选不超过5个时全部向模型提供完整Schema，
  不再额外注入`search_tools`；超过5个时仍保持首轮预选Top 3、主动搜索Top 5。
- 将“直接披露上限”和“预选数量”拆为独立常量，并同步工具包API与Console文案；权限、工具协议名、
  MCP快照、延迟连接和Alembic `006`均不变化。

## v0.4.8

- 将运行时固定升级到`pydantic-ai-slim 2.31.0`与`pydantic-ai-harness 0.21.0`，继续固定
  `openai 2.46.0`，并保持稳定解析到FastMCP 3；Alembic仍为`006`。
- Deferred工具和Capability工具的可执行状态统一由Pydantic AI Core判断：未搜索或未加载的工具
  即使被模型猜中协议名也不能执行；压缩历史移除披露证据后需要重新搜索。
- Planning、Skill、ToolOutputLimits、MCP快照、取消和上下文Checkpoint保留现有产品协议，随框架升级
  获得Planning缓存边界、MCP新协议兼容、Provider流关闭及安全修复，不新增客户端适配。

## v0.4.7

- Console将Harness任务计划从机器人回复内部移到消息区底部的轻量浮层，当前Run只保留一张面板并随最新
  Planning快照实时更新；步骤完成状态可直接勾选查看，完整计划全部完成后立即隐藏。
- 打开历史会话时只检查最新Assistant Run：仅恢复其最后一份未完成计划；最新Run无计划或计划
  已完成时不回捞更早计划。取消、失败和Pending的未完成计划分别显示明确终态。
- 后端Planning、SSE、History和Tool Result中的计划快照保持不变；Alembic仍为`006`。

## v0.4.6

- 全局接入Pydantic AI Harness 0.18 `Planning()`默认六工具。模型可自行判断复杂任务是否需要计划；
  每次Run使用独立`InMemoryPlanStore`，不增加Agent配置、数据库迁移或跨轮任务状态。
- 规划工具继续使用标准Tool Call/Return；SSE和History以`source=planning`标识，并在已确认的
  Tool Result上附带结构化计划快照。取消、失败、Pending和上下文压缩继续遵循现有消息配对语义。
- Console最初把六个规划工具聚合成一张“任务计划”卡；系统工具目录新增只读“任务规划”分组。
  v0.4.7进一步收口了计划的展示位置与历史恢复范围。Alembic保持`006`。

## v0.4.5

- 删除内置Doc Search MCP实现、初始种子和现行产品文档入口；当前新数据库只预置DeepCube，
  但管理员仍可按通用URL MCP机制自行创建同名服务。
- Alembic `006`删除已有`mcp_servers.name=doc_search`预制记录，并从显式Agent能力列表中移除
  所有`mcp:doc_search`引用；保持其他能力顺序以及`null`、空列表语义，降级不恢复已删除授权。
- 历史会话及其中已有的旧Tool Call/Result保持原样；Console随运行目录自动移除对应卡片和选项，
  并通过后端目录、API和前端防残留测试锁定删除结果。

## v0.4.4

- 删除仅用于演示的内置Text Tools MCP实现、初始种子、Console示例和现行产品文档入口；MCP通用机制、协议与历史会话保持不变。
- Alembic `005`删除已有`mcp_servers.name=text_tools`预制记录，并从显式Agent能力列表中移除所有`mcp:text_tools`引用；保持其他能力顺序以及`null`、空列表语义，降级不恢复已删除授权。
- 真实共享MCP进程、租约、TTL、并发和聊天回归改用`tests/fixtures/`下的测试专用Echo MCP，Fixture不会被生产内置目录扫描。

## v0.4.3

- Console Skill页面支持导出单个上传Skill和导出全部上传Skill。单个导出返回平台保存的原始ZIP，
  可直接通过现有“上传安装”导入另一环境；全部导出生成集合ZIP，包含一份清单和多个仍可独立
  安装的`skills/<code>.zip`。代码内置Skill随产品版本发布，不进入导出包。
- 导出不新增数据库、Revision或批量安装协议，也不迁移Agent绑定。普通上传遇到相同Skill `code`
  仍返回409；只有管理员明确使用“替换ZIP”，且新包`code`与原值一致时才覆盖当前包。
- 配置备份新增ORM表范围完整性校验，并在备份文件中写入脱敏的范围清单，明确配置表、只保留结构
  的会话表以及未内嵌的Skill原ZIP。数据库备份与Skill“导出全部”组成完整留存方案，但仍保持
  两个独立、可检查的文件。

## v0.4.2

- 本地 `search_tools` 的模型说明改为一次提供最多3个互补查询：用户原始语言、简洁英文能力描述，
  以及名称目录中的可选协议名。利用主模型已有的中英理解能力，减少“中文先失败、下一轮再改英文”
  造成的额外模型请求。
- 第三方 MCP仍不要求中文 `title`或双语描述；后端不增加翻译服务、业务同义词词典、Embedding或
  额外模型调用。首轮自动Top 3不变，只有模型主动调用 `search_tools`时使用该策略。
- 名称目录明确区分“能力描述可以双语表达”和“协议名必须原样复制”。结果仍受授权、只读副作用
  过滤、已披露排除和Top 5上限约束。

## v0.4.1

- MCP Tool Search改用独立的通用词法检索内核，按协议名、MCP标准 `title`、参数名和描述分级
  匹配，支持NFKC、连接符、拼音和受限错字回退，并返回稳定的命中证据与置信状态。
- 查询明确包含完整或去掉Server前缀的协议工具名时，只返回精确命中的工具，避免额外披露相邻
  Schema；动态目录同时提示模型优先复制协议名，不反复翻译改写。
- 不内置业务或中英文翻译词典：纯英文Schema不能凭中文问题自动获得语义召回，只有标准 `title`、
  双语描述、拼音或协议名本身提供词面依据时才会命中。URL MCP检测快照同步保留标准 `title`。

## v0.4.0

使用 Pydantic AI Harness 0.18 的 `ToolOutputLimits`治理大工具结果：阈值按当前模型可用输入窗口的
20%计算，并限制在4K～24K Token。普通 Function、MCP、Sandbox和MCP Resource工具超过阈值时，
完整结果保存到DeepFOS平台文件服务，模型与History只保留受限预览、结构草图和平台File ID；
模型通过Harness原生系统工具 `read_tool_result`按行、尾部或关键词读取完整内容。平台上传失败时
回退到8K字符有损截断，避免大结果继续挤占上下文。

句柄只允许从当前会话原始 `chat_messages`恢复，不信任模型猜测，也不使用可能被压缩的Checkpoint
扩权；同一Run回读复用内存并使用single-flight下载。SSE和History增加可选 `artifacts`元数据，
Console提示“完整结果已外置”并保留预览；系统工具目录增加“工具结果”分组。没有新增Artifact表、
下载中心、TTL或Alembic迁移，数据库head仍为 `004`。

## v0.3.24

收口文件与脚本执行语义：`read_file`只读取文本、DOCX和XLSX；图片不再作为文件工具分支，
当前轮图片附件直接作为原生多模态输入交给Provider，DeepAgent不再根据模型目录预先拒绝，
不支持图片的模型由Provider正常报错；历史图片和Skill图片均不读取。删除PDF上传和模型模态配置，
PDF与PPT/PPTX保留为未来独立文档解析工具的产品方向，不并入 `read_file`。

`execute_skill_script`与 `execute_python`明确共用当前 `SubprocessBackend`。Agent选择含脚本Skill并
启用 `builtin:sandbox`时即可执行固定脚本。仓库删除未实施的隔离探针、Sidecar方案和相关待办，
只保留当前一次性Python子进程、资源上限、取消和临时目录清理等真实运行链路。Alembic保持 `004`。

## v0.3.23

将会话附件与 Skill 文件收口为运行时系统工具 `read_file`：文本按行、DOCX按逻辑块、
XLSX按物理行分页。文件字节和完整解析结果只在一次用户
Run内缓存并使用 single-flight，下一轮重新加载。旧的三个附件工具、`read_skill_resource`和
可配置的 `builtin:attachment`均已删除；Alembic `004`只从显式 Agent能力列表中清理旧编码。
`execute_python`仍属于可配置的 `builtin:sandbox`。

依赖热修复：将 OpenAI Python SDK 固定为 `2.46.0`。Pydantic AI 2.26 对 OpenAI SDK 只声明
`>=2.45.0`，干净构建曾自动解析到 OpenAI 3.0，而该版本的异步传输要求 `aiohttp>=3.14.1`，
与 DeepFOS平台运行时的 `aiohttp 3.9.x`不兼容。固定版本避免同一提交因上游发布而构建漂移。

Skill恢复修复：上传与当前内容Hash相同的ZIP时，先核验平台原文件；只有原文件可下载才返回
`noop`。平台File ID已经失效时，管理员的同Hash替换会重新上传原包、更新数据库File ID并恢复
当前Pod运行目录，避免“当轮可用、Pod重启后再次丢失”。

## v0.3.22

复用会话附件解析内核，让已授权 Skill 的文本、DOCX 和 XLSX 资源通过统一分页工具按需
读取；历史安装包无需重装即可按路径和 MIME 识别 Office 资源。Sandbox 内部增加受控脚本请求
协议，只允许显式选择同一 Skill 的 Python 脚本、输入和资源，并只从 `outputs/result.json`读取
正式结构化结果。该协议后续由当前Sandbox直接对模型开放；Alembic仍为 `003`。

## v0.3.21

在 Console 增加独立只读的“系统工具”目录，按 Skill、MCP 工具发现和 MCP Resource
展示运行时自动注入工具的来源与触发条件，但不把它们加入 Agent 能力配置或数据库。Python
沙箱说明同步收口：预装三方包从当前 Sandbox Profile 动态生成，明确完整 `code/inputs`、最后
一行结构化返回和大表摘要规则，并提供可直接参考的最小代码示例。Alembic 仍为 `003`。

## v0.3.20

将上传 Skill 的运行副本收敛为每个 Pod 的 `runtime-skills/<code>/` 单一当前目录。
服务启动、安装、替换、启用或管理员重载时同步平台 ZIP；普通 Agent Run 只读本地文件，不再按
内容 Hash保留多份目录或逐轮访问平台文件服务。单个 Skill 同步失败只会让该 Skill 暂时不可用，
不会阻塞其他 Skill、工具和普通回答；Console 与 `final.context.skills` 提供脱敏诊断。Alembic
仍为 `003`。

平台 ZIP 是系统级配置文件，不属于业务 App/Space。上传使用可无 Space 回读的 `INF` 文件类型；
安装、替换、Pod 启动恢复、管理员下载和 Run 兜底均保留调用身份但主动去掉
`app/space/language`业务上下文。数据库不保存请求上下文、Token、Cookie 或完整 Header。

## v0.3.19

将 URL MCP 的“发现”和“执行”拆开：管理员检测时把工具定义保存到现有 inspection
快照，聊天 Run 直接使用快照完成 Tool Search 和 Schema 披露，只有模型真实调用工具时才按
当前请求 Header 建立远程连接。同一 Run 复用连接；单个远程 MCP 暂时不可用时返回
`ToolFailed`，不阻塞其他工具或普通回答。历史 MCP 缺少明细快照时不会进入模型工具列表，需在
Console 执行“检测并更新”。Alembic head 仍为 `003`。

## v0.3.18

将 Skill 与 ToolPackage 分离，新增 Alembic 003 的 `skill_packages` 和 `agents.skills`。
上传 ZIP 原包保存在平台文件服务，数据库只保存 File ID、Manifest 和资源索引；Harness 0.18
初始仅披露 Skill 名称与描述，模型调用 `load_capability` 后才加载完整 `SKILL.md`。References 与
Assets 通过授权分页工具按需访问；脚本正文不能读取，只能通过受控脚本工具执行。

## v0.3.17

为每个 Session 建立稳定 `conversation_id`，为每次发送、Deferred 续跑和压缩生成独立
`run_id`；SSE、History、结构化日志和 Console 会话详情使用同一组标识关联运行。日志只记录
脱敏生命周期、Usage、缓存 Token 和工具次数；Provider 未报告缓存指标时保持未知。MCP 仅为
`cube_query_data` 与 `dimension_query_members` 两个复杂查询工具选择性披露可信返回 Schema，
并且只在工具实际直接披露或经 Tool Search 加载后产生额外 Token。

## v0.3.16

将自动和主动压缩收敛到同一内部投影流程，并以固定六段结构更新摘要；旧摘要作为
单一锚点合并新事实，不累积摘要列表。自动阈值、主动接口、SSE、Checkpoint 和失败兜底语义
保持不变，压缩核心通过独立覆盖率门槛检查。

## v0.3.15

让模型目录的 `max_output_tokens` 真正作为普通与流式请求的模型输出上限生效；同时
区分模型可纠正的 `ModelRetry` 与已经终止的 `ToolFailed`。附件下载、损坏、解析、超限和
MCP Resource 上游失败不再无意义消耗工具重试次数，工具协议、SSE 和历史结构保持不变。

## v0.3.14

修复压缩与工具检索联调边界：每个 Pod 固定一个异步 Gunicorn Worker，使进程内
活动 Run 与取消请求一致命中；主动压缩先返回终态并关闭 SSE，客户端再刷新历史和上下文状态。
MCP 预选以用户问题为主、Context Reference 标签仅作无命中回退，同时识别否定式只读表达，
过滤明显副作用工具，并在单次 Run 内排除已披露工具和重复搜索。Pydantic AI 的跨 Run 工具
发现历史恢复语义保持不变。

## v0.3.13

完成 Pydantic AI 2.26 上下文压缩兼容收口：同一个官方 `CancellationToken`
贯穿正式回答与内部摘要，停止生成时不会因摘要失败回退后继续回答；Local 与 Provider Native
工具统一按官方 Call/Return 基类配对，避免原生工具历史阻断主动压缩或产生错误修补。现有
单次摘要、Checkpoint、主动压缩接口和 SSE 协议保持不变。

## v0.3.12

为已授权 MCP 增加工具检索与渐进披露：候选超过 3 个时，首轮仅完整披露本地
词法检索命中的 Top 3，其余工具以稳定协议名目录提供，并可通过 Pydantic AI `search_tools`
按需加载 Top 5；小型 MCP 仍直接披露全部工具。无合法会话附件时附件 Toolset 不向模型
暴露。该优化不改变 ToolPackage 授权、MCP Header、真实执行和历史协议。

## v0.3.11

针对 MySQL 短空闲超时增加连接池保护：连接使用前继续执行 pre-ping，且仅
MySQL 连接在 90 秒后主动换新；asyncmy/uvloop 在 pre-ping 阶段报告已关闭 Transport
时会被识别为断链并由 SQLAlchemy 重建连接，不重试已经开始执行的业务 SQL。

## v0.3.10

将停止生成收敛为正式取消链路：客户端调用 session 取消接口后继续读取原 SSE，
直到 Pydantic AI 返回真实 `RunCancelled`；部分回答、实际 usage 与 `state=interrupted` 历史
由后端持久化。用户停止不再由前端断流、定时器或本地伪造消息实现，断开连接只负责异常资源清理。

## v0.3.9

将 Pydantic AI 升级到 2.26.0，DeepSeek V4 Flash/Pro 改由官方
`DeepSeekProvider` 与 Profile 处理，并删除本项目的模型消息兼容层。

## v0.3.8

将 Console 工作台、配置中心、动态会话和工具过程中的字符及 emoji 图标统一为
随仓库发布的 Lucide 线性 SVG，并补齐动态渲染、主题尺寸和无障碍标签。

## v0.3.7

统一自动与主动压缩的实时过程条、Checkpoint 保存结果和历史压缩标记；配置中心按
“Agent 设置 / 系统设置 / 辅助资源”重新分组，并增加不暴露凭据和本地完整路径的只读数据库
连接摘要。

## v0.3.6

将上下文语义压缩收敛为单次摘要：在一个安全消息边界上选择待摘要前缀与最近尾部，
一次生成并复检 Checkpoint。429、502、503、504、网络重置或超时只允许使用完全相同的摘要
请求重试一次；无法安全装入模型窗口时不拆段归纳，也不覆盖已有 Checkpoint。

## v0.3.5

为普通和流式聊天增加结构化 `context_refs`。原始引用及 `metadata` 随会话历史保存，
模型只接收去掉 `metadata` 的 XML + JSON 派生投影；纯引用会话、分支、标题和上下文压缩
使用同一原始事实源，不新增数据库表或迁移。

## v0.3.4

在原有自动上下文压缩之外增加 session 级主动压缩 SSE 接口。Console 可在输入框旁
随时尝试压缩当前历史，并展示后端估算的上下文占用与当前有效 Checkpoint；原始聊天历史
仍完整保留。空、短或无明显压缩收益的历史由后端返回 `noop`，前端不替用户禁用入口。
