# 沙箱中文绘图字体

- 文件：`NotoSansCJKsc-Regular.otf`，原版完整字体，未做子集或修改。
- 字体：Noto Sans CJK SC，Regular，Sans 2.004。
- 官方项目：https://github.com/notofonts/noto-cjk
- 固定版本：`523d033d6cb47f4a80c58a35753646f5c3608a78`（`Sans2.004` tag）。
- 上游路径：`Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf`。
- SHA-256：`2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b`。
- 字体内嵌版权声明：© 2014-2021 Adobe (http://www.adobe.com/).
- 许可证：同版本仓库根 `LICENSE`，SIL Open Font License 1.1，随本目录保留。
- 获取：上游 GitHub 固定版本经 jsDelivr GitHub CDN 下载；运行时无下载。

`plotting.py` 在沙箱首次导入 Matplotlib 后注册此字体，默认 sans-serif 使用它，
并使用非交互式 Agg 后端。模型不需要另写字体搜索或将中文转换成英文；用户仍可显式改 rcParams。
普通 Python 调用不导入 Matplotlib，不承担绘图库初始化和字体扫描成本。
字体随 `src/` 进入镜像；不要求操作系统安装字体，也不改宿主字体目录或 venv。
