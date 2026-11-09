# 同版本配置初始化

`POST /api/v1/configuration/import`用于把DeepAgent配置备份初始化到无会话的新环境。它是企业管理员
接口，不提供Console页面，也不是数据库迁移或通用恢复工具。

## 请求

```text
Content-Type: multipart/form-data

database_file=<DeepAgent配置备份.db>   必填
skills_bundle=<Skill全量导出.zip>      备份含上传Skill时必填
apply=false                            默认，仅预检
confirm=false                          正式执行时必须为true
```

- `apply=false`只校验并返回来源环境、来源/目标Revision、各配置表行数、Agent ID和Skill数量。
- `apply=true&confirm=true`执行初始化。
- 配置DB只写入权限为`0600`的请求级临时目录，完成或失败后删除，不上传平台File Store。
- Skill ZIP属于外部配置资产，会上传目标环境平台File Store并生成新的File ID。

## 强制边界

接口动态读取备份库、备份元数据和目标库的Alembic Revision，三者必须完全一致；不硬编码具体版本，
也不做升级、降级、改标或跨版本转换。备份表范围必须与当前代码ORM一致，两侧会话表必须为空，目标
当前不能有活动Run。SQLite完整性、表/列、主键、约束形状、行数和逻辑引用任一校验失败都会拒绝。
预检覆盖模型与 Provider、Agent 与模型、UI 工具与工具包、渠道账号与 Agent 的逻辑引用。
备份含应用专用配置时，还会核对目标 App 与 Space、当前管理员的企业权限、Skill 归属和原包摘要。

正式导入按明确的逻辑依赖顺序在一个事务中整体替换配置，原样保留Agent、Provider、Model和其他业务主键，
并且不写入备份的`alembic_version`和`_backup_info`。SQLite使用`BEGIN IMMEDIATE`；MySQL/PostgreSQL
通过数据库维护行锁与新版普通写入协调，持锁后重新预检。写入后、提交前再次核对行数、引用和目标Revision；失败
时数据库整体回滚。Skill已上传而数据库失败时，会尽力删除本次产生的新文件。

## 新环境流程

1. 部署目标版本，让应用建立当前Revision结构，且不要创建会话。
   多Pod环境应先停止外部对话流量并只保留一个执行初始化的实例，避免其他实例在检查后启动新Run。
2. 用同一版本的备份工具生成配置 DB。存在上传 Skill 时，同时从来源环境调用
   `GET /api/v1/configuration/skills/export` 下载原包集合，包含通用和应用专用 Skill。
3. 调用接口执行`apply=false`预检。
4. 核对Revision、表行数和Agent ID摘要。
5. 使用相同文件执行`apply=true&confirm=true`。
6. 核对Agent ID与来源一致，并完成一次对话冒烟。

配置备份保留源库实际 Revision。以 `015` 为例，目标库也需要是 `015`，且表结构与目标服务一致。
`014` 备份需先导入隔离的 `014` 环境，再按迁移流程升级。

新增配置表需同步维护`configuration_transfer.CONFIGURATION_TABLE_ORDER`的逻辑顺序、分类和引用校验；
不再通过ORM数据库外键推导导入顺序。

`004`新增的`integration_settings`按通用JSON配置原样备份与导入；目录定义不备份，也不随导入自动初始化。

`005` 允许 `agents.model_id` 为空，配置备份与导入保留空值。Agent 安装包与全量配置备份是不同格式，不互相替代。

`005` 的 `agents.ui_tool_selection` 列仍可存在于旧备份中；导入原样保留底表数据，但运行时忽略其工具名名单，只按 `agents.capabilities` 启用整个 UI 工具包。Agent 再保存时清空旧名单，新 Agent 导出安装包不携带此字段。
