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
当前不能有活动Run。SQLite完整性、表/列、主键、外键、行数和逻辑引用任一校验失败都会拒绝。

正式导入按外键顺序在一个事务中整体替换配置，原样保留Agent、Provider、Model和其他业务主键，
并且不写入备份的`alembic_version`和`_backup_info`。写入后再次核对行数、引用和目标Revision；失败
时数据库整体回滚。Skill已上传而数据库失败时，会尽力删除本次产生的新文件。

## 新环境流程

1. 部署目标版本，让应用建立当前Revision结构，且不要创建会话。
   多Pod环境应先停止外部对话流量并只保留一个执行初始化的实例，避免其他实例在检查后启动新Run。
2. 用同一版本的备份工具从来源环境生成配置DB；存在上传Skill时同时“导出全部”。
3. 调用接口执行`apply=false`预检。
4. 核对Revision、表行数和Agent ID摘要。
5. 使用相同文件执行`apply=true&confirm=true`。
6. 核对Agent ID与来源一致，并完成一次对话冒烟。

备份`001`只能导入目标`001`，备份`002`只能导入目标`002`。未来Migration新增配置表后，同版本
备份会按当前ORM自动纳入范围，无需维护导入表白名单。
