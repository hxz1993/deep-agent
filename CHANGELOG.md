# DeepAgent 更新记录

本文件记录 DeepAgent 的产品版本变化，按新版本到旧版本排列。当前代码版本以
`deep_agent.__version__` 为准；数据库结构版本以 Alembic head 为准，两者互不替代。

## v0.10.13

- 修复 Kimi 网络搜索：使用官方 Formula 搜索接口，支持当前会话模型与指定供应商两种模式，保留搜索结果与工具调用的完整配对。
- DeepSeek 会话原生搜索与模型测试不按型号或别名预先拦截，按实际供应商响应处理；调用异常沿用既有 SSE 错误事件，指定供应商下拉保留实测名单，默认使用 V4 Pro。
- Web Search 配置增加当前有效模型测试和已验证的供应商模型下拉；采用轻量真实搜索验证，拿到成功搜索结果即结束，不再等待完整回答。
- 修复聊天请求在 SSE 建立前被拒绝时错误恢复历史、覆盖原始错误的问题；诊断工具清单补齐原生搜索工具类型。
- 优化电子表格、DeepCube 集成配置弹框，统一组件副标题、Agent 占位和接口示例／适用功能页签；数据流节点模型配置仅归档原型，不新增正式配置项或初始数据。
- 发布审计补齐 Gemini / OpenRouter 搜索验证、DeepSeek 失败状态及客户端释放；固定供应商搜索沿用自定义地址，修复测试弹框异步操作竞态。
- 搜索测试按结构化执行证据判断成功，缺少记录仅提示无法确认，不将模型列入黑名单；配置说明简化为已接入供应商，不展示固定型号支持结论。
- 数据库 Head 保持 `006`，无需迁移；统一受影响静态资源缓存版本。

## v0.10.12

- 企微应用增加按需加载的成员缓存分页表，支持姓名与身份 ID 搜索、缓存状态和同步时间筛选；仅读取数据库，沿用管理员权限，不触发企微资料同步。
- 清理成员详情弹框与操作列，缓存字段直接展示；统一渠道图标、配置卡和搜索样式，表格填满剩余页面高度。
- 测试工作台与诊断详情共用历史统计、Run 状态、上下文引用和加载过渡；补齐用户与语言展示，标题会话 ID 显示前 8 位并保留完整复制。
- 修复成员模块加载期间重置缓存导致列表无法继续加载的问题，以及切换模型后概览显示旧模型、缺少模型 ID 时误匹配和 Event 读取失败后用量丢失的问题。
- 统一受影响静态模块的缓存版本，避免新旧依赖混用。
- 配置备份统一校验与报告五张仅保留结构的会话、渠道运行和成员缓存表，兼容尚无缓存表的旧 Revision。
- 数据库 Head 保持 `006`，无需迁移；发布流水线不变。

## v0.10.11

- 修复 Alpha 原有制品打包失败：将根目录四个带空格的原型和报告目录归档到 `docs/design/` 与 `docs/reports/`，同步资料链接。
- 增加原有根目录搬移打包逻辑的回归检查；Jenkins 流水线、业务代码、依赖和数据库结构均不变，Head 保持 `006`。

## v0.10.10

- 修复平台逻辑服务地址的归一化与 Eureka 发现，统一 Account 用户资料链路，避免 Test/Alpha 诊断页面仅回显 User ID。
- 会话列表、详情与当前账号统一展示 Username 和邮箱；详情支持悬停复制完整 User ID，查询缺失与服务失败分别兜底。用户筛选支持 ID、用户名、昵称和邮箱。
- 渠道会话用户输入框增加手动查询，浮层展示资料或错误，不改变弹框高度；查询沿用管理员身份与权限。
- 长会话详情增加加载过渡、取消与失败恢复；上下文补齐复用已渲染历史，减少重复解析。区分平台与企微活跃用户排行。
- 修复加载中离页再进入时的不完整缓存恢复、重复复制的反馈竞态；当前用户资料响应统一禁止 HTTP 缓存。
- 数据库 Head 保持 `006`，无需迁移。

## v0.10.9

- Agent 删除增加历史会话强确认，分别显示本人、其他用户及总会话数；确认后停止相关运行并同事务删除 Agent、全部会话和事件。
- 渠道删除增加绑定与运行记录数量确认，清理渠道台账，保留关联 Agent 和聊天历史；影响范围变化需重新确认。
- 修复会话删除与流式/压缩任务收尾的竞态，避免成功删除后浏览器继续显示旧流；取消协调遵循单 API 实例部署范围。
- 修复损坏的 Web Search 配置无法在控制台重新保存、缺失 Skill 无法解除关联、UI 单工具删除误判及工具移动留下显式悬空选择的问题。MCP 删除在事务内复核引用，统一关联写入的父锁。
- 复审 v0.10.7 与 v0.10.8，更新 Tab 顺序回归断言并验证搜索与续跑；数据库 Head 保持 `006`，无需迁移。

## v0.10.8

- 修复 MCP 搜索排除已可用工具后误返无关结果的问题：先匹配完整授权目录，再仅加载未披露的匹配工具，避免重复搜索逐批披露无关 Schema。
- 精确协议名缺失时返回明确说明，不再用其他对象的保存、创建工具凑结果；修复错误服务前缀和检索缓存对不同协议名称的混淆。
- 本地搜索结果区分已加载、已可用、目录缺失，并保留 SDK 工具披露及会话续跑证据；增加确认前核实执行能力与完成必要读取校验的运行指引。
- 验证搜索、跨轮续跑、DeepSeek Responses 序列化、Agent 装配和 PTC 共 109 项回归；数据库 Head 保持 `006`，无需迁移。远端 MCP 新工具仍需重新检测更新快照。

## v0.10.7

- Agent 资源 Tab 按内置、UI、MCP、Skill 排列，复用目录图标，仅显示已配置数量。
- 统一资源卡片的未开启底色、悬停和开启高亮，修复 UI 工具包关闭时被内部复选框误触发高亮的问题，并加深类型标签文字。
- Skill 改为相同的开关卡片，展示可截断的说明，保留来源、参考文件数和附件数。
- 收紧四个 Tab 与内容的间距，左右栏分割线上下留白；数据库 Head 保持 `006`，无需迁移。

## v0.10.6

- 渠道关联 DeepFOS 应用支持空间 API Key，以 `X-Deepfos-Key` 随 App、派生 Space 和 Language 发送；MCP 发送边界移除混用的登录态凭据，保留内部会话归属。
- 密钥配置仅返回前后缀掩码，支持保留、替换与显式清空；扩展设置采用左侧圆角边框选择、右侧详细配置，开关与切换保持高度，问号复用即时说明浮层。
- 修复渠道会话未记录应用范围的问题：创建时保存 App/Space，已有渠道会话在后续请求中同步生效范围。
- 会话诊断列表新增 App ID 列及应用筛选，筛选在分页和计数之前执行；精简标题图标，调整渠道历史会话的时间、提问数、最近标签与复制入口布局。
- MCP 连接诊断记录应用范围和鉴权类型而不记录凭据；401/403 明确提示鉴权或访问被拒绝，避免误报为临时网络故障。
- 数据库 Head 保持 `006`，无需数据库迁移。

