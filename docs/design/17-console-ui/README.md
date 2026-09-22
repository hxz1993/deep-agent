# 17 · Console UI

> 首次整理：2026-08-21
>
> 当前状态：轻量企业控制台视觉体系已在 `console/` 持续落地；已落地原型与评审截图已清理，生产页面以 `console/` 为准；仅保留尚未实施的数据流 AI 集成原型。

本课题记录 DeepAgent bundled Console 的界面架构、视觉规则、关键交互和原型取舍。
它只约束 Console 前端呈现，不修改 REST、SSE、History、数据库或工具协议。

- [`console-ui-design.md`](console-ui-design.md)：当前有效的页面架构、视觉 token、组件模式和实现映射。
- [`prototype-decisions.md`](prototype-decisions.md)：原型来源、采用内容、舍弃方向和后续原型约定。
- [`../../guides/console-ui-guidelines.md`](../../guides/console-ui-guidelines.md)：后续修改 Console UI 时必须执行的实施与验收规范。


- [数据流节点 AI 辅助用模型集成原型](<Prototype 数据流AI集成/README.md>)：保存三项集成的独立交互设计；数据流配置仍为设计方案，正式目录只保留电子表格与 DeepCube。

- [Logo SVG 设计资产](assets/logos/)：保留 11 个历史图标方案的 SVG 源文件；PNG 预览已清理，正式使用版本以 `console/assets/` 为准。

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
