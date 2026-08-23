# 18 · 会话轨迹

> 调研与原型：2026-08-23
>
> 实施版本：v0.5.3；Alembic head：006。

本课题研究在 DeepAgent 对话测试工作台中增加“轨迹”视图，用于按一次会话的
Run、模型请求和工具调用顺序排查问题。

- [`deepseek-harness-research.md`](deepseek-harness-research.md)：DeepSeek Harness
  源码结论、DeepAgent 现状差距和推荐落地路径。
- [`Prototype DeepAgent会话轨迹.html`](Prototype%20DeepAgent%E4%BC%9A%E8%AF%9D%E8%BD%A8%E8%BF%B9.html)：
  可交互的界面原型，包含对话/轨迹切换、Run、搜索、折叠、详情页签和日志导出反馈。
- [`Prototype DeepAgent会话详情三方案.html`](Prototype%20DeepAgent%E4%BC%9A%E8%AF%9D%E8%AF%A6%E6%83%85%E4%B8%89%E6%96%B9%E6%A1%88.html)：
  右侧会话详情的三套轻风格布局方向，用于确认信息层级，不直接替换现行页面。

## 一句话方向

首版轨迹不是把聊天气泡换一种排版，也不建立第二套事件库，而是对现有History和当前SSE的
只读、安全投影：

```text
原始 chat_messages History + 当前 SSE
→ Pydantic AI Run / Request / Tool 归组
→ 同次模型响应聚合为助手Loop，输入保留Context Reference与附件标记
→ Context Reference同时形成独立CONTEXT事件并归入同一Run
→ 按Run归组的轨迹列表
→ 选中记录查看脱敏后的Payload、Result、模型摘要、用量和已有时间
```

Console默认仍显示“对话”；切换轨迹不会创建Run、调用模型或改变会话。History新增的只是
数据库原本已经保存的模型名、Provider、Response ID、Finish Reason、Usage与工具Outcome安全
投影。系统提示词、动态工具Schema、认证Header和逐Part精确耗时没有可靠事实时明确显示缺失，
不会从当前配置反推。详情按事件类型提供Summary、Payload、Preview、Raw或Result，JSON统一使用
语法高亮。Thinking、Text与ToolCall子Part没有独立持久化时间戳，因此只按所属响应与消息顺序
展示。当前History缺少可靠的逐事件起止时间，首版不绘制甘特图或时间轴，避免制造貌似精确但无法
核验的区间。原型使用模拟数据，部分高级字段仍只代表未来方向。