## v0.10.5

- 渠道扩展开关切换保持弹窗高度，开关垂直居中、左右标题对齐；日志会话 ID 增加双气泡图标，渠道会话卡片按时间在左、ID 与提问数在右排列。

- 渠道扩展设置新增“关联deepfos应用”，默认关闭；开启后填写 App ID，自动派生 Space，为渠道聊天、MCP 工具与附件上传提供一致的应用上下文。
- 扩展设置采用两项之间无分割线的两行布局，开关位于标题左侧，应用关联在上、用户详情查询在下；关闭时隐藏对应配置并保留已填内容。
- 应用范围由服务端已认证的渠道配置确定，切换配置从后续请求生效；保留原会话用户和企微成员查询机制，不改变平台授权。
- 复用渠道 settings JSON，数据库 Head 保持 `006`，无需数据库迁移。
- MCP 工具搜索移除不准确的关键词只读过滤，保留数量阈值与原有授权边界，中文名称调整为“搜索MCP工具”。

## v0.10.4

- 控制台统一常见 Markdown 展示，新增公式、脚注和提示块；表格采用行分隔线，悬停可复制当前表格的 Markdown 原文；代码块支持语言标题、折叠、自动换行和源码复制。
- 新会话以当前 Agent 名称与品牌图标开场，输入栏位于标题下方；应用上下文融入副标题，可直接打开调试上下文设置，无应用时明确平台工具不可用。
- Agent 卡片和列表以四类能力图标与数量展示，统一主题色及未配置灰色；精简会话 ID、提问统计与相关图标，轨迹按用户提问分隔轮次。
- 修复诊断消息定位连带滚动整个页面、裁掉顶部并在底部留白的问题；对话与轨迹仅滚动所属容器。
- Python 沙箱随包提供 Noto Sans CJK SC 字体，首次导入 Matplotlib 时默认使用中文字体与 Agg 后端；普通 Python 调用不加载绘图库。
- 新增模型 HTTP 流首字节、等待进度及结束诊断，记录字节数、块数和等待时间，不读取或记录模型正文；用于定位 HTTP 200 后长时间无回复的问题，不改变超时、重试和取消策略。
- 审计修复脚注回链 ID 冲突与跨容器滚动，统一模块缓存版本，并定向升级 DOMPurify 至 3.4.15。
- 数据库仍为 `006`，无新增迁移；Test 环境模型等待的上游原因尚未确认，不能将新增诊断日志视作已修复上游响应。

## v0.10.3

- 新增渠道账户会话诊断，支持用户／群聊列表、活跃用户联动、历史会话与群聊发言人筛选；用户与群聊候选按数据库查询去重，不限于当前表格页。
- 平台用户筛选支持原生 User ID 和 Username，采用独立游标候选与批量资料解析；移除会话来源筛选，统一查询全部来源。
- 优化会话与渠道详情布局、列宽、分页、类型图标和时间设置浮层；ID 分隔点悬停切换复制图标，复制反馈保持文字位置稳定。
- 新增本地只读预览入口，阻止修改类请求；会话删除仅在后端成功后更新页面，失败时保留记录并提示。
- 电子表格预制包升级至 View Planning 提示词 V85、工作流 Skill 1.4.5、智能分析 Skill 2.13.2，保留三个完整 UI 工具；安装目录展示明确的提示词版本，无版本不显示。
- 更新控制台品牌图标，整理渠道交互原型及定时任务研究设计；定时任务仍未实现。
- 审计修复渠道空企微配置、查询期间会话被删除、鉴权失效后的身份缓存残留、候选键盘定位及重复删除请求；统一静态模块缓存版本，并补齐当前数据库与安装配置的回归断言。
- 数据库仍为 `006`，无需新增迁移；更新预制 ZIP 不自动覆盖已安装 Agent。

## v0.10.2

- 增加平台用户名补齐诊断：记录服务发现方式、命名空间、目标主机、HTTP 状态、人数、耗时与降级阶段，支持排查 Test 显示用户 ID；不记录凭据、用户资料及响应正文。
- 完善企微应用卡片、关联渠道展示、Secret 主动清空和灰色选择占位；成员缓存仅保留重置入口，确认浮层明确保留姓名与映射、按需更新。
- 验证超管机器人明文 User ID 与普通机器人 Open User ID 共用成员映射；无需新增超管配置。补充成员查询失败阶段、安全错误码及网关身份查询降级日志，不记录姓名、原始用户 ID 或凭据。
- 渠道标题统一为 `User｜姓名(原始 ID)` 和 `Group｜Chat ID`；修复标题行内编辑重复点击清空、字体不一致、空值提交及左边框裁切。
- 从 Test 官方导出更新预安装的 DeepCube 与电子表格 Agent 包。
- 数据库仍为 `006`，无需新增迁移；已有渠道资料查询开关保持原设置。

## v0.10.1

- 渠道页增加“渠道账户／企微应用”同行切换，应用采用列表与详情卡片，提供凭据编辑、连接测试和应用级成员缓存统计。
- 新增默认关闭的“查询用户详情”，多个机器人可关联同一企微应用。旧渠道升级后不查询资料；企业 ID 与企微 Agent ID 创建后不可修改，Secret 仅掩码回显。
- 企微成员密文 ID 转换与姓名查询由 API 统一维护，明文／密文映射合并、一周资料缓存、过期后台刷新，以及刷新／重置、并发去重和迟到结果保护。
- 启用后私聊标题展示姓名及原始 ID，首条私聊和每轮群聊来源上下文增加 User ID、姓名；未知和外部用户保留原始 ID，群名、会话所有者和工具权限不变。
- 新增 Alembic `006` 两张企微表；配置备份包含应用凭据与渠道设置，排除成员缓存。存在新增数据或引用时禁止静默降级。
- 恢复助手处理过程标题左对齐；统一集成未配置占位、清空操作及企微渠道图标、弹窗标题高度。

## v0.10.0

- 新增完整 Agent 安装包导入、导出及预制包目录，统一携带 Agent、上传 Skill 和 UI 工具定义，支持新建及显式覆盖指定 Agent。
- 安装预检展示资源新建、覆盖、保持不变、缺失及共享影响；写入采用摘要复核、事务和文件补偿，保留目标 Agent ID、会话及外部引用。
- 新增 Agent 的 UI 工具选择范围：保留共享包额外工具，本次 Agent 只启用选定工具；同步配置、复制、备份、导出和运行装配。
- 安装流程调整为选择安装包、配置 Agent、表格检查和独立反馈；上传立即解析并展示摘要或错误，覆盖保留原默认模型且不显示模型配置，空目标占位使用灰字。
- 统一弹窗主题表面、只读及提醒状态；UI 工具明细移除更新时间，点击行进入配置。
- 数据库升级到 `005`：允许 Agent 空模型，移除温度字段，保存 UI 工具选择范围。已有本地 `005` 需按安装包指南补齐结构；受限降级明确拒绝丢失配置。
- 收录 25,600 组隔离安装组合验收与专项结果；本轮 UI 收尾只做定向测试和桌面冒烟。

## v0.9.11

