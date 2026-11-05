# DeepTable 内嵌三一助手（deeptable-assistant）：多轮对话浏览器内存暴涨诊断

本文记录 2026-11-05 在 Test 环境 `epm-test.sany.com.cn` 上对「SP战规分解」DeepTable 页面内 AI 助手面板做的浏览器内存诊断。所有结论来自本机 Chrome + CDP 实测；未修改任何产品代码，未新增依赖，未改动服务端配置与数据库结构。

## 结论

**没有复现到典型的 JS 堆泄漏。** 6 轮对话后强制 GC，堆稳定在 `31.6 → 32.7 MB`，渲染进程 RSS 仅 `324.8 → 335.5 MB`（约 `+1.8 MB/轮`，与聊天记录 DOM 增长一致）。

现场观感上的“内存爆了”，由三个因素叠加造成：

1. **i18n 文案未编译**：面板每次渲染都对几十条 UI 文案调用 `formatMessage`，而当前是 raw catalog，每次调用都 `console.error` 一条 8 行告警。单轮长回答产生 **10 万～70 万行、8k～9 万条告警、7～35 MB** 控制台文本。
2. **流式期间大量空转重渲染**：12 秒流式窗口内 `MutationObserver` 仅记录 455 次 DOM 变更，却产生约 2.6 万行 console；同期渲染进程 CPU 累计 **13.12 秒 / 12 秒墙钟**，主线程基本占满。
3. **每轮收尾再全量拉会话列表**：`POST /api/v1/chat/stream` 之后紧跟一次 `GET /api/v1/sessions?...has_messages=true&limit=20`，随会话数增长线性抬高基线。

DevTools 或任何挂载 CDP 的客户端在线时，上述 console 消息会被完整保留，几轮就是几百 MB 到 GB 级，这才是“浏览器内存爆了”的直接来源。V8 把 `heapTotal` 维持在高位（峰值 112 MB）不立即归还系统，进一步放大了任务管理器里的观感。

## 测试环境与方法

| 项 | 内容 |
| --- | --- |
| 页面 | `http://epm-test.sany.com.cn/element-deep-table/2-0-0/?elementName=B_Elite01`（SP战规分解） |
| 助手 | 表格工具栏「AI助手」→ 右侧「三一助手 Beta」面板 |
| 后端 | `deepfos-server/deep-agent-server1-0/api/v1/*`（即本项目 `deeptable-assistant` 集成） |
| 前端 | DeepFOS DeepTable bundle `iframe/element-deep-table/2-0-0/main-jE8M0GjU.js` |
| 浏览器 | 本机 Google Chrome，有头、独立持久 profile（不使用用户日常 profile） |
| 认证 | Cookie `sy-poc-v2-op-cookie-key_deepfos_token` + `sy-poc-v2-op-cookie-key_deepfos_users` |
| 采集手段 | CDP `Performance.getMetrics`、`Memory.getDOMCounters`、`HeapProfiler.collectGarbage`、页面内 `MutationObserver`、Win32 进程内存、控制台行数统计 |

采集脚本与原始数据在 `%TEMP%\pwtools_TMP_to_delete\work\`（`metrics.jsonl` 与 119 MB 控制台日志），仓库目录未产生任何改动。

## 实测数据

### 逐轮指标

| 阶段 | raw heap (MB) | 强制 GC 后 heap (MB) | heapTotal (MB) | DOM 节点 | 监听器 | 渲染进程 RSS (MB) |
| --- | --- | --- | --- | --- | --- | --- |
| 打开面板前 | — | 31.6 | — | 2565 | 1131 | — |
| 打开面板后 | 34.2 | — | 39.4 | 2651 | 1150 | — |
| 第 2 轮后 | 61.8 | **31.3** | 112.1 | 2844 | 1284 | 324.8 |
| 第 4 轮后 | 63.7 | **32.3** | 113.7 | 3159 | 1288 | 328.7 |
| 第 6 轮后 | 32.9 | **32.7** | 107.8 | 3304 | 1294 | 335.5 |

判读：

- raw 62～64 MB 经强制 GC 回落至 31～32 MB，说明每轮约 30 MB 是**可回收的瞬时垃圾与未归还堆页**，不是被持有的对象。
- 真正留存的部分：DOM 节点约 `+128/轮`（聊天记录，线性且合理）；监听器在开面板后固定（1284→1294，无每轮累积）；`documents` 恒为 12（无 iframe 泄漏）。
- 渲染进程 RSS `+1.8 MB/轮`，与消息列表增长一致，无失控趋势。

### 控制台洪泛

| 指标 | 数值 |
| --- | --- |
| 6 轮累计输出 | **2,540,697 行 / 119 MB** |
| 单轮长回答 | 约 10 万～70 万行，8k～9 万条告警，7～35 MB |
| 空闲状态 | 0 增长 / 10 s（仅在流式期间产生） |
| 流式期间速率 | 0.1～0.3 MB/s，峰值 306 KB/s |

告警形态（栈指向 `main-jE8M0GjU.js:9255`）：

```text
[WARNING] Uncompiled message detected! Message:
> 复制会话 ID
That means you use raw catalog or your catalog doesn't have a translation for the message and fallback was used.
ICU features such as interpolation and plurals will not work properly for that message.

