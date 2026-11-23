# 开发助手与资料发行

Console 的“开发调试”弹框提供初始化 PDF 和一份 `deepagent-agents-dev` 开发 Skill。PDF 面向实施人员；Skill 安装到 Coding Agent，负责业务配置、接口联调、脚本、原生输出、电子表格和会话诊断，有对应源码时再使用后端/前端开发资料。

使用顺序为：下载 Skill ZIP，附给 Coding Agent，再复制页面提示词。提示词仅指向包内 README，并提供当前服务根地址，引导安装后检查连接和权限；安装、权限核对、按需阅读和验收步骤由包内 README 与 SKILL.md 维护，不在页面重复。提示词不带下载链接或认证信息。Coding 工具用用户提供的私有配置访问目标环境；本机已启用 trusted local-dev 身份时可直接验证当前权限。`localhost` 必须是实际执行命令的电脑，不能假定云执行器能访问它。

旧 `#/developer` 地址在工作台上打开同一弹框；普通导航入口不切换页面。关闭弹框中止资料读取和未完成下载，不影响后台聊天流。

## 管理接口

两个路由都使用 `CONSOLE_ADMIN_DEPENDENCIES`，不新建权限或签发 Key：

| 接口 | 返回 |
| --- | --- |
| `GET /api/v1/developer-resources` | `{items: [...]}`，两项固定资料 |
| `GET /api/v1/developer-resources/{code}/download` | PDF/ZIP 字节、实际 MIME 和下载文件名 |

固定编码是 `initialization-guide`、`development-skill`。每项有 `name`、`filename`、`media_type`、`version`、`size_bytes`、`sha256`、`download_url`，PDF 另有 `page_count`，Skill 另有 `document_count`（Markdown 文档数，不包括诊断脚本）。`download_url` 相对服务根、不以 `/` 开头，Console 通过 `serviceUrl` 保留网关前缀。

未认证返回 401，非管理员返回 403，未知编码返回 404。发行文件缺失、与服务版本不一致或摘要异常时返回 503，并用 `X-DeepAgent-Error: developer_resources_unavailable` 标识；Console 显示处理提示，临时网络和网关失败仍可重试。只从产品固定文件路径读取，错误响应不披露部署路径。成功及发行文件异常响应均为 `Cache-Control: no-store`，下载带 `nosniff`。浏览器通过 `daFetch` 继承登录身份，检查响应后保存 Blob，不把错误 JSON 当下载文件。

Skill ZIP 不放公开 Console 静态目录。旧 `console/downloads/spreadsheet-initialization.pdf` 地址保留，因此旧 PDF 并未全面私有化。

## 唯一资料源与构建

产品维护目录：

```text
src/deep_agent/developer_resources/
├── skill/deepagent-agents-dev/
│   ├── README.md             # Coding 工具安装、连接与验收
│   ├── SKILL.md              # 简短任务路由
│   ├── references/01-…11-…md
│   ├── references/source/backend/…
│   ├── references/source/frontend/…
│   └── scripts/…
└── generated/
    ├── manifest.json
    └── deepagent-agents-dev.zip
```

版本仅从 `deep_agent.__version__` 读取；源文档的 `{{DEEP_AGENT_VERSION}}` 在构建时替换。修改资料或产品版本后执行：

```bash
.venv/bin/python scripts/build_developer_resources.py --write
.venv/bin/python scripts/build_developer_resources.py
PYTHONPATH=src:. .venv/bin/python -m unittest \
  tests.unit.test_developer_resources tests.integration.test_developer_resources_api
```

构建固定 ZIP 文件顺序、时间戳、权限和压缩等级，生成实际字节数、SHA256、PDF 页数及文档数。默认命令只检查提交的生成文件是否仍与源一致。生成物是正式产品资料，和源文档一起发行；运行时不依赖 `scripts/`、原型目录、维护者机器或构建环境中的 PDF 解析器。当前 Jenkins 使用完整 `git archive` 打包仓库，已跟踪的上述产品目录会进入发行包，不需要另复制私有文件。

初始化 PDF 维护自 [语雀初始化指南](https://proinnova.yuque.com/ys1krd/ky4q6u/deepagent-initialization)。当前 PDF 共 6 页，包含新版侧栏、MCP「会话应用」和组件场景的演示截图。首次初始化在「服务发布 → 组件场景」关联 Agent 即可。

更新时先在隔离浏览器中用演示数据重拍受影响的操作，再更新语雀正文并回读确认。用已发布的正文和截图排版 PDF，检查截图、分页和目录后，运行上方的资源构建与校验命令，从控制台重新下载核对页数和摘要。Skill 继续使用一个 ZIP 安装包，产品源与本机 Codex Skill 各自维护。

## 内容核验记录

应用配置与开发资料于 2026-11-10 复核。目标部署通过 `/info`、OpenAPI 和真实回读独立确认。

| 内容 | 核验来源与处理 |
| --- | --- |
| 业务配置与管理接口 | 对照 `api/v1/agents.py`、`llm_models.py`、`skills.py`、上传/会话路由及项目指南；保留精确 ID、显式关联和管理员边界 |
| 脚本与上下文 | 对照执行工具、`deepagent_files.py`；区分普通 Python 与 Skill 输入，包含 include_context、共享 Run 缓存与无凭据上下文 |
| 工具目录、PTC、图片 | 移除旧“Skill 源码不可读”说明；时间默认运行时，PTC 使用授权 Python/Skill；图片在外层查看，文件来源仍受会话授权 |
| 安装与原生输出 | 保留当前 Agent ZIP、独立 Skill ZIP、平台接受/客户端采用/业务保存的不同边界；版本历史只用于说明升级方向 |
| 应用专用配置 | 复核作用域权限、同名安装、原包回读、旧哈希键与可读新键、备份恢复及 `015` 的字段归属 |
| Codex 后端开发参考 | 导入主路由及完整 13 篇参考，补充当前 API/运行边界；保留解释器检查和 SSE 汇总脚本 |
| Codex 业务前端参考 | 导入主路由及完整 9 篇参考，保留独立电子表格与 Planning 交付区别及 SSE 汇总脚本 |
| 可移植性 | 移除维护者绝对路径和固定私有 Profile；只保留任务相关的源码相对路径，不携带 Codex 专用 agents 元数据 |
| 轨迹与文件导出 | 诊断指南补齐 JSON／ZIP 入口、预估与快照、清单与 SHA256、部分缺失和当前调用者文件权限；用于排障，不是可恢复安装包 |
| 包完整性 | 构建检查所有 Markdown 文件链接、路径/凭据模式；测试实际 ZIP 校验、标准 Skill 解析与完整文档对应关系 |

示例使用虚构 ID 和通用业务名称；客户原材料、平台凭据、真实会话内容、运行数据库和安装器缓存不进入包。主 `SKILL.md` 只做路由，引用按需读取，避免每次加载全量开发资料。

2026-11-06 精简复核：本机后端和前端两套资料合计约 247 KB，本包额外提供无需源码的 11 篇业务指南；它们与源码专题的使用阶段不同，不按文件数量删除。合并主入口和源码入口的重复说明，安装流程集中到新增 README；保留 13 篇后端、9 篇前端专题及诊断脚本。主 SKILL 从 4,719 字节缩至 2,848 字节，包内全部源码资料从 273,649 字节降至 268,447 字节（含新增 README）。这些字节数是资料体积，不是模型实际上下文或计费 Token。

WorkBuddy 的安装、实际读取、连接、创建最小 Agent 和一次对话属于真实客户端验收，不能由包解析单元测试代替。对应会话和验收结果在交付时单列。