- 新增集成配置：电子表格助手、DeepCube 助手按编码选择关联 Agent；配置与接口示例同屏展示，列表平铺展示配置值。
- 新增 `/api/v1/integrations/{code}` 配置读取与管理接口，采用 `code`、`settings` 字段；读取需登录，修改需管理员权限，未配置返回明确错误。
- 数据库升级到 `004`，仅新增 `integration_settings`，不插入初始数据；配置备份与同版导入同步支持新表。
- 调整控制台设置目录顺序、渠道网关状态胶囊及快捷命令入口；日志等级选项使用英文，列表／原始切换采用紧凑图标按钮，原始日志字体缩小。
- 修复日志视图切换时查询按钮闪动及原始视图残留列表错误提示；集成保存增加超时恢复与结果待确认提示，避免网络悬挂时锁住页面。

## v0.9.10

- 日志正文在后端写入时包含工具、模型、Agent 名称及已有结果、耗时与 Token 信息；查询保留实际正文和原文，列表不再附加参数与关联 ID。
- 日志详情使用中文字段标签与紧凑布局，仅会话 ID 保留复制和跳转；原始日志默认折叠，列表会话 ID 固定显示前 8 位。
- 保留原日志事件、级别和业务结果，补充沙箱脚本失败的准确回显；按实际 UTF-8 字节限制日志长度，避免长正文导致整条记录丢失。

## v0.9.9

- 诊断日志改为逐行管道分隔文本，终端与文件复用同一消息；保留原记录位置、级别、触发条件及安全事实，不新增或减少成功日志。新文件为 `.log`，旧行保留原文。
- 日志查询返回真实 `raw`，保留管理员校验、分页与筛选；控制台增加列表/原始切换、字段原名详情和文本导出，自动加载有条数、大小和时间上限，切换查询与离开页面会取消读取。
- 合并 UI 与内置工具入口；统一下拉、字段提示、工具编辑与配置布局，调整渠道和模型启停及操作位置、会话与轨迹展示。
- 会话接口补充初始 App/Space 投影，保留所有者隔离；补充 MySQL 字符集说明。
- 审计修复模型目录加载中切换筛选丢失请求、Widget首次上方向键错选及浏览器往返缓存恢复后控件失效的问题，并统一静态资源缓存版本。
- 数据库 Head 仍为 `003`，无新增迁移，不清空现有数据。

## v0.9.8

- 修复 API 将数据库升级到 `003` 后，渠道网关仍要求 `002` 导致联合启动退出、平台入口返回 404 的问题。
- 移除渠道网关启动和统一健康检查中的固定数据库版本拦截；API 仍负责迁移，网关健康接口展示实际数据库版本，鉴权与健康状态检查保持不变。
- 增加真实迁移后 Gateway 生命周期回归，覆盖当前 Head 和相同结构下变化的版本标识，防止后续新增迁移再次遗漏。
- 增强 Test 只读 Jenkins 诊断，收集健康探针和更多当前/上次退出日志；不发布、不重启、不修改数据库。
- 数据库 Head 仍为 `003`，无新增迁移。

## v0.9.7

- 数据库升级到 Alembic `003`，移除 10 条外键；Provider、模型、Agent、UI 工具、会话和渠道的引用校验、删除限制及关联清理由后端事务维护。
- 使用父记录锁协调引用变更与删除；删除会话时原子清理事件并解除渠道绑定。渠道去重只处理真正的消息唯一键冲突，不吞掉其他保存异常。
- 配置备份按源库实际 Revision 输出，导入使用明确的逻辑依赖顺序、提交前引用检查和数据库写入门禁；仍要求备份和目标 Revision 一致。
- 迁移预检孤儿数据，保留列、索引和记录；兼容部分外键已删除，支持 `003 → 002` 降级。上线必须先停止旧 API/Gateway 写入，再迁移并启动新版。
- 保持 MySQL utf8mb4 部署要求；此版本不自动转换已有数据库字符集。
- 修正 UI 工具包删除确认说明与编码输入校验，不改变后端资源规则。
- 修正已有聊天 SSE 返回类型注解，使 OpenAPI 文档能正常生成，不改变聊天响应。

## v0.9.6

- 修复历史会话普通追问时，用户消息位于 UI 工具调用与真实结果之间，历史补齐误生成取消结果并触发模型 `Duplicate tool output` / HTTP 400 的问题。
- 在同一助手消息段内按调用 ID 和工具名匹配后续结果或重试，不跨后续助手轮次全局去重；真实缺失结果、原生工具和正式中断语义保持不变。
- 增加历史后续结果匹配、缺失结果补齐诊断日志，记录 Session、Run、Agent、工具名、调用 ID、阶段和固定原因，不记录输入、工具参数或结果正文；日志失败不影响续聊。
- 覆盖并行工具部分完成、调用 ID 复用、重试、日志失效及 SQLite 历史恢复后连续追问的回归，保留原始事件不变。
- 数据库 Head 仍为 `002`，无数据迁移；旧会话在升级后重新读取历史即可应用修复。

## v0.9.5

- 仅在生成会话标题时关闭模型思考，避免 DeepSeek Responses 的 96 Token 预算被推理占满、返回空标题；正常聊天和工具执行设置不变。
- 兼容 DeepSeek 新模型别名和两种协议、Kimi 原生思考开关及默认温度；继续使用首轮有效模型，保持单次请求与原有兜底、取消规则。
- 增加可按 Session / Run 查询的标题生成、失败、取消及耗时日志，区分输出预算不足、超时、图片读取和标题保存失败，不记录正文或异常原文。
- 渠道保留来源固定标题，不额外调用标题模型；增加渠道入口回归验证。
- 轨迹中的 `execute_python` 增加 Code 页签，复用 PTC 的 Python 高亮和复制功能。
- 数据库 Head 仍为 `002`，无数据迁移。

## v0.9.4

- 内置工具失败保留操作说明、原生错误类型、HTTP 状态、业务码及有界脱敏提示，覆盖下载中心导出、Python 产物发布、文件与 Skill 读取、Web 和 MCP 资源。
- 修复平台客户端丢失错误详情及非成功 HTTP 下载被误当文件的问题；各环境文件格式限制仍由平台决定，不从会话附件类型推断。
- 增加文件读取、上传、下载中心登记和清理等步骤日志，关联 Session、Run 和 Tool Call；日志只保存安全事实，不记录正文、凭据或异常原文。
- 分别报告原始失败与清理失败，避免误报已回滚；原生错误可进入模型、SSE、历史和轨迹，保持原有权限、取消及重试语义。
- 修复模型名称输入框使用浏览器缓存候选时的字体跳变。
- 数据库 Head 仍为 `002`，无数据迁移；联合启动与部署流水线不变。

## v0.9.3

- 增加停止接口入口、活动执行首次取消来源及 SSE 发送连接结束日志，关联 Session、Run、Agent 与连接耗时。
- 区分普通/渠道停止请求、实际 HTTP 断连、发送失败、服务关闭及未知任务取消，不再仅凭 `CancelledError` 推断客户端断开。
- 保留首次取消来源，正常连接收尾不等于模型执行成功；补充真实 ASGI/SDK 断连、停止、服务关闭、未知取消与日志失败隔离回归。
- 日志不采集请求正文、凭据或异常原文；执行、SSE、历史持久化与超时策略不变。数据库 Head 仍为 `002`，无数据迁移。

