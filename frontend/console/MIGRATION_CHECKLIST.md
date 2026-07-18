# DeepAgent 一体化控制台迁移矩阵

本矩阵用于确保 `/console/` 在保留旧入口的同时覆盖现有功能。只有自动化与页面联调均通过后，项目才可标记完成。

## 页面与上下文

- [x] `/console/`、`/`、`/settings.html`、`/models.html`、`/admin/` 均可访问。
- [x] 平台用户只读；缺少 `user` 时锁定工作台与配置中心。
- [x] 仅允许覆盖 App、Space、Language，不读取旧 `da_request_headers`。
- [x] App/Space 切换中止流并清空旧状态；Language 切换保留会话。
- [x] 明亮、护眼、暗色、高对比度主题均可使用。

## 测试工作台

- [x] Agent 搜索、刷新、选择和最近选择恢复。
- [x] 会话搜索、新建、切换、刷新、重命名、自动标题和删除。
- [x] 历史文本、Thinking、工具调用、工具结果、附件、模型和 Token 用量恢复。
- [x] SSE 跨 chunk、停止、错误、取消和重试。
- [x] 历史消息编辑与分支重发。
- [x] 会话级模型覆盖与附件模态校验。
- [ ] 图片、TXT、PDF、DOCX、XLSX 上传、粘贴、移除和失败重试。页面能力已迁移；本机 Test 文件服务域名无法解析，真实上传请求返回 502，未生成文件 ID。
- [x] confirm、choice、text、date、structured form HITL 提交、取消和恢复。
- [x] 非法 HITL 参数不会造成页面崩溃或历史空占位。
- [x] 运行检查器只显示真实会话、工具链和脱敏事件。

## 配置中心

- [x] Agent CRUD、模型/能力/MCP/UI 工具关联和长 Prompt。
- [x] LLM 模型 CRUD、单条/全量元数据刷新和 API Key 掩码。
- [x] 模型目录搜索、筛选、分页、刷新和预填模型。
- [x] MCP CRUD、transport 字段切换、Header JSON 校验和 UI 工具刷新。
- [x] UI 工具 CRUD、来源筛选、Schema 编辑、模板和 Agent 关联。
- [x] 辅助模型 use case 映射修改、回读和恢复。
- [x] 401、403、409、422、500、503 使用明确错误状态。

## 视觉与清理

- [x] 1440x900、1280x800、1024x768、390x844 无遮挡和异常横向滚动。
- [x] 测试会话与临时配置已删除，辅助模型映射已恢复。
- [x] Playwright、浏览器、代理、临时服务和 `TMP to delete` 文件已清理；按联调要求仅保留 8000 后端。
- [x] diff 不含 Token、Cookie、密码、用户 ID 或 API Key；测试中的 `secret` 均为固定假值。
