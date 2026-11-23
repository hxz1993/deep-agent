# 可持续修改的中间产物：产品与框架再核验

> 核验日期：2026-11-07。只读官方文档与部分公开源码，未登录这些产品做操作验收，也未安装或升级框架。
> 本文补充[原交接](sources/dot-artifact-handoff-20261106.md)，不重复展开其中全部框架清单。产品体验、存储原语和同步协议分开比较。

## 结论

方向有成熟先例，但没有发现一个经本轮核验、可直接替换 DeepAgent 全部业务产物需求的组件。

- **看用户体验**：Pigment 的 Report、Claude Artifacts、Deepnote Notebook 都让人围绕同一份内容继续修改，而不是把聊天正文当最终事实源。
- **看服务边界**：Google ADK 把产物服务与 session state 分开，而且是可选依赖；这最贴近“装上可用，不装仍能聊天”。
- **看模型增量**：Trustcall 和 Mastra 展示了模型更新已有结构化内容的方法；两者不自动提供我们的业务权限、修订冲突与执行回执。
- **看前端同步**：AG-UI 有快照＋JSON Patch 增量的正式约定；它解决传输与页面状态同步，不代替权威存储。

上述组合是本项目的设计归纳，不是这些厂商使用相同内部架构的证据。建议借鉴职责和契约，先用现有 Python／Pydantic AI 接点实现可选模块，不为此换运行框架。

## 产品：共同维护一份可见的内容