## v0.9.2

- 全新数据库默认使用 DeepSeek Provider 与 `deepseek-flash`，默认 Agent 保留固定 ID 与模型关联；API Key 留空。
- 初始化模型上下文 1000000、最大输出 384000，配置文本/图像输入及附件、推理、工具调用、结构化输出、Temperature、开放权重、交错推理标签。
- 当前默认配置与已发布迁移分离，只在空库首次升级至 Head 时应用；已有环境升级/重启不覆盖配置、不补建已删除默认项，数据库 Head 仍为 `002`。
- 收窄诊断会话默认列宽，复制 Session ID 后原位显示小勾，1.5 秒后恢复复制图标。

## v0.9.1

- 修复 DeepSeek Responses 工具搜索后重复声明工具导致的 400；通过 Provider 公开能力配置保留原生联网搜索，工具发现使用普通函数回退，不影响 OpenAI 和既有会话历史。
- 增加模型 API 请求/响应脱敏日志，记录路径、状态、工具清单、顶层重名及搜索历史重复声明；不保存请求正文、工具参数、图片或凭据，日志失败不改变执行结果。
- 轨迹恢复展示只调用工具、没有文字正文的助手轮次，保留数据库事件来源，与导出一致。
- 会话用户批量解析为平台用户名；无状态筛选时先分页再批量计算页内状态，减少列表查询成本，概览与列表筛选相互隔离。
- 优化会话列宽拖拽、紧凑分页、八位 Session ID、复制反馈及加载提示；调整日志密度和序号，统一筛选、标题间距与状态展示。
- 数据库 Head 仍为 `002`，无数据迁移；Pydantic AI 依赖、联合启动和 Jenkins 流水线不变。

## v0.9.0

- 诊断新增管理员跨用户会话概览、筛选列表及完整只读详情；统计基于现有会话和真实事件，平台用户统一展示用户名，资料不可用时回退真实 ID。
- 会话详情与对话测试共用原始页面骨架、正文和工具过程、轨迹、信息栏及展示样式；精确定位改用小标签，完整轨迹支持安全导出，不开放发送、审批或停止能力。
- 日志使用紧凑列表与左右字段详情，搜索和筛选分为两行；只保留会话关联入口，Run ID 只展示、不参与页面筛选和跳转。
- 左侧“诊断”包含日志和会话，“系统”移至底部并包含全局配置、模型目录和接口文档；补充权限、状态隔离、共享 UI、联动和真实浏览器回归。
- 数据库 Head `002`、普通 REST/SSE、执行流程、渠道回复及联合启动方式不变。

## v0.8.6

- 增加 API/企微网关统一安全诊断日志：后台有界队列、文件轮转及容量管理、等待与失败观测；日志失败不改变正常模型、工具、SSE 或会话落库行为。
- 诊断中心提供管理员日志查询、筛选、分页和字段详情；仅当前实例，不新增数据库表或会话事件。会话概览、列表及只读详情的后续设计保存在 `docs/design/23-diagnostics-and-logs/`，尚未接入正式页面。
- 会话搜索改为后端按名称模糊匹配或完整 Session ID 精确匹配，保留当前用户和 Agent 范围。
- Provider 支持删除其关联模型配置及最后一个 Provider，保留真实引用保护与原子事务；新增仅测试已开启模型的临时批量连通检查，不创建会话或保存结果，探针关闭 SDK 自动重试。
- Skill 单包下载使用编码和版本命名；新增多 ZIP 新建安装弹窗，逐包显示结果、不覆盖已有编码；请求超时和服务错误如实标记待确认，不自动重传。
- 精简并居中 MCP 编辑表单，修复 Headers JSON 选中与高亮错位并统一输入底色；内部继承检测失败时补充缺少测试 App 的配置指引。渠道 Secret 按掩码回显，留空或未修改不覆盖旧值。
- 修复模型测试切页后的状态残留，优化 Skill 上传弹窗、导航和搜索控件；数据库 Head `002`、联合启动及 Jenkins 流水线不变。

## v0.8.5

- 删除运行快照与正常终态回执，不再写入 `run/progress`、`run/terminal`；旧数据不清理、不迁移、不做兼容恢复。
- 已完成助手轮次、已接受工具调用、独立完成的工具结果及时提交；按消息及部分标识去重、归组，保留并行工具和压缩前内容。
- 半段正文仅经 SSE 展示，不定时落库；停止或异常尽量保留已有部分输出，正常结束只补尚未保存的真实内容。
- 执行异常终止幂等记录 `execution/error`，包含安全原因和阶段；主动停止、正常完成、可继续的工具失败不追加异常。
- 控制台去掉 RUN 专用展示，保留历史轮询、停止、人工操作边界和渠道累计预览/final 替换。
- 切入正在执行的会话时，通过只读 SSE 观察指定一轮；复用实时正文、思考和工具展示，终态回读历史。观察连接不执行模型，断开不取消原请求；空闲会话不保持订阅，异常回退该轮历史轮询。
- 版本升级前排空旧进程活动请求；数据库 Head `002`、联合启动和 Jenkins 流水线不变。

## v0.8.4

- 按职责统一目录命名：程序编排归入 `ptc/`，共享模型支持归入 `models/`，通用工具归入 `capabilities/general/`。
- 移除空目录、无用包标记与渠道转发模块；统一 MCP 常量、公共测试替身和文档引用。
- 将控制台历史原型移入设计文档，生产静态目录不再包含原型；增加目录结构与资源路径回归检查。
- 保持业务逻辑、REST/SSE、模型 Prompt 和数据库结构不变（Head `002`）。
- 保持联合启动入口和发布脚本不变；运维已统一的 Alpha/Test API＋企微网关启动方式无需为此版本再次调整。

## v0.8.3

- 移除根目录软链接、旧前端目录与网关兼容包；源码、控制台、MCP、迁移和部署仅保留唯一正式位置。
- 本地、测试与镜像统一显式加载 `src`，同步 Gunicorn、渠道、MCP 子进程、维护命令及 Jenkins 启动路径。
- Jenkins 在用脚本统一为网关、仅 API 和诊断三种；原始脚本归入历史归档，不再用于发布当前源码。
- 清理失效缓存、临时验证产物和历史调试备份，保留稳定数据库、附件、凭证与沙箱环境。
- 不改变业务逻辑、REST/SSE、模型 Prompt 或数据库结构（Head `002`）。

## v0.8.2

- 按用途整理源码、控制台、MCP 启动器、部署、迁移及维护目录；渠道网关实现归入渠道模块。
- 保留旧启动命令、Python 导入和检出后文件路径的兼容入口，同一份业务实现不重复复制。
- Jenkins 粘贴式流水线内容不变；SCM 轻量检出任务的 Script Path 需指向 `deploy/jenkins/`。
- 统一仓库与源码根路径，保持本地数据库、沙箱、内置 Skill、控制台 URL 和备份位置不变。
- 临时产物集中到 `tmp/`，运行数据排除版本控制；不修改业务逻辑、REST/SSE 或数据库结构（Head `002`）。

