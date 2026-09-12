# Console 下拉框占位项与清空项清单

2026-09-13，基于当前 DeepAgent Console 源码只读核对。范围是本项目 Console，不是 DeepFOS 平台其他组件。本轮先记录供查看，未改组件或业务代码。

统一交互方向：提示文字只在尚未选择的控件中显示；菜单只列真正可选的数据。需要允许清空时，在输入框右侧用独立清空动作。空列表和加载失败直接显示状态，不伪造 option。已有失效关联保留事实提示，不能因为菜单不显示就静默清掉后台配置。

| 页面入口 / 控件 | 当前类似内容 | 建议表现 | 源码位置 |
| --- | --- | --- | --- |
| 安装 Agent → 覆盖目标 | 请选择要覆盖的 Agent | 控件占位；菜单仅真实 Agent，覆盖必须明确选择 | `console/configuration/agent-packages.js:131` |
| 安装 Agent → 名称与模型 | 未配置模型 | 清空 x；未选择时字段为空，菜单只列可用模型 | `console/configuration/agent-packages.js:205` |
| Agent → 配置 → 模型 | 未配置模型 | 同上，保留可保存空模型的能力 | `console/configuration/core.js:281` |
| 集成 → Agent 关联 | 未配置；Agent 已不可用（禁用项） | 清空作为独立动作；失效关联在字段下提示，不放入菜单 | `console/configuration/integrations.js:13` |
| 渠道 → 新建 / 配置 → Agent，环境无 Agent 时 | （请先创建 Agent） | 整个控件不可编辑，字段旁显示暂无 Agent，不构造菜单项 | `console/configuration/channels.js:33` |
| 模型 → 新建 → 供应商，环境无 Provider 时 | （请先创建供应商） | 同上，显示空状态 | `console/configuration/llm-models.js:118` |
| 工具 → UI 工具 → 结果 Schema → 根类型 | — | 未定义结果类型作为字段空态，清空按钮表达移除类型 | `console/configuration/ui-schema-editor.js:239`；空项生成在第26行 |
| 对话 → 需要补充信息的选择字段 | 请选择 | 控件占位，不进入实际业务选项；非必填字段另提供清空 | `console/chat.js:2975` |
| 公共模型选项生成器 | 无可用模型；保留当前已停用或配置无效模型 | 无可用模型显示状态；当前失效模型单独提示，菜单仅可用模型 | `console/model-options.js:60` |
| 对话的原生 Agent / 模型状态控件 | 加载中、无可用 Agent、加载失败、无可用模型 | 需结合当前自定义界面统一空状态；原生 Agent select 在隐藏状态容器中，不当作当前可见菜单问题 | `console/chat.js:1815`、`2028`；`console/index.html:201` |
| 模型目录 → 输入模态筛选 | 全部输入模态（空 value） | 这是“取消筛选”，不是失效数据；若一并统一，可用清空筛选动作，菜单只列文本/图像/音频/视频 | `console/catalog-template.html:42` |

## 公共组件的原因

`console/ui-select.js:106` 的 `renderMenu()` 只跳过 `hidden`，仍渲染 `disabled` 选项；第122行只加了 `aria-disabled`。键盘导航虽然排除了禁用项，视觉菜单仍可能显示它们。这就是“不能选但仍像选项”的共性来源。空字符串 option 也没有区分占位提示与业务值。

`console/configuration/global-settings.js:143` 的另一套下拉渲染直接枚举 option，也没有通用 disabled/hidden 过滤；当前联网搜索 Provider 数据来源是实际 Provider，未发现同样的空 option，属于公共实现需要一起核对的点。

MCP 的历史 `STDIO（历史配置，已停止支持）` 已带 `hidden disabled`，当前公共菜单会隐藏它，不列作可见缺陷。模型供应商“未配置密钥”的后缀表达的是一个真实可选配置记录的状态，也不能仅凭文案含“未配置”就删除该记录。

## 本轮设计图采用的规则

- 新建/覆盖用二选一按钮。
- 安装来源叫“预制安装包”。
- 模型菜单只放实际可用模型；右侧 x 清空。
- 状态说明不挤进选择菜单。
- 本清单是源码审查结果，不宣称已逐页重做浏览器验收。
