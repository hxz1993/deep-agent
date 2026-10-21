# 0.14.0 Console 验收

验收对象是正式 `console/` 静态模块，使用私有副本数据库的隔离 API `127.0.0.1:18014`，数据库已迁移 012。未改动稳定 8000、Cloud 或客户 Agent。原型 8026 不作为正式功能验收依据。

## 自动检查

- `node --test tests/frontend/console/*.test.mjs`：867 项全部通过（安全复审前的全量基线；其后新增 2 项安全回归，定向复验通过，发布前全量由主验收统一执行），无 skip。
- 本次新增 17 项行为回归（结构化输出 16 项、安装摘要 1 项），覆盖草稿默认关闭、空对象、卡片/Schema 无损转换、内部 required、无效输入保留与阻断、删除/改名、数组和组合 Schema、原生空值、文本净化、15 行折叠、SSE/History 身份、轨迹候选与普通工具计数、更新人回退、安装开关影响。
- 修改模块执行 `node --check`，`git diff --check -- console tests/frontend/console` 通过。

## 浏览器与独立回读

使用单独 Playwright session `output0140`，桌面 1469×1100。测试结束关闭浏览器实例，复查该 session 与 `playwright_chromiumdev_profile` 无残留。用户原有浏览器和原型页未关闭。

| 场景 | 结果与证据 |
| --- | --- |
| 正式新建配置 | 在“输出”页开启，以卡片/原生 JSON Schema 编辑，页头统一保存；真实 POST 201 |
| 原生 Schema 无效 | 无效 JSON 阻止整页保存，修复后可切卡片，不丢原文 |
| 详情 Schema 无效 | 关闭详情后仍阻止整页保存，再次打开保留无效原文；修复后成功保存 |
| Schema 无损 | 独立 GET `/agents/{id}/output-config` 回读 `text` 顶层必须、`records.items.required: [id]`、未知 `x-console-hint: keep` 均保留 |
| 刷新与取消 | 刷新重开读取相同编码和约束；取消 `discarded_name` 草稿后仍为原编码 |
| 开关与草稿 | 关闭后真实 PATCH 成功；重新进入仍关闭，打开后保留原定义草稿 |
| 复制 | 正式列表“复制 Agent”真实 POST 201；独立 API 回读副本关闭状态和完整草稿，内部 required 保留，更新人是本次操作账号 |
| 更新人两种布局 | 卡片及列表均显示更新人。隔离账号不在真实账号目录中，返回 `resolution: unavailable`，页面按可信账号 ID 回退；Username 优先及转义由自动测试验证 |
| 正式 History 展示 | 合成会话 History 只有用户和接受输出，没有失败草稿或输出工具卡；页面只有一个正式输出 |
| 两种视图 | 回复区和轨迹输出页均可切“完整 JSON / 按结构分组”；正文作为通用文本，其余为 JSON，不生成业务表单 |
| 长 JSON | 37 行整体 JSON、25 行分组 JSON 默认折叠；完整原值仍留在复制/下载路径 |
| 完整复制和下载 | 浏览器真实复制并读取刚写入的剪贴板；下载 JSON 后用独立 Python 解析。末尾 `key_21`、0、false、null 均完整保留 |
| 失败/成功轨迹 | 同类型“输出”，分别 Error / Completed；失败页标“此输出未被接受”，候选是原 `{}`，成功页是独立接受值 |
| Schema 详情 | 从原用户事件冻结快照读取 Schema，不查询当前 Agent 定义，不展示虚构版本号或耗时 |
| 工具统计 | 输出调用、输出 ack 和 SDK 全局 RetryPrompt 均不产生普通工具计数；本合成会话页面“调用 0” |
| 主题 | 明亮、护眼、暗色、高对比均切换并检查输出文字/背景 token 生效；配置和分组回复截图已目视核验 |

配置合成 Agent ID：`c10d935b-4f1d-4f4b-8cc6-24f0e77d6152`；关闭草稿副本：`5a8b2808-6b3a-4347-95f7-52f0e2a8057d`。仅存在隔离验收数据库。

展示会话：Agent `7d5fd545-9626-49ee-a72c-6cdb6670fa2a`、Session `62998b1e19b449ac83a95afc777efc71`、接受 `output_id: 19c9fb2aea1f525c8a2ecbc479d1a320`。该记录由 SDK FunctionModel 产生真实持久化事实，用于可重复的页面验收，**不计入真实 Provider 验证**。DeepSeek/Kimi 的真实调用由主验收记录单独报告。

## 本次页面验收修复

真实 SDK 在响应级格式拒绝后可能生成没有 `tool_name`、且使用新 ID 的全局 RetryPrompt。原轨迹会把它误投影为普通完成工具并多计一次调用。现在只有已经标记输出预算的这类提示被排除普通工具投影；原始事件仍可查看，关闭路径保持原逻辑。新增两个来源配对回归，重新加载正式页面后“调用 0”通过。

截图、下载和 Playwright 原始页面证据仅存本地私有 `docs/local-artifacts/` 及 `.playwright-cli/`，未纳入 Git；可清理文件名带 `TMP to delete`。生产依赖没有变化。

## 公共协议独立验收

`tests/integration/test_structured_output_public_protocol.py`：6 项全部通过（5.7 秒）。普通 HTTP 六类原生根值及 History 身份、关闭路径无新字段/事件、SSE 失败→成功→final 顺序及持久通知先行、普通账号归属隔离、请求/Token/提问/工具/最近异常不重复均经真实 API 与 SQLite 验证。Live 验收使用真实 ASGI 逐帧连接：断开一个观察者不取消生产者；接受事件可先从 History 独立回读，终态前新观察者回放、原观察者和 final 使用同一 output ID，且仅执行一次模型请求。没有继承并重复收集旧测试类。

## 安全复审

输出编辑器改用同时转义引号的独立 HTML 转义，避免含引号的合法 JSON 属性名逃出 `data-output-*` 属性；新增更新人 title 同样转义引号。恶意结构名与 `__proto__` / `constructor` / `prototype` 自有键无损回归通过，未修改全局对象原型。真实浏览器 DOM 验证恶意属性名往返后未生成 onclick / img，也未执行 handler；独立安全测试后浏览器由 finally 关闭。安全与相关定向 52 项通过。