## v0.8.1

- 修复企微等待阶段隐藏累计正文：各轮公开正文按空行累加，工具执行、思考和压缩期间保留全部进度。
- 首段正文前保留原生三个点动画；有正文后追加“处理中…”文字提示，避免原生加载标记隐藏正文。
- final 全量替换为最终答案；保留停止、失败和投递异常处理，不改变历史事件或模型执行。
- 141 项渠道相关测试通过，并在本地企微验证三轮进度保留及最终替换。
- 数据库结构不变，Alembic Head 仍为 `002`；Jenkins 流水线无需调整。

## v0.8.0

- 新增同 Event Store 的运行检查点：完整阶段、已接受工具调用及结果即时提交，流式半段约 1 秒节流保存，不再等待整个 Run 结束。
- Console 刷新和另一页面打开运行会话后自动跟随持久化历史，保留滚动与折叠；运行中 UI 工具快照只读，正式待操作状态才可交互。
- 正常、停止、失败与待人工操作均原子归并正式历史并封口；压缩后的早期轮次保留，终态不与预览重复。
- 服务中断后幂等恢复已保存过程，标记未完成工具中断，不自动重跑副作用；运行归属和租约保护仍在执行的服务。
- 回归渠道累加预览/最终覆盖、工具重试失败、PTC、大结果分页、上下文压缩及历史恢复。
- 数据库结构不变，Alembic Head 仍为 `002`；Jenkins 流水线无需调整。部署仍按单 API worker 运行，检查点租约不是多副本协调方案。

## v0.7.4

- 企微过程正文按轮次换行累加预览，思考、工具执行和上下文压缩切回原生加载；完成后仅保留最后一轮答案。
- 最终回复以 `final.output` 整体覆盖，空答案、停止、失败及待人工操作均明确结束加载。
- 临时预览投递失败不打断或重跑 Agent，最终投递单独记状态；终态后的断流或迟到事件不覆盖已完成答案。
- 数据库结构不变，Alembic Head 仍为 `002`；Jenkins 流水线无需调整。

## v0.7.3

- UI 工具参数纠错上限调整为 3 次（首次调用之外），无效参数不会生成可操作卡片。
- 修复工具重试耗尽或执行异常后的失败收尾：持久化安全诊断和失败结果，关闭实时工具卡并释放会话；后续消息可继续执行。
- 兼容旧版异常补齐结果被误标成功的历史展示，不修改原始事件；正常成功和用户拒绝不受该兼容逻辑影响。
- 工具搜索以 SDK 当前可用工具为准，已失效的披露证据允许再次搜索恢复，不因累计缓存永久屏蔽；仍受当前 Agent 授权约束。
- 数据库结构不变，Alembic Head 仍为 `002`；Jenkins 流水线无需调整。

## v0.7.2

- 大窗口模型单次工具结果上限提升至 48K Token，保留窗口比例限制及未知窗口的 24K 回退。
- 扩大文件和工具结果的默认分页，增加项内字符游标，长行、长单元格及尾部回读不再遗漏内容。
- 修复 PTC 正式结果、网页正文和 XLSX 单元格的提前截断；完整结果统一交给大小治理，保留脱敏与安全上限。
- 沙箱大结果明确标识为预览；完整数据通过同次执行的 `publish_path` 显式发布，不自动重跑或保存中间数据。
- 数据库结构不变，Alembic Head 仍为 `002`；本次不改变 Jenkins 部署方式。

## v0.7.1

- 工具选择及搜索后的系统公告统一默认直接调用原生 MCP；搜索、工具发现、配对、快照及普通依赖步骤不推荐使用 PTC。
  PTC 保留给二进制传递或无需进入模型上下文的中间数据处理。
- PTC 执行错误返回官方失败 Tool Result，不消耗普通工具的 ModelRetry 预算；保留脱敏且限长的
  嵌套调用审计，实时对话与历史轨迹准确区分失败、完成和中断。
- 显式关闭 SDK 流并在退出前后捕获官方消息；模型异常、取消和客户端断开时保存已有部分历史。
  数据库写入增加超时与失败回执，落库失败不再被 flush 误判成功，也不覆盖原始错误通知。
- Console 停止操作增加有时限的后端状态核验；缺少终态但后端已空闲时恢复历史，仍在运行时允许
  重试停止，避免停止按钮一直锁住。旧 Run 的异步回调不再影响新 Run。
- 数据库结构不变，Alembic Head 仍为 `002`；本次不改变 Jenkins 部署方式。

## v0.7.0

- 修复企微引用消息丢失：文本/语音引用走现有Context Reference，图片/文件/视频/图文引用复用SDK下载和附件链路；
  引用与本次问题独立保存，命令不从引用中执行。五个命令仅识别独立纯文本，不再猜测剥离群聊@提及。
- 渠道群聊逐条、私聊每个Session仅首轮将发送者以Context Reference写入模型与历史，群Binding不随发言人变化；可信渠道
  请求追加中文会话规则，普通客户端不受影响。Agent卡片和列表标题旁显示派生的渠道关联标记。
- Binding使用`peer_metadata` JSON替代独立名称列，保存来源会话身份；私聊不依赖Runs反查用户。
  标题改为`User｜User ID`、`Group｜Chat ID`，实际有名时附名称并保留ID；保留人工改名，数据库仍为002。
- Console工具过程改为无框灰字轻量行，保留工具图标，悬停或键盘聚焦时显示展开箭头。
- 新增可选渠道网关进程；公共Gateway/ChannelService与WeCom Adapter分离，只有Adapter引用官方
  `wecom-aibot-python-sdk==1.0.2`，连接、认证、心跳、重连、事件分发、媒体下载解密和流式回复均使用SDK；
  API继续独立固定部署，未来平台复用公共逻辑但仍需新增Adapter与配置。
- Alembic`002`新增Channel三表，本轮字段精简与纠正直接合并于002，不新增迁移版本。渠道通过管理员配置
  `session_user_id`（会话用户），按`single/group`及对应User ID/Chat ID映射Session；Agent/用户切换不改写旧历史。
- 入站先认领消息台账再排队；调用前记录派发边界，避免SSE Run ID丢失时误重跑Agent。
  处理终态与投递结果分开保存，重复投递不执行第二次；文字增量、工具片段和心跳不更新台账。
- 增加渠道专用`/help`、`/new`、`/stop`、`/status`、`/whoami`；停止/新建走独立控制路径，取消此前
  排队消息并等待真实停止，15秒超时不换绑。`/new`在同一事务创建Session、来源事件、切换Binding并完成命令，
  旧历史保留，不实现`/reset`，不改变Console/DeepCube/电子表格的命令规则。
- Session取消接口支持可信渠道调用，新增`GET /api/v1/sessions/{id}/run-status`；
  校验Account与Session归属，按实际任务身份取消，保持单API worker约束。
- text、image、file、voice、video和mixed通过现有FileStore与Chat SSE进入Agent；企微只接收节流后的
  Markdown文本，不暴露thinking、工具参数或轨迹。AMR语音加入上传白名单。
