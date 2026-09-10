# DeepAgent 诊断中心 · 收敛交互原型

2026-09-09 · **L4 明亮版日志 + S1 概览 + S2 列表 + 现有 Console 只读详情**。

这是设计评审原型，不是已经上线的管理端功能。全部为本地虚构数据，不读取真实用户会话、日志或凭据，不发送业务请求，不保存到浏览器持久存储。

## 打开

仓库根目录运行：

```sh
python3 docs/design/23-diagnostics-and-logs/interactive-prototype/preview_server.py
```

访问 [原型首页](http://127.0.0.1:8023/)。服务只绑定回环地址，仅允许访问此原型和 Console 静态资源；没有业务 API。不修改或重启 8000 / 8010 服务。

使用 ES modules 直接引用仓库内的真实 Console 展示模块，因此请通过预览服务打开，不要双击 `file://`。结束评审后可停止上述预览进程；文件为正式设计材料，不是临时产物。

## 可点击的路径

1. **日志**：紧凑内容行（27px）、级别与来源胶囊，无状态圆点、无表头和纵向网格；右侧只保留 Session ID。选择行打开右侧详情，展开规范字段、复制标识、跳入关联会话，或按 Run 收窄日志。筛选、关键词高亮、清空、手动刷新、加载更多和空结果均可操作。
2. **会话概览**：统计、提问趋势、Agent 分布、最近提问、需要关注、活跃用户。图表与统计统一由当前筛选数据计算；提问数入口按真实提问逐条呈现，保留追问与会话关联。
3. **会话列表**：共用 Agent / 时间 / 用户 / 渠道筛选，增加执行状态、名称模糊与完整 ID 精确查询；分页和返回保留筛选。点任意行进入完整详情，不挤进右侧抽屉。
4. **会话详情**：对话 / 轨迹切换、正文与 Markdown、思考/工具过程折叠、轨迹顺序/调用/搜索、节点详情、概览/渠道/当前/上下文、复制和关联日志。去掉发送、重试、停止、审批、编辑和删除。运行中或待操作也仅展示静态只读记录。

只有日志、会话是诊断入口。侧边栏其他原有菜单仅作位置参照，不做无关页面。复制为真实剪贴板操作；刷新使用同一静态数据，并明确提示；未放下载或自动诊断占位按钮。

## 代码复用

| 部分 | 实际使用 |
| --- | --- |
| Conversation / Assistant actions | 直接导入 `console/conversation-items.js`、`console/assistant-presentation.js` |
| 轨迹列表、节点详情、搜索、调用过滤 | 直接导入 `console/trajectory.js` 的 `createTrajectoryController` |
| Markdown、JSON、复制、图标 | 原有 Console renderer / helper、本地 Lucide、Marked、DOMPurify、Highlight |
| 对话、处理过程、工具、右侧信息 | 复用原有 DOM 类名及 `themes.css` / `workspace.css` / `console.css`；只读装配位于 `readonly-session.js` |

没有直接导入 `chat.js` / `workspace.js`：两者目前把页面初始化、网络请求和呈现耦合在一起，直接导入会触发业务初始化。本原型不借评审任务重构生产代码；正式落地时应抽出可注入数据、显式只读的 Presenter / Inspector，供两个入口共用，而不是维护两套业务逻辑。

## 验证

```sh
node --experimental-vm-modules --test \
  docs/design/23-diagnostics-and-logs/interactive-prototype/prototype.test.mjs \
  docs/design/23-diagnostics-and-logs/interactive-prototype/readonly-session.test.mjs
```

- 19 项通过：筛选与统计一致、提问关联、日志查询、转义、安全依赖，以及完成/失败/等待操作/进行中/取消的真实 Conversation / Trajectory 投影与工具配对。
- 内置浏览器实测：概览→列表→详情→工具轨迹→关联日志；ID 精确查询、返回保留条件；提问集合；日志高亮、分页、空结果与独立滚动。
- CSP 禁止业务连接；预览接口路径穿越及 `/api/v1/sessions` 请求返回 404，允许的轨迹静态资源返回 200。
- 仅新增/修改设计目录。没有修改生产 Console、业务逻辑、版本、数据库、启动命令或流水线；没有提交、推送或打 Tag。

## 评审口径

统计是演示数据的事实计数，不代表回答正确率或用户满意度；长等待不等于卡死；失败与主动取消区分。真实管理员跨用户查询仍需独立后端权限，不能复用本原型绕过普通会话所有权限制。首版可保持日志只读查询，图表、问题集合与跨用户详情是否实施以评审结论为准。
