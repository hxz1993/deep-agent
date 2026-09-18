# 17 · Console UI

> 首次整理：2026-08-21
>
> 当前状态：轻量企业控制台视觉体系已在 `console/` 持续落地；保留的统一侧边栏与 0825 单层导航原型归档在本课题目录，生产页面仍以 `console/` 为准。

本课题记录 DeepAgent bundled Console 的界面架构、视觉规则、关键交互和原型取舍。
它只约束 Console 前端呈现，不修改 REST、SSE、History、数据库或工具协议。

- [`console-ui-design.md`](console-ui-design.md)：当前有效的页面架构、视觉 token、组件模式和实现映射。
- [`prototype-decisions.md`](prototype-decisions.md)：原型来源、采用内容、舍弃方向和后续原型约定。
- [`Prototype DeepAgent 统一侧边栏/`](<Prototype DeepAgent 统一侧边栏/>)：用户保留的统一侧边栏原型，从仓库根移入，不属于生产静态资源。
- [`Prototype DeepAgent 0825 单层导航方案/`](<Prototype DeepAgent 0825 单层导航方案/>)：四种单层导航方案的独立 HTML 对比原型，从 `console/prototypes/` 移入，不依赖产品资源或业务 API。
- [`Prototype DeepAgent 用户字段三方案/index.html`](<Prototype DeepAgent 用户字段三方案/index.html>)：头像、昵称、用户名和邮箱的一列展示方案；可切换列表、卡片与列宽。采用 A 后的[局部确认](<Prototype DeepAgent 用户字段三方案/scenes.html>)保留 ID 查看与复制、缺失兜底和搜索交互。
- [`../../guides/console-ui-guidelines.md`](../../guides/console-ui-guidelines.md)：后续修改 Console UI 时必须执行的实施与验收规范。

- [渠道应用鉴权三方案](<Prototype 渠道应用鉴权三方案/index.html>)：渠道应用关联与鉴权的历史原型。
- [渠道扩展设置三方案](<Prototype 渠道扩展设置三方案/index.html>)：渠道扩展设置的历史原型。

- [数据流节点 AI 辅助用模型集成原型](<Prototype 数据流AI集成/README.md>)：保存三项集成的独立交互设计；数据流配置仍为设计方案，正式目录只保留电子表格与 DeepCube。

## 一句话方向

**企业控制台 + 轻 Bento 卡片 + 高密度业务列表**：使用浅蓝灰画布、大圆角白色表面、
弱边框和低阴影建立层级；信息密度高但不拥挤，以少量平台紫和语义色辅助扫描。

## 权威实现

- 页面结构：`console/index.html`、`console/*-template.html`
- 全局壳与工作台样式：`console/console.css`
- 配置页结构与样式：`console/configuration/`
- 工作台布局与拖拽：`console/workspace.js`
- 目标测试：`tests/frontend/console/`

本文档用于解释设计决策。页面功能、字段和边界如果与文档不一致，以当前源码和接口契约为准。