- Console配置中心新增“渠道”，支持账号CRUD、启停、运行状态和连接测试；Gateway未部署或不可达时
  仍可维护配置并显示明确状态。顶部网关状态与机器人连接状态分开；快捷命令只读弹框与渠道`/help`
  共用`GET /api/v1/channel-commands`目录。
- 渠道Session通过`source=channel`标题事件保存来源快照，默认按用户/群名称或ID命名，不被首轮模型标题覆盖；
  Console列表和详情显示来源。`maintenance.channel_origins`提供默认dry-run、备份后apply的本地旧数据修复，
  保留人工标题和历史正文，不新增表或迁移版本。
- Console用户消息的附件与上下文引用置于气泡顶部，聊天和轨迹图片支持悬停预览及大图查看；
  大图仅在同一条用户消息的图片之间切换，点击空白处可关闭。
- 0.7.0固定单API worker和单Gateway副本，不实现飞书Adapter、Webhook、Lease、Outbox、主动消息、卡片Action或企微用户级平台
  身份映射；Secret按本版约定明文入库，但管理接口不返回原值，编辑留空时保持原配置。

## v0.6.8

- Console完善配置中心、会话过程展示、当前用户名称适配和MCP检测刷新反馈；本地开发与平台账号中心
  继续通过同一个`/api/v1/current-user`契约返回用户资料。
- 修复Tool Search向模型暴露不兼容参数结构导致会话执行失败的问题，保持工具发现与实际调用参数一致。
- 为内置工具、运行时系统工具、UI工具和MCP工具统一补充展示名称来源；实时SSE只额外返回
  `display_name`，History根据当前Agent工具定义动态补充，Event Payload、数据库和轨迹保持不变。
- Console普通会话工具卡使用`display_name || tool`展示，历史与实时逻辑一致；轨迹继续显示稳定协议名。
  内置`builtin__confirm`和`builtin__choice`分别补充“确认操作”和“候选选择”元数据。
- 本版本不新增数据库迁移，Alembic生产基线仍为`001`。

## v0.6.7

- 将显式编排能力包规范化为`builtin:ptc`，Console名称为“程序编排”，模型协议仍保持Harness
  `run_code`；旧`builtin:code-mode`在读取、运行和配置导入时自动兼容，新保存与导出统一写新编码，
  不增加数据库迁移。
- PTC只编排当前Agent已授权且已披露的MCP；Web Search、Web Fetch、Web Image View和Python沙箱均
  不进入PTC。Code-only `load_file_data`同时支持当前会话授权的`file_id`与公开HTTP(S) URL，原始
  Base64仅在MCP参数边界展开，不进入模型、Event、日志或轨迹。
- `execute_python`支持把当前会话授权的FileStore文件挂入一次性沙箱，并预置`deepagent_files`
  的`input_path()`与`publish_path()`；成功执行后可将零到多个产物上传为FileStore文件，模型与历史
  只接收File ID、名称、MIME、大小和SHA-256，不接收宿主路径或Base64。
- Python沙箱增加常用网络、网页、图片、图表、PDF和Office处理依赖，并默认允许访问公网；仍保留
  资源限制、Landlock、环境变量白名单和临时目录销毁，同时在Worker连接层尽力阻断回环、私网、
  链路本地和云元数据地址。`subprocess-dev`仍明确不是面向恶意代码的强隔离沙箱。
- 大工具结果阈值、Spill和Artifact外置逻辑保持不变；大工具Artifact与Python生成文件统一进入当前
  会话文件授权投影。后续Run从原始Event恢复授权，Console聊天卡与轨迹展示安全文件引用。

## v0.6.6

- 新增企业管理员接口`POST /api/v1/configuration/import`，用于无会话新环境的同Alembic Revision
  配置初始化；默认只预检，显式确认后在单事务中保留全部业务主键完成替换，不执行迁移或改标。
- 配置备份DB仅作为请求级临时文件；上传Skill通过全量导出包迁移到目标File Store并回写新File ID，
  数据库失败时回滚并尽力清理本次上传文件。
- `builtin:web`增加仅向图片输入模型披露的`web_image_view(url, force_download=false)`；工具返回
  Pydantic AI原生`ImageUrl`，由下一次模型请求按Provider能力直传URL或安全下载，不进入Code Mode。
- 显式HTTP隧道代理的fake-IP/SNI兼容同时覆盖`force_download=true`的延迟图片下载；Pydantic AI仍负责
  SSRF校验、固定公网IP和证书验证，不关闭TLS校验。
- `web_fetch`继续复用Pydantic AI的SSRF-safe下载、类型判断和长度限制；HTML转换只做一处薄适配，
  过滤`script/style`正文并在原位置保留`![alt](绝对URL)`，不自动选择或发送页面图片。
- Web图片工具结果在同一次`agent.run()`内保持原生视觉输入；Event Store只保存URL、MIME、
  `force_download`和安全审计，后续Run投影为轻量引用，SSE、History、轨迹与Console均不保存Base64。
- Console工具结果与轨迹详情按URL懒加载无Referrer预览，失败时保留原图链接。本版本不修改
  ToolOutputLimits、大工具Artifact或附件存储。
- 将已稳定的Revision 001～012折叠为唯一生产基线`001`；新环境从当前完整结构和默认数据直接
  创建。旧012库只能在停旧实例、完整备份、物理结构校验和全表内容哈希校验后执行无DDL改标。

## v0.6.5

- 新增默认不关联的`builtin:web`，固定包含`web_search`与`web_fetch`；只有Agent显式关联后才注入，
  Internal HTTP、`execute_python`联网和Monty任意Socket/Shell仍保持关闭。
- 新增全局唯一Web Search配置：明确选择当前模型原生搜索，或DeepSeek、Kimi、Tavily、Exa、
  You.com、DuckDuckGo之一；不做隐式降级，密钥读取只返回掩码与是否已配置。
- 复用Pydantic AI 2.35.1的原生WebSearch、DuckDuckGo/Tavily与SSRF-safe WebFetch，复用Harness
  0.26.0的Exa/You.com，补充DeepSeek Responses与Kimi `$web_search`薄适配。
- WebFetch兼容显式HTTP CONNECT代理的fake-IP DNS：仍由Pydantic AI校验并固定公网IP，同时把原始
  主机名传入TLS SNI；确定性网络失败返回`ToolFailed`，不再耗尽模型工具重试并中断整轮。
- 流式Run异常时保存Pydantic AI已经产生的消息、工具轨迹和Usage，并把最后一条Assistant标记为
  `failed`；只有模型尚未产生Assistant时才继续写独立`run/terminal`，History不再只剩用户问题。
- Alembic`012`增加通用`global_settings`表并初始化`web_search={"mode":"native"}`；配置备份与
  Console全局配置同步覆盖新结构，Chat、SSE、History和Event Store协议保持不变。

## v0.6.4

- Alembic `011`为`llm_models`恢复非空、默认启用的`enabled`字段，升级时现有模型全部保持启用；
  Provider仍不设置启停层，其可用性继续由类型、地址、凭据和协议配置决定。
