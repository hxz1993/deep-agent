# Agent 配置、更新人与跨版本安装验收

本记录覆盖 0.14.0 候选工作树中的配置存储、管理员 API 和安装协议。旧端源码固定为 `552b7b25291e85a23b22244d5178823f1c60570e`，不使用新解析器模拟旧端。最终发版 SHA、三种真实数据库、真实 Provider 与浏览器总验收见本目录主报告；此文不将这些独立项目算作已通过。

## 执行结果

- `tests.unit.test_agent_output_configuration`：最终定向复跑 16 项通过，包括 011→012、Unicode、降级保护、原子回滚、关闭草稿、删除、复制、Schema 变更导致预检失效、不查询列表 Schema、开启包带 Skill/UI 的提交失败/提交确认丢失、死锁不误报重复，以及本次追加的 PK-13/20 三项并发边界。
- `tests.integration.test_agent_output_configuration_api`：7 项通过，包括可信更新人、普通用户读取不依赖管理员/账号查询、专用管理读取、合并删除 422、未知源用户不影响安装操作者、用户名批量查询与 403、已知工具名冲突提前 422，以及重复名/不可用模型时开关/定义/时间/更新人不部分变化。
- 原安装单元和 HTTP 集成：46 项通过，包括取消、并发、事务失败、提交确认丢失、Skill 文件补偿、自带包、模型/MCP 匹配、资源共享和 ZIP 安全。
- 初轮配置/API/原安装组合为 **65 项，0 失败、0 skip**，约 10.6 秒；随后新增的测试按专项复跑，不将初轮方法数冒充最终全量结果。最终 Python 全量结果以主报告为准。
- `tests.unit.test_agent_max_requests` 和 `tests.unit.test_console_admin_auth`：32 项通过。旧写接口测试增加了可信用户夹具，未放宽产品写身份校验。
- `tests.integration.test_configuration_crud_contracts` 和 `tests.integration.test_skill_api`：25 项通过；同样只补可信用户测试夹具，没有放宽生产校验。
- `tests.integration.test_agent_package_cross_version_http`：产品版本已变更为 **0.14.0** 后再次运行，**12 组 PK-X 矩阵均通过**，在一个集成场景内逐格断言，最后一次 **7.449 秒**。不能把一个 unittest 方法误写成只有一个兼容场景，也不能将它计为 12 个 Python 测试方法。

新增 PK-13/20 专项与渠道生命周期一起运行，17 个方法、0 失败、0 skip、3.687 秒：正式导出在真实 Skill 下载中遇到仅 Schema 或仅开关变化，双快照均拒绝；仅 Schema 变化不依赖 Agent 更新时间变化来发现。同一开启包并发安装一胜一冲突，仅一个 Agent/配置/Skill 文件；预检后的覆盖目标被删除时明确冲突，不产生 Agent、定义或文件。

## 跨版本方法与边界

1. `git archive` 固定旧提交的 `src/` 与必要测试辅助文件，启动独立旧进程；新进程从候选源码启动。两者使用不同回环随机端口、SQLite、LocalFileStore 和 Skill runtime。
2. 经新版正式 `GET /api/v1/agents/{id}/export` 取得 ZIP，再通过旧版正式 multipart 预检、安装、普通 Agent 回读接口验证。关闭草稿同样经正式导出，未用手工删键的包代替。
3. 旧端用固定旧 ORM 元数据创建独立 SQLite。012 的 Alembic 迁移另在迁移测试中验证；本兼容测试不冒充真实 MySQL/PG 迁移验证。
4. 普通与结构化运行使用真实 SDK/Runner 和确定性 FunctionModel；未调用在线 Provider。所有正文和账号均为合成夹具。
5. 对旧版拒绝开启包的两种模式，同时请求预检和安装，前后独立 SQLite 连接读取全部表并计算内容指纹，证实零写入。共享 UI/Skill 在关闭包覆盖测试中验证复用。
6. 新端开启包安装后通过专用接口和独立 SQLite 连接核对定义、内部 required、说明、Unicode 与安装账号；通过真实 Runner 得到原生 JSON 和 output_info。关闭覆盖及两个真实旧 builtin 覆盖后，已有输出 History 深度相等。
7. 所有子进程和 HTTP client 在 finally 清理，临时目录 `TMP_to_delete_agent_compat_http_*` 删除，不修改稳定 8000 或其数据库。

## PK-X 逐格记录

