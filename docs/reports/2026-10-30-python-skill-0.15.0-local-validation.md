# 0.15.0 Python／Skill 执行统一：本地验收

本文记录实现阶段的本地验收；最终提交、Tag与追加回归见[发布审计](2026-10-30-release-0.15.0-audit.md)。基线为 `test / v0.14.12 / 18ba289`；产品版本已改为 `0.15.0`，数据库保持 `013`。依赖变更：无。原有未提交的 Console 引用热区、网络与 `.env` 调整均保留。

## 实际改动

- Skill 在独立临时目录的 `skill/` 下保留原入口与相对目录，不再生成临时根 `main.py`。根 `inputs.json`、`outputs/result.json` 保留，不强制 `main()` 函数。
- 携带同 Skill 已索引的 Python 模块，资源仍由 `resource_paths` 选择。校对文件指纹，拒绝混入更新后的其他版本。支持同目录导入、相对导入、嵌套包与初始化文件；直接执行初始化文件只执行一次，入口也支持带连字符、额外点及大写 `.PY`。保留目录和控制文件的大小写变体均禁止被附件覆盖，实际文件系统上的路径碰撞明确拒绝。
- `execute_skill_script` 增加可选 `files: [{file_id,path?}]`。与普通 Python 共用附件授权、输入映射、显式产物上传、失败补偿及返回顶层 `files` 的流程；普通 Python 的代码／inputs 形式不变。
- PTC 调用同一 Skill 实现，外层收集可信上传产物并沿用现有会话文件事实；不信任 MCP 伪造的文件 metadata。没有新增 HTTP 路由、History 类型或 SSE 事件类型。
- `.env` 由脚本明确选择后自行读取，不自动注入，不继承宿主平台凭据；随包明文配置会随原包下载、导出。此前授权移除的 Python 网络 IP 拦截属于本版本，Web Fetch 等工具自己的网络策略不变。
- 资源规则见 [Skill 指南](../guides/skills.md)。本轮没有放开其他资产后缀、增加工作区或合并沙盒分支。

## 回归结果

最终源码与测试冻结后，使用项目标准命令完成全量，退出码为0；1245个源码、测试与Console文件指纹与冻结时一致。

| 检查 | 已取得结果 |
| --- | --- |
| 最终 Python 全量 | 3362 项：3296 通过、66 跳过、0 失败；724.992秒 |
| Console 全量 | 1009 通过，0 失败、0 跳过 |
| 入口边界修复后的定向组 | 64 项：63 通过、1 个 macOS RLIMIT_AS 不适用 |
| 最后一次路径保护修改后的定向组 | 60 通过，0 失败、0 跳过 |
| 隔离真实 Worker HTTP／文件链路 | 9/9 符合预期，无跳过 |
| 本地真实 Provider | 五接口、OCR Mock 的普通／PTC 四个会话直接成功；普通 Python 原生与固定短程序 PTC 也通过 |
| 旧脚本隔离对照 | 5 个脚本语法通过；4 条正向业务／HTML 对照一致 |
| 静态检查 | Python 编译、修改 JS 语法、`git diff --check` 通过 |

分组有重叠，不累计相加。首轮全量之后补测发现入口 `.PY` 及直接执行 `__init__.py` 两个边界，均先复现再修复。最终全量覆盖这些修复；私有测试采集入口曾因导入路径缺失、缺少主进程保护而导致导入错误和 spawn 子进程重复启动测试。采集脚本已修正，最终回归使用项目标准 `python -m unittest discover`，没有改产品超时、删除断言或新增 skip；由失败采集进程遗留的4个隔离HTTP服务及目录已清理。

66 项跳过已单独核对：53 项为未配置隔离 MySQL／PostgreSQL 的数据库专项，5 项为未配置 Nacos 环境，7 项为 Linux 专属检查，1 项为本机不支持有限 RLIMIT_AS。本轮没有 SQL／数据库结构调整，未把这些跳过当成数据库验证通过。

最终全量结束出现测试进程 `unclosed event loop` ResourceWarning，旧回归日志中也存在；没有断言失败，不代表服务运行失败。

## 保留的真实验收会话