- 普通`GET /llm-models`正式定义为当前可用模型列表；管理Console显式读取全部模型，以便停用、审计
  和重新启用。模型停用后从Agent与聊天选择器消失，但已有Agent引用保持不变。
- 新建Agent、切换模型、请求级模型覆盖和实际运行均拒绝停用模型；重新启用或手动切换前不自动替换
  已有Agent配置。被Agent引用的模型仍禁止删除。
- Provider详情恢复关联模型Switch，模型目录仍按真实模型记录判断是否已添加，不因停用生成重复配置。
  Chat、History、SSE、Event Store以及DeepCube、DeepTable客户端协议没有变化。

## v0.6.3

- Code Mode 明确区分外层普通工具与仅能在 `run_code` 内调用的 SDK 函数；手写
  `load_file_data` 目录签名由测试锁定，避免模型把代码别名误当成直接工具。
- 嵌套 MCP 返回仅对证据白名单中的稳定 Schema 生成类型；未知或第三方不可信 Schema
  退化为通用 JSON。嵌套审计继续保留在一条 `run_code` Tool Item 中，并增加总大小上限。
- 上下文预算对 DeepSeek v4 Vision 的当前轮图片按实际像素尺寸和 Provider 公式估算；其他
  Provider/模型或无法识别的图片继续使用保守固定值，不改变实际 Provider Usage。
- Console 轨迹为 `run_code` 增加独立 `CODE` 标签和可复制、语法高亮的 Python Code 页签；
  用户输入详情将附件与 Context Reference 分区展示。API、SSE、Event 与数据库仍保持不变。

## v0.6.2

- 增加默认不关联的 `builtin:code-mode` Agent 能力包，使用 Harness `CodeMode`、
  `CodeModeToolset` 与 Monty 提供 `run_code`；数据库结构仍为 Alembic `010`。
- 已授权且已披露 MCP 保留原生 Tool Schema，同时作为 Monty 函数参与多步依赖、
  并发、循环、过滤和聚合；未授权 MCP 和 Deferred/Approval/内部系统工具不进入代码环境。
- 增加 Code Mode 专属 `load_file_data`，只允许当前会话附件或大工具结果 Artifact；
  Base64 使用 Run 内不透明变量延迟到 MCP 边界生成，不进入模型、Event、History、SSE、检索和轨迹。
- `run_code` 嵌套调用只保留有界、脱敏的工具名、Call ID、状态、耗时和结果摘要；
  REST、SSE、Conversation History、Event API 与三端客户端协议不变。

## v0.6.1

- 运行时联合升级到`pydantic-ai-slim 2.35.1`、`pydantic-ai-harness 0.26.0`、`openai 3.5.0`、
  `aiohttp 3.14.3+`和`deepfos 1.1.97+`；Google Provider的代理客户端切换为Pydantic AI推荐的
  `httpx2`。本次只吸收Provider、流式、工具与Harness稳定性修复，不启用CodeMode、Shell、
  Playwright、FallbackCompaction或新的产品协议。
- 大工具结果外置使用Harness原生`json_lines` Serializer：顶层数组在平台Artifact中每条记录一行，
  模型可通过`read_tool_result`按记录分页或匹配中文关键词；小结果、文件句柄和客户端协议保持不变。
- Alembic `010`删除Provider、模型、Skill、MCP、UI工具包和UI工具上的冗余`enabled`字段；
  配置实体只要存在即可出现在管理目录，Provider的真实可用性继续由地址、凭据和协议校验决定。
- Agent的`capabilities`收敛为非空显式列表；新Agent的`capabilities/skills`均默认为空，
  不再通过`null`、预制默认值或资源自身开关隐式获得能力。资源是否进入Run只由Agent关联决定。
- Console同步删除Provider、模型、Skill、MCP、UI工具包和UI工具的启停配置、状态列与停用操作；
  内置工具目录不再展示“默认启用/关闭”。Agent的模型与工具/Skill关联成为唯一使用入口。
- Skill信息编辑弹窗改为与配置中心其他标准弹窗一致的宽度、标题和表单布局。
- Chat、History、SSE、Session Event、Plan、Deferred、附件和外部客户端协议均不变。

## v0.6.0

- 将旧 `chat_messages` 与独立上下文投影重构为 `chat_session_events`：每个完整 Pydantic AI
  `ModelRequest/ModelResponse` 一行；普通 Run 不再单独落库，只有无 Assistant Response 的异常
  终态写 `run/terminal`，压缩使用同一 Event ID 原位更新。会话历史、模型恢复、SSE、Plan 和
  Console 轨迹统一从 Event Store 投影。
- Alembic `008` 是会话域破坏性迁移：删除旧会话历史与旧上下文投影，重建全局唯一 Session、
  严格 Sequence、Turn/Run 定位和可空 App/Space 来源；Call/Result 通过完整 Message Payload 内的
  Call ID 精确配对。Provider、Model、Agent、
  MCP、Skill、UI Tool 配置保持不变；升级前必须分别执行配置备份和会话历史归档。
- Alembic `009` 删除已废弃的标题专用模型配置表，并兼容开发阶段 Revision 008 中该表已存在或
  已被手动清理的两种物理状态；产品版本仍为 0.6.0。
- 上下文压缩改为单条 Compaction Event，采用固定八段结构摘要、确定性的 Tool/Context Evidence、
  DSH 式超大工具结果头尾裁剪和来源 Hash 校验；失败、超时、取消和 Stale 不替换上一次有效历史。
- 摘要总超时提高到 300 秒，并每约 10 秒发送非持久化压缩进度；明确的 Provider Context Overflow
  在没有已输出文本或工具副作用时压缩后安全重试一次，其他失败不重复发送同一大请求。
- 增加 `search_conversation_history` 与 `read_conversation_event` 两个只读系统工具，支持权限内中文、
  英文、路径、Tool Name、Call ID 和稳定标识检索；完整 Artifact 继续由 `read_tool_result` 分页读取。
- Harness Planning 只暴露 `write_plan` 完整快照；Deferred 新 Run 通过共同 `turn_id` 恢复本 Turn
  最近成功计划，新用户 Turn 不注入旧计划。
- 会话标题改为与首轮正文并行的独立模型调用，复用该轮实际生效的Provider与模型；兜底、模型和
  手动标题都写入`session/title` Event。删除标题专用模型配置、自动重新生成、刷新按钮和客户端轮询。
- Console 普通聊天改读 `/history` 的 Conversation 投影，只有轨迹读取 Event API；实时会话使用
  Text/Thinking/Tool Delta 与 `final`，不再重复发送持久化 Conversation Item 通知。Compaction 按
  Sequence 和 Event ID 原位展示；同时合入 Provider/Skill 等配置详情的紧凑布局。本版本不恢复旧会话，
  也不实现 Branch。

## v0.5.8

- 完善纯本地SQLite调试：仓库外Profile可显式开启仅限回环`local_dev`进程内部ASGI scope的
  Console管理鉴权旁路，无需平台Token、App或Space即可维护Provider、Model、Agent并执行基础对话。
- 本地Profile配置改为覆盖同名环境变量，避免终端残留的Test/平台配置污染SQLite启动；未开启旁路时
  仍保持原有可信Token校验，部署入口和外部HTTP Header均无法触发本地放行。
