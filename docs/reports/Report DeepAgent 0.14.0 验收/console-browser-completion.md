# Console 剩余平台浏览器项闭合

本次仅使用独立 Playwright session `output0140` 和隔离 API `127.0.0.1:18014`。没有使用 8026 原型抵充正式 UI，也未触及稳定 8000。浏览器实例已关闭。输出类型与精确行数的边界输入采用正式生产 renderer 在同页 DOM 的合成夹具，明确区别于真实数据库 History/Provider 测试；夹具宿主均在 `finally` 移除。

| ID | 浏览器执行内容 | 结果与边界 |
| --- | --- | --- |
| UI-14 | 正式 `mountStructuredOutput`：`{text:"",object:{},array:[],zero:0,disabled:false,nullable:null}`，真实点击分组按钮并解析 DOM | 字符串为正文（空串仍保留 text 分组），其余 5 个 JSON；英文分组编码顺序和值完整。先前真实 History 的 text/contract/clarification/0/false/null 展示另见 Console 主验收 |
| UI-15 | 正式 renderer 的根 `[1,2]`、false、0、null、空串、普通字符串 | 每个根值由浏览器 pre 文本再次 JSON.parse 后与原值比较完全相同；分组按钮禁用，不伪造顶层对象。六种根值真实 HTTP→History 身份已由公共协议测试独立验证 |
| UI-16 | JSON 14、15、16 行，以及 Markdown 围栏 14、15、16 行；分别执行展开和收起 | 14/15 行不默认折叠，16 行默认折叠，展开/收起成功。JSON 通过 12/13/14 项数组形成精确 14/15/16 行，不拿 37 行样例代替边界 |
| UI-16 长单行 | JSON 单字符串 5,000 字符和 Markdown 单行 5,000 字符 | JSON 横滚节点 scrollWidth 36,044、clientWidth 394；设置 scrollLeft=120 后真实读回 120。Markdown 同样横滚读回 120，无折行截断原值 |
| UI-20 刷新 | 真实 History 会话 `62998b1e19b449ac83a95afc777efc71` 选择“按结构分组”，刷新并重新打开 | 分组按钮仍 aria-pressed=true。存储仅 `da_console_output_view_v1: grouped` |
| UI-20 新会话 | 从前述会话切到不同 Agent 的真实 DeepSeek 合成验收会话 `6a3c3d3cf05545a392ac7a41e66fd7de` | 同样恢复分组模式；没有重新调用模型。不是仅在同一 output_id 内切换 |
| UI-20 异常存储 | 写入损坏偏好后刷新；另以拒绝 getItem/setItem 的 storage 夹具挂载正式 renderer | 损坏/不可用 storage 安全退回完整 JSON，拒写时仍可本地切换；视图切换捕获写方法请求数 0 |
| UI-22 尺寸 | 正式 Agent 编辑页，1024×768、1280×800、1469×1100 | 三种尺寸保存按钮完整位于视口，输出编码→说明的 Tab / Shift+Tab 顺序正确 |
| UI-22 键盘 | 1024×768 下 Enter 打开结构详情，Tab 到 Schema 编辑区，Escape 关闭；Space 切换“必须”并切回 | 均成功，未提交数据；与之前四主题检查合并覆盖此项 |
| UI-24 | 正式页头保存路径，对合成 Agent 的 PATCH 分别注入 422/409/500，以及 `internetdisconnected` 网络中断 | 四种失败均显示错误、保留 Agent 名称和输出定义草稿、恢复保存按钮、编辑页保持打开，没有虚假成功关闭。移除拦截后独立 GET 确认源定义未改变 |
| CFG-23 | 正式页头保存先注入 409，随后去掉失败让第二次 PATCH 到真实 API | 409 后编码 `browser_retry_result` 草稿保留；第二次真实 200，关闭编辑页，并由独立 GET output-config 读回开启状态和该编码。共 2 次 PATCH，没有自动盲重试 |

UI-24 的四种网络故障由浏览器 route 进行确定性注入，只用于验证客户端失败分支；服务端事务原子性、真实 422/409 已在数据库和管理 API 测试中分别验证。故障注入产生的 Console 网络错误属于预期，不是静态资源加载失败。

## 修复

UI-20 补验发现：原实现只在进程内按 output ID 记忆视图，刷新和新会话无法恢复。本次改为浏览器持久化一个 `full` / `grouped` 枚举；不保存 Schema、业务值、用户信息或输出 ID。每次输出挂载都读取该本地偏好，根数组/标量仍使用完整 JSON；折叠状态继续维持有界内存记忆。

新增偏好白名单、拒绝 storage 读写的定向回归通过。此后生产代码冻结，最终完整 Node 检查由本记录尾部执行结果与主验收共同记录。

最终执行：`node --test tests/frontend/console/*.test.mjs`：873/873 通过、0 skip（3.421 秒，包含 forward compatibility 新文件）；`git diff --check -- console tests/frontend/console` 通过。测试浏览器 `output0140` 已关闭，之后不再修改生产代码。

## SCH-15：正式编辑器的本地引用无损往返补验

在隔离 18014 的正式 Console，使用合成 Agent `c10d935b-4f1d-4f4b-8cc6-24f0e77d6152`，输出编码改为 `browser_ref_result`。通过原生 Schema 编辑器实际输入以下定义，并执行原生 → 卡片 → 原生往返；将 contract 的“必须”选中再取消，确认顶层 required 仍仅为 text；打开引用字段详情并退出。随后页头保存返回真实 PATCH 200，独立 GET output-config 读回定义；刷新浏览器、重开该 Agent、再次进入原生 Schema 与卡片，全部成功。

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "title": "组合输出注解",
  "$defs": {
    "contract": {
      "type": "object",
      "title": "合同定义",
      "description": "本地定义注解",
      "properties": {
        "id": { "type": "string" },
        "memo": { "type": "string" }
      },
      "required": ["id"],
      "additionalProperties": false
    }
  },
  "properties": {
    "text": { "type": "string", "title": "正文", "description": "本轮说明" },
    "contract": { "$ref": "#/$defs/contract", "title": "合同", "description": "本地无环引用" }
  },
  "required": ["text"],
  "additionalProperties": false,
  "x-client-note": { "keep": "annotation", "nested": [false, 0, null] }
}
```

三个检查点（卡片往返后、独立后端 GET、刷新重开后的编辑器）均对原定义做递归键排序后的完整 JSON 相等断言，而非只检查某些键存在。`$defs`、原生 `$ref`、引用兄弟注解、内部/顶层 required、两层 additionalProperties:false、title/description 和 x-* 注解完整保留；没有被引用展开结果替换。额外从浏览器外 curl 再读回同一完整定义确认持久化。

本次只补验和修改报告，没有生产代码改动；未调用模型。Playwright `output0140` 在 finally 中关闭，随后进程检查未发现该 session 或 `playwright_chromiumdev_profile` 残留。
