# 诊断中心浏览器视觉验收

- 模式：fixture；主题：paper；视口：1736×1024
- 结果：13/13 检查通过
- 浏览器与上下文已关闭：true
- 参考主稿：docs/design/23-diagnostics-and-logs/visual-rebuild-20260910
- 截图仅是人工对照证据；通过 DOM/行为检查不代表像素一致。
- Fixture 模式只返回合成账号与业务文本；live 模式只读且不把接口正文保存到报告。

## 检查结果

- 通过：logs: real rows or explicit truthful empty state
- 通过：logs: master-sized layout and font boundaries
- 通过：logs: copy actions restricted to Session ID and Run ID
- 通过：log review sequence
- 通过：overview: master-sized layout and font boundaries
- 通过：overview: chart and metrics use loaded data
- 通过：overview: account labels contain username only
- 通过：list: master-sized layout and font boundaries
- 通过：list: username cells have no avatars/email
- 通过：session review sequence
- 通过：browser has no unhandled page errors
- 通过：review made no mutation requests
- 通过：fixture usernames do not fetch avatar URLs

## 截图

- [logs.png](logs.png)
- [overview.png](overview.png)
- [list.png](list.png)

布局位置、字体、颜色、边框和相对于图片主稿的区域差值见 [review.json](review.json)。
