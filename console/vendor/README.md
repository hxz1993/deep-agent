# 前端第三方依赖（vendor）

本目录存放 Markdown 渲染相关的预构建静态资源，**无需 npm 构建**即可使用。

| 文件 | 项目 | 版本 | 许可证 |
|------|------|------|--------|
| `marked.min.js` | [markedjs/marked](https://github.com/markedjs/marked) | 12.0.2 | MIT |
| `purify.min.js` | [cure53/DOMPurify](https://github.com/cure53/DOMPurify) | 3.4.15 | Apache-2.0 OR MPL-2.0 |
| `highlight.min.js` | [highlightjs/highlight.js](https://github.com/highlightjs/highlight.js) | 11.11.1 | BSD-3-Clause |
| `mermaid.min.js` | [mermaid-js/mermaid](https://github.com/mermaid-js/mermaid) | 11.17.0 | MIT |
| `github-markdown-light.min.css`、`github-markdown-dark.min.css` | [sindresorhus/github-markdown-css](https://github.com/sindresorhus/github-markdown-css) | 5.8.1 | MIT |
| `highlight-github-dark.min.css` | highlight.js `github-dark` theme | 11.11.1 | BSD-3-Clause |
| `highlight-github.min.css` | highlight.js `github` theme（浅色主题用） | 11.11.1 | BSD-3-Clause |

## 升级方式

从官方 CDN 重新下载对应版本文件覆盖即可，例如：

```powershell
Invoke-WebRequest -Uri "https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js" -OutFile marked.min.js
```

更新后请在 `console/README.md` 中同步版本号说明。

Console 通过 `markdownTheme` 加载 GitHub Markdown CSS，`theme.js` 随界面主题切换浅色/深色版本；`workspace.css` 只适配 Console 字号、主题 token 和独立滚动。样式许可证保留在 `github-markdown-LICENSE`。Marked 负责解析，highlight.js 只高亮已知语言，未知或未标记的围栏保留纯文本；DOMPurify 继续清理输出。

### 常用 Markdown 扩展（2026-09-15）

- `marked-footnote.umd.js`：marked-footnote 1.2.4（MIT），来自 npm 官方包，兼容 Marked 12；许可证从项目 `license` 保留为 `marked-footnote-LICENSE`。
- `marked-katex.umd.js`：marked-katex-extension 5.1.4（MIT），兼容 Marked 4–15；许可证为 `marked-katex-LICENSE`。
- `katex/`：KaTeX 0.16.47（MIT），官方 npm 包中的浏览器 JS、CSS、字体及许可证。公式采用 `trust:false`、有限宏展开及尺寸，HTML/MathML/SVG 输出统一经过 DOMPurify。
- 以上均为预构建静态资源；运行时不访问 CDN，不增加 npm 构建链。公式支持 `$...$`、`$$...$$`、`\(...\)`、`\[...\]`；无效语法保留可读文本。

DOMPurify 3.4.15 使用官方同名 tag 的 `dist/purify.min.js`，许可证见 `DOMPurify-LICENSE`。随着公式引入 MathML/SVG，同时更新 sanitizer 的 rawtext、命名空间与 DOM clobbering 防护；保持默认 `SAFE_FOR_XML`，不启用 HTML/SVG 事件或 KaTeX trust。脚注仍复用 marked-footnote 的 tokenizer，展示 ID 使用消息级数字命名空间，防止重复引用与原始标签碰撞。

- `qrcode.js`：qrcode-generator 1.4.4（MIT），官方 npm 发布包，经 jsDelivr 获取；许可证 `qrcode-LICENSE`。仅在本地生成飞书授权二维码，不向第三方二维码服务发送授权链接。