| **产品与资料** | **本轮能确认的做法** | **可借鉴之处** | **不能由此推导** |
| --- | --- | --- | --- |
| [Pigment Analyst](https://kb.pigment.com/docs/configure-and-run-the-conversational-analyst-agent)、[报告编辑](https://kb.pigment.com/docs/edit-analyst-agent-reports) | 先通过对话形成 Report，再选择保存为 Mission；聊天报告可继续提示修改，Mission 报告支持直接编辑、保存及分享／导出 | 区分分析要求、执行过程和本次 Output；报告可修改并由业务页面采用 | 未公开内部 JSON、查询计划或数据库并发协议；也不代表所有聊天报告已有同样的直接编辑能力 |
| [Claude Artifacts](https://support.claude.com/en/articles/17153992-what-are-artifacts-and-how-do-i-use-them) | 内容在对话旁独立打开，可继续提示修改；模板内容支持直接编辑；可从产物入口找回和分享／导出 | 内容作为独立对象存在，对话围绕它进行；交互不必强制套成 Mission | 当前模板与旧版 Artifacts 有差别；官方说明没有证明本项目需要的 typed JSON API、CAS、领域校验或确认协议 |
| [Deepnote Agent](https://deepnote.com/docs/deepnote-agent) | Notebook 是持续编辑的对象；Ask／Edit 分开；Agent 可增删改 block、执行和检查结果，展示修改并接受／丢弃或继续提要求 | 把内容、执行结果、对话和变更审阅放在同一个工作区；适合开放报告／分析 | Notebook 不是合同或 BOM 的通用业务对象；不能把其全部计算和权限体系视作可直接嵌入 |

### Pigment 对本项目最重要的两点

第一，保存 Mission 是在已有对话和 Report 基础上的可选复用步骤，并非所有分析的前置条件。第二，报告编辑与未来 Mission 要求分开；修改本次结果不自动等于改变以后每次运行的方法。产品细节继续归[原 Pigment 专题](pigment-and-prototype-research.md)，本页只保留比较。

其公开资料能证明产品体验，不能回答“内部是不是一个 JSON＋Python＋HTML”。对数据库、版本冲突和 schema 的设计仍要自己明确。

### Claude 的新旧能力不要混写

本次官方帮助页将 2026-09-16 以前创建的产物称为 legacy artifacts，并单列新模板与旧版的操作。研究只采用当前文档可见的独立内容、持续编辑和导出模式，不继续把旧截图中的版本下拉等交互当作今天全部产物的统一契约。

这里没有要求 DeepAgent 复制整套画布、模板或代码执行体系；我们的合同／BOM 页面已有专业 UI，首先需要的是它和 Agent 共同操作一份当前内容。

## Deepnote 作为报告产品方向的补充（2026-11-07）

用户实际试用后认为上手简单、接近期望的报告制作方式，本次明确把它保留为产品方向参考，不表示已决定采用其源码或替换当前平台。

它从协作计算笔记发展而来：2020-10 推出 Beta，2022 正式发布，早期重点是协作、连接数据、可复算和效率。[历史定位](https://deepnote.com/blog/out-of-beta)
当前官网定位为人与 Agent 共用的数据工作区，Notebook 组合查询、代码、输入、图表、说明及 Agent 步骤；看板和经营快报是交付形式之一。[当前定位](https://deepnote.com/)、[Notebook 组成](https://deepnote.com/docs/notebooks)

对本项目的设计归纳：可考虑用“按块组织、可以执行的分析文档”承载报告制作过程。用户与 Agent 围绕同一文档调整查询、计算和解释，最终 HTML／PDF 保留为输出；具体采用哪种画布、block 或执行协议尚未选择。报告编辑器属于上层产品，底层可选产物模块不因此强制采用 Notebook。

| **商业信息** | **核验范围** |
| --- | --- |
| 融资 | 2022-01 官方宣布 2,000 万美元 A 轮，Accel／Index Ventures 领投；本轮未找到可靠的新融资公告。[官方公告](https://deepnote.com/blog/series-a) |
| 营收 | 2024-07 公司新闻稿称过去一年收入达到约 3 倍，未披露绝对值；本轮未取得可靠的 2025／2026 营收、ARR 或盈利数据。[公司新闻稿](https://www.businesswire.com/news/home/20240729061332/en/) |
| 采用情况 | 官网宣称超过 60 万数据专业人员使用，并列企业案例；这不是付费客户数或收入证明。[客户页](https://deepnote.com/customers) |
| 商业动作 | 2024 宣布收购 Hyperquery；商业版本继续提供团队与企业服务。[收购公告](https://deepnote.com/blog/deepnote-acquires-hyperquery)、[价格](https://deepnote.com/pricing) |

商业判断：持续分析文档的产品价值有参考意义；其独立商业上限、增长质量仍缺充分公开数据，不能凭试用简单就判定没有潜力，也不能凭使用人数推算收入。我们优先借鉴报告制作方式，结合自身财务口径、模型和业务流程，不照搬整个通用数据平台。

## 框架与协议：各自只补一段

| **项目** | **可核验原语** | **适合借鉴的部分** | **还缺什么** |
| --- | --- | --- | --- |
| [ADK ArtifactService](https://adk.dev/artifacts/) | Runner 接入独立产物服务，按具名内容保存和加载；支持 session／user 范围，同名保存产生版本 | 服务与运行解耦；存储实现可替换；未提供服务时不应给 Agent 暴露依赖它的工具 | 主要管理文件／blob；未提供我们的字段 patch、业务 schema 和调用者基准修订条件 |
| [Mastra Working Memory](https://mastra.ai/docs/memory/working-memory) | Markdown 或 schema 结构；结构化更新使用对象递归合并，数组整体替换，文档规定 null 删除；支持 thread／resource 范围 | Agent 只提交变化字段，宿主持久保存；记忆可明确启用及限定范围 | 记忆语义不能直接当业务采用协议；并非所有存储／作用域都支持原子 merge；null 删除不能照搬到业务可空字段 |
| [Trustcall](https://github.com/hinthornw/trustcall/blob/main/trustcall/_base.py)、[验证节点](https://github.com/hinthornw/trustcall/blob/main/trustcall/_validation_node.py) | `PatchDoc` 对已有 JSON 提交增量；代码应用 patch，并通过 schema 验证及纠错 | 证明“模型改局部、程序合并和验证”已有真实实现 | 本轮未看到该组件提供完整产物 CRUD、租户权限、数据库条件更新或业务执行层；不能把验证重试当保存成功 |
| [AG-UI State](https://docs.ag-ui.com/concepts/state)、[事件契约](https://github.com/ag-ui-protocol/ag-ui/blob/main/docs/concepts/events.mdx) | `STATE_SNAPSHOT` 建立基线，`STATE_DELTA` 传递 JSON Patch；客户端按序应用，失配时重新取快照 | 页面先有完整内容，后续只处理变化，断连后重新同步 | 同步事件本身不提供数据库、对象授权或陈旧写入保护；不能让客户端状态直接覆盖服务端权威内容 |

### ADK：可选服务不等于失败时悄悄忽略

ADK 文档明确要求在 Runner 提供 ArtifactService 后才能调用上下文中的产物方法；未配置却调用会报错。它证明的是**可插入独立服务**，并不自动保证错误工具也能无感工作。

DeepAgent 因此要同时做两件事：服务可选、工具装配也受控。未启用时不暴露产物工具；启用但存储失败时明确报错，不能只把 JSON 放进聊天就声称保存完成。是否提供只读关闭态另行设计。

### Mastra：合并语义与原子性有条件

当前文档的 `mergeWorkingMemory` 原子合并要求 resource scope 及支持该能力的适配器，列出的实现为 PostgreSQL；不满足时会报错，并有能力探测方法。不能因此宣称所有 Working Memory 更新都有同一并发保证。

原交接记录了更细的历史源码观察。本轮没有逐行复验全部适配器，因此不把其当作固定发行版承诺。我们继续采用已确认的受控 patch＋base_revision，避免混用“缺失”“null”“删除”和数组替换的不同含义。

### Trustcall：借鉴更新过程，不引入另一套运行框架

本轮在公开 `main` 源码重新确认了 `PatchDoc`、`jsonpatch.apply_patch` 和验证节点。公开源码是动态分支，未安装固定发行版做集成；不用它证明事务、幂等或多端权限。DeepAgent 可在当前工具实现中借鉴同样的“修改请求→合并→完整校验”顺序，无需把 LangGraph 作为前置依赖。

### AG-UI：最接近前端协同，但不是必须接入的协议

它给出的快照／增量分工很适合专业页面，但数据库权威内容、授权与 revision 仍需后端实现。若以后有前端高频同步需要，可以映射到这种模式；首版正常 API 读取／保存、保存后刷新就可闭环，不必更换现有 SSE 及历史协议。

## Pydantic AI 是否妨碍做成可选

没有发现必须迁移框架的理由。[官方 Toolsets 文档](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/)提供工具集合组合与动态选择原语；本地当前 `factory.py` 已按能力组装 toolset，可在现有入口接可选产物服务。

官方最新示例可能超出仓库锁定版本，不能把官网函数签名视作本机已经可用。本轮结论仅依赖已核对的本地装配边界，不要求依赖升级。Conversation／运行持久化不直接解决领域对象的身份、patch、条件保存和专业校验，这一段仍应是应用自己的模块。

## 对原交接的补充与保留

| **项目** | **处理** |
| --- | --- |
| 原生 JSON、服务端 patch、可只保留最新内容 | 保留已确认方向；并发修订不等于完整版本库 |
| Mission／任务容器 | 仍为开放选项，不能因 Pigment 有 Mission 就让所有产物依赖它 |
| 图形工作流产品与报告执行组件 | 原文已列 Sim、n8n、Windmill、Hex、Quarto 等；本轮不逐个重复研究，不把它们全部登记为已复核 |
| 原文客户案例源码及旧版本行号 | 保留历史快照，本轮没有逐个重新运行或验收 |
| 新增重点 | 产品持续编辑体验、可选服务与工具装配的双重门禁、AG-UI 同步边界、关闭与故障隔离 |

具体建议见[可选模块设计](optional-artifact-module-design.md)。本轮没有商业产品登录实测、性能基准或生产存储试验，研究通过不等同于可立即上线。
