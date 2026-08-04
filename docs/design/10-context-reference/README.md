# 10 · 上下文引用

> 首次研究：2026-08-03
>
> 当前状态：DeepAgent 公共协议在 v0.3.5 落地；DeepCube、DeepTable 客户端迁移待后续跨仓实施

本课题讨论业务页面如何把当前选区、元素、脚本、Cube、页面状态等结构化参考信息交给
DeepAgent。快捷命令不另建公共协议：确定性操作继续调用 API，业务命令继续作为普通文本由
对应 Agent Prompt 解释。

- [`context-reference-design.md`](context-reference-design.md)：完整方案、接口、运行链路、历史语义、Prompt 模板、电子表格与 DeepCube 示例、测试和实施顺序。
- [`../03-context-compaction/`](../03-context-compaction/README.md)：Context Reference 进入历史与 Checkpoint 后的压缩边界。
- [`../06-attachment-parsing/`](../06-attachment-parsing/README.md)：结构化引用持久化与运行前解析可复用的附件实现经验。
- [`../TODO.md`](../TODO.md)：跨课题唯一待办清单。

当前结论：

1. 浏览器请求增加 `context_refs`，不再要求业务前端手工拼接 `<reference>` 文本信封。
2. Context Reference 使用严格公共外壳和宽松业务 `payload`；DeepAgent 不需要集中注册全部业务类型。
3. DeepAgent 公共运行层负责校验、持久化、通用 Prompt 格式化和安全边界；具体 Agent Prompt 负责解释自己认识的 `type + schema_version`。
4. `metadata` 完整持久化并返回前端，但默认不发送给模型；希望模型使用的字段必须放入 `payload`。
5. 前端决定本轮是否附加上下文；DeepAgent 首版不做去重、活动上下文或自动补发。
6. 快捷命令维持现状：不增加 `command` 字段，不建立 DeepAgent 全局业务命令注册表，也不交给 Harness 拆解。
7. 新建、删除、导出、停止等确定性操作继续由前端或后端直接执行，不伪装成模型快捷命令。
