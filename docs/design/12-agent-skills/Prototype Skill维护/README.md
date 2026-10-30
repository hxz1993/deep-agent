# Prototype Skill维护：执行环境变量

设计评审，未落地。延续原Skill原型入口，复用当前Console的真实Skill详情渲染、主题、按钮和图标，新增环境变量弹窗。不是业务管理页面。

```bash
.venv/bin/python 'docs/design/12-agent-skills/Prototype Skill维护/server.py' --port 8024
```

打开：http://127.0.0.1:8024/#/skill/fund-operations-report

- “基金与经营运营报告”有示例配置，可查看密钥留空保留及多Agent共享提示。
- 返回列表打开“财务比率分析草稿”，可查看无配置的空状态。
- 新增、删除、取消、重复变量名和保留变量名校验均可交互；明暗主题可切换。
- 不连接真实API，不保存到浏览器存储；刷新丢弃修改。请勿输入真实凭据。
- 产品源码保持只读，原型服务通过允许列表提供所需静态资源。
- 方案见 ../skill-environment-design.md。落地后清理原型，保留方案。
