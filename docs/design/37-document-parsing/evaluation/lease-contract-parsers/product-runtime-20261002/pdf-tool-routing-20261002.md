# PDF工具选择与命名修复 · 2026-10-02

原会话`ba0b511c16bc4103a6fdbc19951525ad`在Paddle配置下先调用`read_file`，被格式校验拒绝后才调用`read_document`完成解析。该会话并非供应商失败。附件模型提示无差别写着“请调用read_file”，工具定义也没有明确列出不支持PDF，构成相互冲突的选择提示。

本轮采用`parse_document`，中文“解析文档”；`read_file`保留中文“读取文件”。参数、`builtin:document`分组、供应商快照、缓存、分页与续读协议不变，不保留旧运行时别名。旧事件和历史调用不改名。供应商内部解析函数仍是`documents.providers.parse_document`，通过`parse_with_provider`导入与工具闭包隔开。

修复范围：

- 附件投影仅提供精确文件引用和格式，不再一律引导到`read_file`。
- 系统说明、工具说明和目录统一明确：`read_file`只读取文本、DOCX、XLSX，不支持PDF、PPT/PPTX和图片OCR；PDF包括带文字层的PDF直接使用已加载的`parse_document`。
- 普通文本和Office读取保持原行为；文档解析仍受Agent显式选择及当前供应商格式限制。不推荐未加载工具。
- 更新模型工具名、显示名、目录配置跳转、日志工具标识、跳过二次外置的排除项、使用说明及测试。未增环境变量或DDL。

同一46页合同经过生产Chat路由、真实Agent装配和本地DeepSeek/Paddle新请求复测：首个内容工具为`parse_document`，无`read_file`调用；6批读完，52.107秒结束，末批`has_more=false`。逐页拼接与适配器原生正文完全一致，每批含包装不超过48K估算Token。测试在隔离配置快照中运行，不修改用户会话和Agent。只证明该样本链路及内容传递正确，不宣称模型选择永远正确或OCR字段完全准确。

定向79项、完整Python2640项（跳过53项）、Console777项通过。静态脚本检查、`git diff --check`通过。8000用原稳定配置重启，健康正常；浏览器工具目录显示`parse_document`、Paddle格式和配置跳转。测试页、Key输入、配置快照和临时Skill目录已清理，原生产物仅留在Git忽略的TMP私有目录。

本轮未提交或发布；仍沿用此前MinerU真实服务未通过的发布限制。