Please compile your catalog first.
 @ http://epm-test.sany.com.cn/iframe/element-deep-table/2-0-0/main-jE8M0GjU.js:9255
```

重复次数最高的文案均为面板自身 UI 字符串，说明触发点在面板渲染而非表格区域：

| 次数 | 文案 |
| --- | --- |
| 988 | `复制会话 ID` |
| 741 | `以下 JSON 会作为 @context 写入消息的参考信息。` |
| 741 | `上下文大小：0 字符，约 0 tokens（估算值）` |
| 740 | `查看 page 原始上下文` |
| 740 | `页面原始上下文` |

### 流式期间的重渲染与 CPU

| 指标 | 数值 | 说明 |
| --- | --- | --- |
| DOM 变更次数（12 s 窗口） | 455 | 约 38 次/秒，远低于 console 消息速率 |
| 同期 console 输出 | 约 2.6 万行 | 约 3200 条告警 |
| 渲染进程 CPU | 13.12 s / 12 s | 主线程基本 100% 占满 |
| `LayoutCount` / `RecalcStyleCount` | 0 | 开销在 JS/render 阶段，不在布局 |

即大量 render 未产生视觉变化，只空转文案格式化并刷告警。

### 每轮网络行为

```text
POST /deepfos-server/deep-agent-server1-0/api/v1/chat/stream            ← 每轮 1 次
GET  /deepfos-server/deep-agent-server1-0/api/v1/sessions?...limit=20   ← 每轮结束后再拉 1 次
```

未发现未关闭的流式连接堆积：6 轮共 6 次 `chat/stream`，均正常结束。

## 现场为什么会“爆”

同一页面在本机跑 6 轮没有出现堆失控，差异在于 console 消息是否被保留：

1. **F12 Console 面板常开**：每轮 8～9 万条消息被 DevTools 缓存，4 轮约 30 万条字符串对象，百 MB 起步。
2. **挂载 CDP 的浏览器扩展**（录制、调试类）效果相同。
3. **会话拖长**：消息列表全量渲染，叠加每轮 `limit=20` 会话回拉，基线随轮次抬高。
4. **`heapTotal` 高位滞留**：V8 从 112 MB 峰值不会立刻归还系统，任务管理器进程内存曲线持续上扬。

## 修复建议

### 前端（DeepTable 的 AI 面板 / custom-js）

1. **编译 message catalog**（`@formatjs/cli compile`），或在生产构建把 `intl.onError` 降级为一次性去重上报。仅此一条即可消除绝大部分噪声与 CPU 空转。
2. **流式增量合批**：不要每个 token 都 setState 整树；按 `requestAnimationFrame` 或 50～100 ms 合并刷新，消息条目 `React.memo`，流式期间只更新最后一条。
3. **会话列表懒加载**：`sessions?limit=20` 改为按需加载，不在每轮结束全量重拉。
4. **消息列表虚拟化**：长会话下避免 DOM 线性增长。

### 后端（deep-agent-server）

5. `chat/stream` 的 delta 合批下发（例如 30～50 ms 或按字节阈值成帧），从源头减少前端渲染次数。

### 验收标准

修复后同样跑 5 轮，应同时满足：

- 流式期间 console 输出 **< 100 行/轮**；
- 渲染进程 CPU **< 40%**（单核视角）；
- 强制 GC 后 heap 斜率 **≈ 0**，`heapTotal` 在流式结束 30 s 内回落至 40 MB 量级；
- 每轮 DOM 节点增量仅来自消息正文（约 100～150）。

## 局限与未覆盖

- 未在用户日常 Chrome profile（含现有登录态与扩展）上复测，也未开启真实 DevTools Console 面板做对照实验；第 5 节关于 console 保留的推断基于 CDP 行为，不是该环境的直接实测。
- 未做堆快照（heap snapshot）按构造器归类，因此只能从“GC 后可回收”判定非泄漏，不能断言绝对不存在小规模留存增长（当前测得约 `0.13 MB/轮`，量级不足以解释现场现象）。
- 未覆盖「生成分析报告」「目标差异分析」两个快捷入口；两者会触发工具调用与更长输出，预计会放大同一个告警洪泛，需要单独复测。
- 前端代码属 DeepFOS DeepTable 侧（`main-jE8M0GjU.js`，已压缩），本仓库无法直接修改；本报告只定位现象与建议，不包含上游改动。
