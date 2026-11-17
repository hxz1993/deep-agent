# MCP、CLI 与 Python SDK 实施和验收

> **0.17.0a4 发布状态**　正式启用 A2A 和 MCP。本文中的 CLI、SDK、通用 HTTP 调用和安装包分发均为保留的开发草案，未在产品注册，后续可能改写或移除。MCP 的鉴权取件路径继续开放。

[接入契约与 Console](server-entrypoints.md) · [A2A 一期实测](server-validation.md)。本页用于二期一次交付三种接入。A2A 继续使用项目已锁定的官方 SDK。三种新增入口上线时共用现有发布、任务、执行器和文件权限，沿用数据库 Revision 016。

## 实现顺序

| **步骤** | **实现内容** | **完成条件** |
| --- | --- | --- |
| 1. 共用服务 | 从当前 A2A Handler 提取发布查验、任务受理、所有者校验、任务状态查询与文件授权。A2A 保留原 Card、双路由、SSE、官方错误及附件行为 | 先跑现有 A2A 集成套件，利用同一份 TaskStore 和授权数据校验结果等价 |
| 2. 公共 HTTP | 按[接口表](server-entrypoints.md#工具对照)实现目录、提交、查任务、列表、补答、取消、下载。目录使用单条启用状态查询，只投影公开字段。Task ID 操作先按当前主体和 App/Space 找任务，再按任务保存的发布 UUID 校验当前发布及 Scope | HTTP 与 A2A 互查同一 Task，另一位有权访问该 App 的用户也只能访问自己的任务。已有任务按原发布启停及撤权规则处理 |
| 3. MCP | 在同一 API 进程挂载 `/api/v1/agent-service/mcp`，使用仓库已有的 Python MCP SDK，采用 Streamable HTTP 的普通工具调用。每个请求从 HTTP Header 获取身份。MCP 工具直接调用共享服务 | 原生 SDK 与桌面客户端均能初始化、发现和实际执行。检查认证中间件、挂载生命周期、网关前缀及带 Header 的请求 |
| 4. 客户端制品 | 独立构建 `deepfos-agent-client` wheel，包含 Python SDK、`deepfos-agent` CLI 与静态调用 Skill。CLI 负责参数、机器输出、安装和退出码，HTTP 请求由 SDK 统一发出 | 在仓库外干净环境从同一个 wheel 装 CLI 和 SDK，用实际服务执行完整任务。保留离线 wheel 分发路径 |
| 5. Console | 发布页三个页签按环境展示，补充 MCP 连接配置、CLI 的两个安装入口、SDK 安装与快速开始。近期任务展示服务端接入方式，HTTP 客户端标识仅作为诊断信息 | 切换发布时三页内容稳定，核对复制的 URL、版本、Header 名和安装文本。浏览器检查桌面、窄屏、键盘和暗色 |
| 6. 回归 | 先跑隔离集成，再跑真实平台和客户端，最后运行 `scripts/run_tests.py --suite all` | 每条真实成功记录客户端版本、任务 ID、结果、文件摘要及可复现步骤。保留一组本地验收任务供 Console 核对 |

任务所属由发布 UUID、经平台核验的主体、App、Space 共同决定。提交使用调用端生成并保留的 `message_id` 做幂等。同一个消息 ID 和同一内容重试返回原 Task，内容有变化时返回冲突。一个 Context 表示一个内部会话，补材料沿用原 Task 和 `question_id`，任务完成后的追问使用原 Context 创建下一 Task。CLI 等待超时或 Ctrl+C 只结束本地观察。

会话执行需要核对 `execution.py` 中的 Session 绑定、普通 Chat 的只读保护、集成诊断的 Session 查询和 Console 来源显示。任务或会话开始渠道可分别标注 A2A、MCP、HTTP；同一 Task 的后续查询渠道不能覆盖任务的发起渠道。若新渠道写入新的会话来源值，所有禁止普通 Chat 续跑的入口要同时识别。阶段实施基于现有 Session 字段和任务内存字段。

## 统一客户端约定

- SDK 保持一套环境级配置，支持默认 App 和按请求覆盖。使用环境提供的 API Key 或 Token。同源下载，具有大小边界和超时，拒绝把凭据转发到其他主机。应用侧可发起取消，关闭 SDK 连接只停止本地网络操作。
- CLI 支持 `--json`，stdout 输出一个 JSON 对象，状态和诊断走 stderr。参数错误、鉴权失败、等待超时及业务失败使用不同退出码。需要补答时带回 Task ID、问题标识和可执行的下一步。响应丢失时输出可供恢复的 `message_id`。下载先写临时文件，按返回长度和可用摘要校验后原子保存，默认保留同名文件。
- 同步适配由 CLI 进程自行组织，Python SDK 首版提供 async 客户端。SDK 核心采用轻量 HTTP 依赖，安装包无需 DeepAgent 服务、模型运行时或 A2A SDK。
- MCP 使用 [输入 Schema](mcp-tool-inputs.json)，`tools/list` 返回稳定的一组工具，`list_agents()` 返回所有已启用发布。任务业务失败由任务状态表达，参数和访问错误使用 MCP 工具错误。工具结果以 `structuredContent` 为主，同时提供兼容只读取文本的宿主的 JSON 文本。
- 输入文件采用 `attachments[]` 内联字节表示，由 CLI 或 SDK 本地读取。共用文件类型、数量和总量校验。三种服务端入口统一限制附件总量为 8 MiB，并在请求体读取阶段限制 JSON 至 11,446,954 字节。部署代理需容纳 Base64 和 JSON 包装，建议请求体上限至少 11 MiB。MCP 宿主未提供附件字节时，可通过 CLI 或 SDK 上传并完成同一 Task。
- 文件产物来自可信的执行事件。MCP 默认返回完整鉴权下载 URL，至多 20 MiB 可用 `get_artifact(format="base64")` 从文本 JSON 取得原字节。验收区分「看到了文件名」「客户端拿到文件字节」「保存并打开成功」。

## 客户端验收

| **客户端** | **操作与成功证据** |
| --- | --- |
| WorkBuddy 桌面 | 在「连接器」添加自定义 Streamable HTTP，URL 指向 8002 的 MCP 端点，用宿主配置 `app` 及 `X-Deepfos-Key`。先记录连接及工具发现，再让 WorkBuddy 原生 MCP 发起任务。如果桌面端只能完成配置／握手，继续用 CodeBuddy 原生 MCP 完成任务并记录两个产品各自结果。 [WorkBuddy MCP 指南](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/MCP-Guide) |
| CodeBuddy 原生 MCP | 临时 MCP JSON 使用 `type: http`、同一个 URL、`app` 和凭据环境变量引用，启动时以 `--mcp-config` 指向该文件。让模型调用 `list_agents`、`submit_task`、`get_task`。验收记录原生 Tool Call、Task ID 和正确结果，服务端可对应，纯终端 HTTP 调用只用于诊断。 [CodeBuddy MCP 文档](https://www.codebuddy.ai/docs/cli/mcp) |
| Python SDK 与 CLI | 以测试 Agent 生成实际预算差异表，完成目录、提交、待补材料、续跑、取 XLSX 和校验内容。CLI 补测退出码、JSON 输出、超时恢复、独立 PATH。SDK 补测连接关闭、并发和按次替换 App |
| CLI 加 Skill | 在临时 CodeBuddy 项目中粘贴 Console 的安装指令，使用构建好的 wheel 安装 CLI，将 Skill 落到项目 `.codebuddy/skills/deepfos-agent-call/`。新会话验证 Skill 被发现并实际指导终端执行 `deepfos-agent`，最后拿到同一端点的任务结果。为这个用例单独启动 CodeBuddy，关闭测试 MCP，避免模型换入口。 [CodeBuddy Skills](https://www.codebuddy.ai/docs/cli/skills) |

桌面端的回环地址必须从实际运行 MCP 的客户端进程可达。若所选模式将执行放到云端，换用在本机运行的模式，或已有团队允许访问的测试端点。使用已授权的本地配置和隔离的测试 Agent，保留任务和诊断 ID。客户端凭据、VPN、包仓、权限弹框或网络有阻塞时记录具体证据并继续其他项目；任务通过需要真实结果。

支持 JSON 配置及环境变量展开的 CodeBuddy 可使用以下配置。WorkBuddy 界面若提供独立的连接字段，填同样的地址和 Header，由宿主管理凭据。

```json
{
  "mcpServers": {
    "deepagent-local": {
      "type": "http",
      "url": "http://127.0.0.1:8002/api/v1/agent-service/mcp",
      "headers": {
        "app": "${DEEPFOS_AGENT_APP}",
        "X-Deepfos-Key": "${DEEPFOS_AGENT_API_KEY}"
      }
    }
  }
}
```

无企业包源的本地验收直接安装本轮构建的 wheel，核对包版本和文件摘要。控制台正式生成的安装文案以实际制品地址为准。Agent 安装指令先检查 uv 和当前宿主的 Skill 目录，CLI 安装完成后从同一 wheel 安装 Skill，随后用新会话验收。

## 回归矩阵和风险

| **风险** | **验证方式** |
| --- | --- |
| 目录泄露内部信息或越权执行 | 两个不同普通用户和 App 获取一致的已启用目录，核对只返回三字段。分别覆盖无凭据、无权 App、Scope 越界、停用、新旧任务、撤权和跨应用取件 |
| 共用逻辑改坏一期 | 用官方 A2A SDK 重跑通用及带 App 两路、六个方法、SSE 断开后继续执行、并发取消、幂等、补答、同 Context 追问、图片/文本附件及 XLSX；复验 Inspector、既有 Langflow 和 CodeBuddy A2A 用例。在线 Playground 仍需要已批准的可达测试入口 |
| 新渠道写出的会话绕过禁续跑或丢失诊断 | 由 A2A 提交，用 HTTP/MCP 回查，反向取件、补答和取消。验证 Console 任务、会话详情及 Turn 跳转，尝试通过普通 Chat 续跑应被拒绝 |
| 任务存储随进程清空 | 在隔离运行实例验证跨入口可见、部署多个进程时的同实例路由，以及进程重启后 Task 过期、历史仍可查看 |
| 分发与宿主兼容性 | 用真实 wheel 安装，检查企业包源或离线制品，使用受支持的最低 Python 和当前本地版本验证。安装指令只使用版本和凭据来源名。对已存在的同名 Skill 核对版本后更新，保留用户自己的内容 |

本轮预检使用本机已安装的 WorkBuddy 5.11.3、CodeBuddy Code 2.183.0、uv 0.10.11 与 Python MCP SDK 1.30.0。8002 健康检查通过。官方 MCP Python 客户端已在隔离回环服务完成带 App 和 Key 的连接、工具发现及调用。CodeBuddy 的启动事件报告探针 MCP 已连接，服务端收到两个 Header，本次模型未调用探针工具。项目级探针 Skill 在 CodeBuddy 中已触发并返回预期标记。二期真实端点、WorkBuddy 桌面配置、原生业务任务和正式 wheel 安装待开发验收。当前 A2A 集成 13 项、Console 定向 17 项及 8002 浏览器冒烟通过。

## 二期本地验收记录

| 入口 | 证据 |
| --- | --- |
| CodeBuddy Code 2.183.0 原生 MCP | 在 8002 连续调用 `list_agents`、`submit_task`、`get_task`。任务 `ebe5ec2305974c0ca2b005575ec9fd07` 返回差额 20。 |
| CodeBuddy CLI 与 Skill | 在独立项目安装同一个 wheel 和项目级 Skill。新会话加载 Skill 后执行 CLI，任务 `babfbeb1a53a4e7b86f196f31592aa96` 返回差额 20。 |
| Python SDK，Python 3.10.20 | 8002 任务 `8236b0e59a914e5c91591da3904b0090` 接收文本附件，停在待补材料。同一 Task 续跑后下载 XLSX，并核对 `C2=20`。 |
| 跨入口 | A2A 提交后由 MCP 回查。MCP 提交任务 `5208fcb1e0784de7b8bf539383aa9af1`，由 SDK 补答和下载 XLSX。另用 MCP `resources/read` 取件，验证字节与 SDK 下载一致。在 Console 验证任务关联至诊断会话和具体提问。 |
| 一期兼容 | 8002 官方 SDK 复验六个方法，订阅及取消任务 `5d923737af17437b8530e894c40adce5`。Inspector 用带 App 的 Card 流式返回 20。Langflow 通过现有本地适配器复验图片、同 Task 补材料、同 Context 追问及 XLSX 下载，文件任务 `2414519333ae45e49796ee2ab8d63791`。CodeBuddy A2A 通过本地适配器返回 20。在线 A²D Playground 用隔离样例通过普通发送和流式发送，临时 HTTPS 入口已关闭。 |
| WorkBuddy 5.11.3 | 确认桌面自定义连接器入口，未取得连接和任务执行证据。原生 MCP 任务通过 CodeBuddy 验收。 |
| 重启与过期 | 隔离实例重启，旧 Task 返回 404，两份会话仍保留。集成测试覆盖终态过期后 HTTP 和 MCP 查询。 |
| 全量回归 | `scripts/run_tests.py --suite all` 复跑通过。Python 共 3,610 项，跳过 66 项，无失败或错误。Console 共 1,183 项，全部通过。首轮的 3 项错误均通过定向复跑和全量复跑。 |

本地 wheel 版本 `0.17.0a3`。SHA-256 `a30fa87ccf6c40e7e2647b8dcd393bce3a66ccf5880ba750384e0dd57fd1617b`。8002 下载与本地文件摘要一致。完整安装方式见[集成服务指南](../../guides/agent-service.md#mcpcli-和-python-sdk)。