| 场景 | 会话 | 结果 |
| --- | --- | --- |
| 五接口，原生 Skill | [查看轨迹](http://127.0.0.1:8000/console/#/diagnostics/sessions/4e89c1b02e544129ac2617940e748cb9?view=trajectory) | 真实只读五接口 5/5；HTML＋JSON 上传并下载核对 |
| 五接口，PTC Skill | [查看轨迹](http://127.0.0.1:8000/console/#/diagnostics/sessions/2c02ac6532be4da58ac86ad8e6767ad1?view=trajectory) | 一个 run_code 内调用 Skill；两份产物 |
| PDF OCR Mock，原生 Skill | [查看轨迹](http://127.0.0.1:8000/console/#/diagnostics/sessions/b6112026dbe4456f9e73877dc71d993f?view=trajectory) | 前台首次上传 PDF；提交、轮询、分页、生成和下载通过 |
| PDF OCR Mock，PTC Skill | [查看轨迹](http://127.0.0.1:8000/console/#/diagnostics/sessions/aeea03a71d9841d7a5f00f8a901025e7?view=trajectory) | 一个 run_code 内调用 Skill；两份产物；刷新后仍一条 PTC 记录 |
| 普通 Python，原生 | [查看轨迹](http://127.0.0.1:8000/console/#/diagnostics/sessions/52c1105d27a8454ca9a7678f0d04033d?view=trajectory) | 两次调用完成生成、上传和 File ID 回读；合计10，平方和38 |
| 普通 Python，PTC固定程序 | [查看轨迹](http://127.0.0.1:8000/console/#/diagnostics/sessions/6a1c618c50024d68b6068147c20f7b2c?view=trajectory) | 一个 run_code 串两次 Python，回读校验为 true |

采用本地配置的 DeepSeek Flash。四条主链路均通过前台发起；OCR PDF 从前台上传。六个成功会话共12份产物实际通过文件接口下载，并比对长度、SHA256 和报告内容；浏览器另验证下载响应与 HTML 打开。当前 8000 使用 **LocalFileStore**，不能把本项称为线上平台文件服务实测。

最终源码重启8000／8010后，双服务健康检查及Python后端smoke均通过，版本为0.15.0。在同一OCR验收会话追加重启复核，6.91秒完成一次真实Provider HTTP请求：一个 `run_code` 调用同一Skill，重新提交、轮询、分页和上传；新两份文件实际下载并再次校验大小、SHA256、3页2表及 `complete=true`。原生／PTC六条主验收与重启复测合计14份文件，未将同一文件的多次下载重复计数。浏览器刷新后保留最新PTC的Summary和两份下载入口。

### 真实范围与独立答案

五接口调用测试环境 `iirjgi005` 的 `system_ping / cube_describe / dimension_get / dimension_query_members / cube_query_data`，由 Skill Python 通过 MCP HTTP 执行，不需要给验收 Agent 另挂 MCP。Cube 为 `cube_budget`，15 个维度，Period 31 个成员；精确查询真实返回0行。报告明确 `no_data`，没有造数或声称验证完整财务报告。

OCR 使用新 Skill `tmp-sany-ocr-http`，Mock 接口为提交任务、查询任务、游标分页取页。三页 PDF 每页为图片、文本层为空。独立答案核验：3页、2表、3条业务行，收入40,000,000分、成本25,500,000分、利润14,500,000分；保留1条无法识别公司名的不确定项。结果与普通／PTC两种入口一致。Mock不执行真实OCR，不代表客户接口、网络或识别准确率验证。

隔离 Worker 负向验证包括401、500、任务失败、轮询耗尽、非法JSON、重复游标，均未误报成功或授予文件访问；缺页返回 `partial`、缺页 `[2]`、完整合计 null。正常及明确 partial 产物均真实上传下载核验，临时进程、目录、Mock线程及 FileStore 已清理。

### 模型纠错记录

没有将纠错成功记成首次成功：

- 会话 `3164f2c28df74d448cffad197e0df9a0` 第一次生成上传成功；回读时模型漏传 `inputs.file_id` 导致 KeyError，补齐后成功。另开上表原生会话，两步直接通过。
- 会话 `32dc2a07a82f432aac6f1d0defc24403` 首段 PTC 因模型代码对可能为 None 的值取下标而被 Monty 静态校验拒绝，未执行内部工具；修改后生成与回读成功。上表固定短程序为显式集成用例，首段即成功，不冒称自由编程质量评测。

没有为此增加 Python 次数限制、弱化校验或修改真实 Agent 提示词。

## 旧 Skill 适配影响

历史会话、工具结果和已保存文件不变；**旧脚本再次执行使用新布局**，没有 main.py 兼容分支。已安装正式共享包未被自动修改。

| 原包／脚本 | 核对结果 |
| --- | --- |
| fund-operations-report / plan_asset_report.py | 模型映射、查询配方2处资源路径需改 |
| fund-operations-report / render_asset_report.py | 模板、配方、模型映射、规则共5处资源路径需改 |
| fund-operations-report / render_report.py | 模板和规则2处资源路径需改 |
| deeptable-intelligent-analysis / analysis_report.py | 已有基于 __file__ 的模板回退，原代码可运行 |
| deeptable-intelligent-analysis / revenue_driver_analysis.py | 本轮输入对照无需路径修改 |

基金3脚本共9处路径适配，仅在私有副本修改。对照了基金通用报告、基金查询计划、分析 HTML 报告及收入驱动计算：业务字段和 HTML 一致；仅归一化实际生成时间、文件名及由此派生摘要。完整资产14项报告缺完整合法业务输入，没有声称通过；缺输入拒绝行为和模板正文哈希一致。五个原脚本均未使用 `publish_path`，原有 JSON／HTML内容输出继续按原形式返回，自动上传需要脚本显式登记。

后续正式部署前，应升级实际仍在使用的旧脚本，并处理依赖旧布局的未完成任务；本轮不替换 Alpha、Cloud 的包或运行时。

## 数据、环境及证据

基线35个会话、820条事件逐行指纹未变。原有Agent／Skill仅修改已授权的临时五接口验收资源；新增OCR验收Agent和Skill。其余原配置、27个原运行目录文件指纹保持。新增事件正文未包含实际配置的API Key，模型未调用资源读取工具展开 `.env`；Git可见改动也未包含配置中的实际密钥。原ZIP含明文配置的行为没有被更改。

当前 `subprocess-dev` 为开发执行后端，macOS health 显示 `security_level=none`、`memory_enforcement=none`，不能宣称生产强隔离或已执行512MB硬内存上限。Python原执行时间／结果限制及PTC固定限制保留。

私有证据位于 `docs/local-artifacts/TMP to delete Python-Skill-0150验收/`，被Git忽略：包括源码指纹、原配置指纹、测试日志、真实事件／下载校验、截图、Mock、原包备份及隔离适配副本。真实凭据和原始响应不进入Git。

OCR Mock 验收完成后停止，重跑前在仓库根启动：

```sh
.venv/bin/python 'docs/local-artifacts/TMP to delete Python-Skill-0150验收/ocr_mock_server.py' --port 18095
```

自建Mock18095和报告预览18096已停止，端口释放；隔离测试子进程与目录已清理，没有新建的无头浏览器进程。API8000、网关8010、验收Agent、Skill、会话和报告保留。用户浏览器保留验收页；普通FileStore下载不依赖Mock继续运行。此处为实现阶段状态；最终Git发布见上方发布审计。

## 全量完成后的工具目录样式复核

用户指出内置工具目录排版异常。Git追溯到 `719a818 / v0.14.11`：Skill改版删除了旧 `.skill-list-*` 卡片样式，但内置工具目录仍在复用这些类，导致按钮和内部图文恢复浏览器默认排版。本次改为复用现有UI工具树及公共资源布局，保留目录数据、选择、焦点和详情逻辑；清除失效覆盖并更新入口及动态脚本缓存引用。没有修改后端、工具权限、执行或数据。

这项前端修复发生在上面的全量之后，追加运行内置工具、UI工具包、配置外壳、Skill四组定向测试：54通过、0失败、0跳过。浏览器确认12个内置目录项均为54px、图标14px，切换到MCP详情正常；9个Provider和3个UI工具目录项仍为54px，Skill列表、详情及参考文档预览正常。截图与计算样式在私有 `docs/local-artifacts/TMP to delete Tool目录样式复核/`；未再重复运行无关后端全量。

当时只读核对发现PDF／PPTX旧索引误落文本的问题；后续按用户新授权，已在下面的管理预览增量中修复。模型侧原有PDF／PPTX正文读取能力保持。

## 后续授权的 Skill 文件预览

所有资源目录项均可选。管理员可预览Python等文本脚本，复用公共代码高亮、复制、折叠及换行；图片使用现有图片弹框样式，支持放大、空白／ESC／关闭按钮收起。DOCX／XLSX沿用正文、表格读取，不还原Office排版。PDF／PPTX及当前不支持格式显示明确占位和“下载 Skill 包”按钮，不再把文件字节当成文本读取。

既有管理员资源GET增加可选`raw=true`用于索引内图片；JSON字段不变。管理专用`console_preview=True`与模型默认读取分开，Agent脚本源码读取仍拒绝；不改变安装、白名单、执行或模型工具权限。图片经同源鉴权fetch后使用临时Blob URL，迟到响应不创建URL，切资源／切页／卸载回收；服务端保留路径、索引、管理员校验及no-store/nosniff/CSP。

后端资源、管理API、统一read_file及安装解析61项通过，0失败／跳过；Console全量1015项通过，0失败／跳过。覆盖管理员可读与模型拒绝、旧索引、图片安全头、非法路径／raw参数、只读不写库，以及前端类型分派、双游标续读、错误反馈、异步取消与URL回收。

本地通过浏览器安装隔离`tmp-skill-file-preview`（13项资源），实际验证Python高亮含17个token标记、折叠／换行、PNG/JPEG/SVG加载和放大、三种关闭方式、Markdown/JSON/HTML源码/ENV、DOCX/XLSX以及PDF/PPTX占位。预览代码和HTML中的`<script>`保持纯文本，没有生成脚本元素。占位页实际下载原包，13项资源ZIP完整、SHA256与上传源包一致；下载目录中的临时副本已移入私有证据目录。

证据位于`docs/local-artifacts/TMP to delete Skill预览验收/`，新测试Skill和预览页保留用于确认。未修改原Skill、Agent、会话、附件或数据库结构；最终Git发布见上方发布审计。
