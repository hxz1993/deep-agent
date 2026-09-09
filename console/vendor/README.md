# 前端第三方依赖（vendor）

本目录存放 Markdown 渲染相关的预构建静态资源，**无需 npm 构建**即可使用。

| 文件 | 项目 | 版本 | 许可证 |
|------|------|------|--------|
| `marked.min.js` | [markedjs/marked](https://github.com/markedjs/marked) | 12.0.2 | MIT |
| `purify.min.js` | [cure53/DOMPurify](https://github.com/cure53/DOMPurify) | 3.2.4 | Apache-2.0 OR MPL-2.0 |
| `highlight.min.js` | [highlightjs/highlight.js](https://github.com/highlightjs/highlight.js) | 11.11.1 | BSD-3-Clause |
| `mermaid.min.js` | [mermaid-js/mermaid](https://github.com/mermaid-js/mermaid) | 11.17.0 | MIT |
| `github-markdown-dark.min.css` | [sindresorhus/github-markdown-css](https://github.com/sindresorhus/github-markdown-css) | 5.8.1 | MIT |
| `highlight-github-dark.min.css` | highlight.js `github-dark` theme | 11.11.1 | BSD-3-Clause |
| `highlight-github.min.css` | highlight.js `github` theme（浅色主题用） | 11.11.1 | BSD-3-Clause |

## 升级方式

从官方 CDN 重新下载对应版本文件覆盖即可，例如：

```powershell
Invoke-WebRequest -Uri "https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js" -OutFile marked.min.js
```

更新后请在 `console/README.md` 中同步版本号说明。