| ID | 结果与证据 |
| --- | --- |
| PK-X01 | DeepCube、电子表格两个真实历史 builtin 分别新建成功；关闭且无配置行，资源正常安装。 |
| PK-X02 | 两个真实 builtin 分别覆盖已开启且已有原生 JSON 输出历史的目标；预检提示关闭，关闭并删除配置，原 History 不变。 |
| PK-X03 | 新版关闭且含上传 Skill/UI 的正式 ZIP 在旧版完整安装成功；普通聊天返回预期文字。 |
| PK-X04 | 同一正式关闭 ZIP 在旧版覆盖成功，Skill/UI 均为 reuse，模型/关联保持有效。 |
| PK-X05 | 新版关闭但保留 Schema 草稿的正式 ZIP 不带新增键/文件；旧版新建与覆盖成功。 |
| PK-X06 | 新版开启 ZIP 在旧版新建预检和安装均 422；全库内容指纹不变。 |
| PK-X07 | 新版开启 ZIP 在旧版覆盖预检和安装均 422；目标及全部共享资源指纹不变。 |
| PK-X08 | 新版开启 ZIP 在新版新建及覆盖成功，真实 Runner 原生 JSON 成功，独立 SQL 回读正确。 |
| PK-X09 | 新版关闭 ZIP 覆盖开启目标，开关关闭且定义删除；既有已接受输出 History 不变。 |
| PK-X10 | 开启包→新版安装→正式再导出→另一个新版目标，Schema、内部 required、名称/说明完整往返。 |
| PK-X11 | 来源软件 99.42.7、格式仍为整数 1 的包在旧端、新端均完整安装成功。 |
| PK-X12 | 安全未知字段/文件在新版新建和覆盖均忽略，已知配置完整；再次导出无未知扩展，未登记为资源。 |

以下为来源软件版本 **0.14.0** 的最终专项复跑 ZIP SHA256。ZIP 时间戳会使重新生成的原始 ZIP 哈希不同；内容 digest 与 ZIP SHA256 分开处理。

| ZIP | SHA256 |
| --- | --- |
| 新版正式关闭包，含 Skill/UI | `66ca267faa54f3629b45eb273b3204c25d8b83ad19a8803a0406f2291fd57e53` |
| 新版正式关闭草稿包 | `315b7f94e5347b22cdcd5592fcb0c16bd6f24761a7b9d4a23e73e86e56a4b728` |
| 新版正式开启包 | `573449f8f335509d26fad8b36d80580bd1e5dc8c15f529f622ea583c352ddd65` |
| 开启包正式再次导出 | `df6135444ca5f0cb1bba405c2c725d30bcc80f7b1542570aa1b205f59ed67c86` |
| 来源软件 99.42.7 | `535651c80178784e5fa940996c07973184dd47cb267871d72b3f5f1d30ced6ad` |
| 安全扩展包 | `93ef3ca0e631f31d900df6564fcb9816fec4b6dc44303cd962cb8e7cfc5f2bd0` |
| 原始 DeepCube builtin | `5f35ce440e95f30709b9b8d8e99d9f61022ff018e09e3be006b0103bac1190bb` |
| 原始电子表格 builtin | `936dbee389cce11ec95ad901cb5dc7fd893f22b81892501905d10ae55a9ddfe7` |

## 实现审计要点

- 原子 POST/PATCH 在同一事务保存 Agent、开关、定义、更新人和时间；只改 Schema 同样触碰 Agent。PATCH 省略保留，显式 null 根据合并后状态验证；安装覆盖按包整体替换，旧包清除目标定义。
- 新表无物理外键，加入逻辑关联审计和配置备份顺序；删除先清配置，不留孤儿。数据库与 ORM 使用实际布尔 false 默认，未使用字符串 `'false'`。
- 普通 Agent 详情不带完整 Schema，专用管理员读取使用单条 join，开关和定义一致；列表不读 Schema，账号解析批量、有界并遵守原授权失败处理。
- 未知扩展容忍仅作用于包内容 DTO；InstallOptions 仍 extra=forbid。ZIP 全条目先执行路径、符号链接、加密、重复、数量和解压预算检查，未知安全内容仍参与 package.digest。
- 安装快照/摘要/导出前后校验/提交确认回读均包含新配置表，只有 Schema 改动也会使旧预检失效。开启缺定义拒绝，关闭草稿从 ZIP 完全省略。
- 012 降级检查配置、开启、更新人、输出事件及冻结快照事实；不能仅关开关后静默丢数据。
- 没有更改两个客户端、业务 Skill、提示词、旧 builtin ZIP 或线上配置。

## 依赖变更（运维）

本子范围无生产依赖变更。新增验证和测试复用现有 Python、Pydantic、SQLAlchemy、httpx、Alembic 与标准库；没有运行时安装流程或固定镜像变更。整次发布的依赖核对以主报告为准。