- 平台文件下载、上传Skill、远程MCP及其他依赖App/Space继承上下文的能力仍需平台身份。本版本不新增
  数据库迁移；Provider + Model两层结构继续使用Alembic `007`。

## v0.5.7

- 收紧Console的Provider详情信息密度：Base URL使用单行图标事实，地址模式、Key和启停状态
  改为紧凑胶囊，运行协议并入Provider副标题，降低关联模型区域上方的占用高度。
- 本版本不新增数据库迁移，不改变Provider、Model、Chat、SSE、History或外部客户端协议；
  Provider + Model两层结构继续使用Alembic `007`。

## v0.5.6

- 完成Provider + Model两层数据归一化：`llm_providers`独占适配类型、Base URL和API Key，
  `llm_models`删除重复连接字段，并通过`RESTRICT`外键关联Provider；模型名称唯一性收敛到Provider内。
- Provider与Model保留独立启停状态，有效可用性由两层状态、Provider密钥和地址共同决定；模型响应新增
  `selectable`与脱敏`unavailable_reason`，`GET /llm-models?selectable_only=true`可只返回可选模型。
- 清除Provider密钥不再批量改写子模型状态，补回密钥或重新启用Provider后原有模型自动恢复可选；
  Agent创建、Chat、Session覆盖、标题生成和上下文估算统一执行显式Provider校验；压缩固定复用当前Agent已校验的主模型。
- 模型创建、更新和删除统一进入同一配置Service事务，删除时原子检查Agent引用；列表批量
  读取Provider和Agent计数，避免逐模型查询。007升级、降级和配置备份均适配最终两层结构。
- Console增加Provider启停配置并统一按有效可用性禁用模型选项。Agent、DeepCube助手和电子表格助手
  继续发送`llm_model.id`作为`model_id`，原有模型核心字段、Chat、SSE和History协议保持兼容。

## v0.5.5

- 新增独立Provider配置层和Provider分栏Console，开始将适配类型、明确Base URL和API Key从模型
  配置中抽离；Provider详情按固定父子关系维护模型。
- 首批支持DeepSeek、MoonshotAI、Alibaba、Z.AI、OpenRouter、OpenAI、Anthropic、Google和
  OpenAI Compatible九类Pydantic AI原生适配；默认地址与自定义地址使用显式开关，不以空值猜测语义。
- 模型管理改为选择Provider，模型响应继续保留`id/name/model/input_modalities`和`model_id`协议，
  Agent、Chat、Session、History、SSE、Planning、Deferred与外部客户端无需改变。
- Console增加Provider分栏管理，并将Agent和聊天模型选择器按Provider分组；模型目录仍只
  预填模型能力，不保存或推断本地Provider凭据。
- MCP小目录直接披露上限调整为10个：全局候选不超过10个时完整披露，超过10个时继续使用
  首轮词法Top 3与模型主动搜索Top 5。

## v0.5.4

- 收敛Console会话轨迹详情展示：Assistant的Thinking归入Preview首段并保持默认折叠，
  短正文按内容自适应，超过高度上限后再滚动。
- Tool、Planning和Deferred复用同一套Payload/Result摘要渲染与类型分发，减少重复前端逻辑；
  REST、SSE、History、数据库与轨迹投影语义均不变化，Alembic Head仍为`006`。

## v0.5.3

- Console 对话测试新增“对话 / 轨迹”分段切换；轨迹按 Pydantic AI Run 归组展示
  用户输入、助手Loop、工具调用/结果、Planning、Deferred、压缩与 Checkpoint。每个助手Loop合并
  同一次模型响应的思考、正文和工具意图，独立工具记录仍用于检查参数、结果和错误。
- History 安全投影补充已持久化的模型名、Provider、Response ID、Finish Reason、Usage 和工具
  Outcome；不返回 Provider URL、私有详情、认证 Header、Token、Cookie 或系统提示词。
- 用户输入轨迹会标记History中已有的结构化Context Reference与附件；轨迹支持顺序/调用
  筛选、搜索、复用会话详情栏的类型化详情和脱敏JSON导出；
  默认仍显示原对话，REST、SSE、模型上下文和数据库结构均不变化，Alembic Head仍为`006`。
- History投影补充数据库中已有的ModelRequest/ModelResponse与ToolReturn时间；记录按已有时间与消息顺序展示，
  不用缺少结束时间的历史数据拼接甘特图或时间轴，也不伪造逐Part耗时。
- 结构化Context Reference除保留在User摘要外，也作为独立`CONTEXT`事件进入同一Run；User和
  Assistant详情提供Markdown Preview与Raw，工具详情按需提供Payload与Result，JSON统一使用语法高亮。

## v0.5.1

- Console抽出共享Markdown渲染链路，Skill的Markdown资源不再按纯文本显示，改为适合小字号阅读的标题、列表、表格、引用和代码样式。
- 主对话支持`mermaid`围栏代码；仅在完整回复落定后按需加载本地Mermaid资源并生成SVG，流式输出过程不反复重绘。
- Mermaid固定使用严格安全模式，渲染失败时保留原始围栏源码并显示轻量提示；REST、SSE、History和Alembic Head `006`均不变化。

## v0.5.0

- 重构一体化 Console 外壳：三个主页面改用统一顶栏和左侧图标导航，产品版本与数据库结构
  归入用户信息；工作台支持会话、Agent和详情栏调宽，窄屏继续使用抽屉退化。
- 配置中心改为连续轻列表与分栏详情：Agent支持卡片/列表双视图和独立编辑页，Skill及UI工具
  使用左右分栏；所有原有CRUD和Skill资源预览继续保留，UI工具直接展示当前包的完整工具列表。
- 模型目录保留联网更新，并适配v0.4.20新增的JSON导入、导出；删除冗余的仅重读数据库按钮。
- 删除、离开未保存页面等二次确认统一使用轻量语义弹层；Skill信息编辑也合并为同一套输入弹层，
  Console不再调用浏览器原生`alert`、`confirm`或`prompt`。
- Agent列表只在首次加载时展示占位；再次切回Agent目录时保留现有卡片或列表，数据返回后原地刷新，
  避免短暂清空内容引起的闪动和布局跳变。
- 上传Skill详情只保留“编辑、上传”两个高频操作，导出、重新加载、启停和删除收纳到无外框三点菜单，
  降低标题栏密度并隔离危险操作。
- 本轮只调整bundled Console与说明文档；REST、SSE、History和数据库结构均不变化，Alembic
  Head保持`006`。

## v0.4.20

- 模型目录增加models.dev官方`catalog.json`离线导入，用于部署环境无法访问models.dev时由
  管理员手工更新。
- 增加当前数据库模型目录JSON导出，导出的`models/providers/labs`可原样再导入；
  Console删除仅重读数据库的冗余“重新加载”按钮。
- 联网刷新与JSON导入复用同一份UTF-8解析、Provider白名单、标准化和完整校验；
  只有全部通过后才原子替换数据库目录，错误文件不会破坏旧数据。
- 导入只更新用于浏览与预填的`model_catalog`，不修改已保存的`llm_models`运行配置；
  Alembic Head保持`006`。

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
