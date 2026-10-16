# 文档解析隔离实验：结果与复现

日期：2026-09-29。真实执行的是 MinerU 免费线上 pipeline、本机 MinerU 4.0.9 两档完整管线，以及 PaddleOCR-VL-1.6 官方线上Demo。**没有完成所有榜单领先模型的同样本横评。** 百度商业API、MinerU精准API、DocMind尚需Key；Logics公开Demo受403阻断。

## 输入与评分

- [两页合成 PDF](complex-numbers-two-pages.pdf)，无真实业务信息。
- 第1页为原生PDF；第2页是同一版面以144 DPI渲染后的纯图片，文字层为空。重复的打印页码是故意保留的版面对照，评分使用物理页序。
- [版面预览](fixture-preview.png)：多级合并表头、每页20个数据单元格、负数、千位分隔、百分号/千分号、折行数字、缺失值、图表、页眉页脚。
- **不是跨页续表样本**，没有测试低DPI、模糊、真实长论文、手写或几十页连续结构。
- [评分脚本](score_cells.py) 检查两表、行列数、合并表头、年份/单位/行名，再比较40个数据单元格；仅去除空白，不替换标点和数值。缺失横杠 `— → -` 计严格字符不匹配，与数值倍率错误分别解释。
- [逐格结果](cell-results.json) 可离线重算；合成页通过不表示真实文档准确率。

## 真实结果

| 完整运行路径 | 原生页精确数据单元格 | 扫描页精确数据单元格 | 实际整次耗时 | 运行状态 |
| --- | ---: | ---: | ---: | --- |
| MinerU 免费Agent API，pipeline | 16/20 | 14/20 | 16.43秒 | 任务成功并下载Markdown |
| 本机 MinerU4.0.9 basic，ONNX CPU | 19/20 | 16/20 | 14.93秒 | exit0 |
| 本机 standard 首次，ONNX + Pro2605 Q8/llama.cpp | 19/20 | 20/20 | 21.90秒 | ZIP已生成，但退出崩溃，exit -6 |
| 本机 standard 独立复验 | 19/20 | 20/20 | 13.76秒 | exit0；Markdown与首次相同 |
| PaddleOCR-VL-1.6 官方Demo，单张扫描页，chart开启 | 未测 | 20/20 | 10.94秒 | 返回结果；图表4个数值也匹配 |

耗时都不是纯模型速度：线上含上传/排队/3秒轮询/下载，本地含初始化/输出。下载模型和安装不计入表中；不得把本表作为CPU对GPU、线上对本地的性能排名，没有p95或稳定性统计。

### 错误逐项说明

1. **免费线上**两页均把表内3处 `‰` 变成 `%`，缺失符号变成汉字“一”；扫描页另把`9,876.54`变成`9.876.54`、把一个千位逗号变成全角。行列结构依然看着正确。原文`3.00‰`是0.003，误读为`3.00%`是0.03，存在10倍含义差异。
2. **本机 basic**原生页只出现缺失符号`— → -`；扫描页3处千分号全错、缺失符号为空。
3. **本机 standard**扫描表格20格匹配，包括3处千分号和缺失符号。原生页仍有横杠归一化。但扫描页表外脚注仍错，不能称整页全部正确；本次VLM改善表格，并未替代所有OCR路径。
4. 两种本地模式保留图表裁剪图片，免费线上Markdown仅有image占位；这些MinerU运行没有单独启用等价chart-to-data选项，不能据此断言其所有模式都不支持图表数据化。Paddle显式开启chart解析，正确输出Q1=1.25%、Q2=2.50%、Q3=1.90%、Q4=3.75%。这项配置差异须保留，不能当默认模式对等比赛。
5. 合并表头、换行金额和百分号的单元格位置经目检及脚本检查。主Markdown过滤页眉/页脚，原件仍保留；没有据此验证所有脚注类别。
6. Paddle表格千分号全部正确，但脚注的`‰`输出为LaTeX百分号下标0，不是原始Unicode逐字保真；这里只计20个数据格和4个图表标注值，不计为全文100%。

Paddle用的是PDF扫描页对应的同一张PNG，未测整PDF上传和跨页合并；10.94秒包含单图上传与返回，不能与MinerU两页用时直接比较。官方Space代码revision记录在[paddle运行记录](paddle16-demo-run.json)，不是百度商业API的账单/稳定性验证。首次原始HTTP请求缺少公开Gradio配置中的State参数报错，补上空State后成功；不计作模型失败。

Logics官方Demo页面指向`alibaba-dt-logics-parsing.ms.show`，其公开`/config`和`/gradio_api/info`均返回HTTP403，停止尝试，没有绕过访问限制，未取得推理结果。

### 稳定性异常

standard首次原生线程报 `recursive_mutex lock failed: Invalid argument`，已生成ZIP仍不等于成功运行。曾另有一次误用已存在输出路径导致exit1、没有新解析产物，不能计为质量复验；[运行清单](local-runs.json) 明确区分这次操作错误。使用独立输出路径后的真实复验exit0，只能证明异常非必现，生产部署仍需定位和持续测试。

本机 Apple M4 / 16GiB / Python3.12.13；模型文件约2.10GB，隔离目录含依赖与缓存约4.8GB。没有测峰值内存或长期吞吐。所有试验进程退出，未安装到产品环境，未改产品配置或数据库。

## 证据导航

- [线上执行事实](cloud-run.json)、[线上原始Markdown](mineru-free-pipeline.md)
- [Paddle官方Demo执行事实](paddle16-demo-run.json)、[Paddle原始Markdown](paddle16-demo.md)
- [本机环境](environment.json)、[basic输出](mineru-local-basic/markdown.md)、[standard输出](mineru-local-standard/markdown.md)
- [本地运行与产物Hash](local-runs.json)、[崩溃摘要](local-crash.txt)、[归档文件Hash](artifact-hashes.json)
- [MP官方榜摘录](benchmark-snapshot.json)、[Logics作者自报摘录](logics-author-snapshot.json)：与本次实测明确分开
- [部署和许可证核验来源](deployment-notes.md)

2026-10-08根目录整理：历史ZIP、执行事实及复现脚本已归档到本地私有目录 `docs/local-artifacts/文档解析早期实验/`；重复解包副本和安装／运行日志已删除。`local-runs.json` 的 `artifact` 保留当时的路径记录，现从上述归档目录的 `local/` 查找同名ZIP。本目录的小型样本和结果不变。私有归档由Git本地exclude排除，不提交供应商原始响应或敏感数据。测试结束已删除临时venv、模型和下载缓存，复现时需重新安装/下载。

## 复现

本目录评分只需要标准库：

```bash
python3 score_cells.py > cell-results.json
```

重新生成样本使用隔离Python环境安装`reportlab`和`pymupdf`，设置`PDF_TEST_FONT`为本地支持中文的TTF字体后运行`create_fixture.py`。脚本默认使用macOS Arial Unicode，其他平台必须配置字体；字体不同会改变版面/文件Hash，比较时应固定本目录已生成的PDF。

本地实际命令为`mineru-kit parse complex-numbers-two-pages.pdf -o <独立文件名>.zip --format zip --tier basic`，standard改为`--tier standard`；版本固定4.0.9，使用隔离配置和已校验权重。不能忽略退出码，仅判断ZIP是否存在。

依赖版本见[环境锁定记录](local-environment-lock.txt)，配置见[config.yaml](config.yaml)，下载权重清单与公开LFS对象Hash见[小模型](onnx-files.json)及[GGUF](gguf-files.json)。免费线上复现脚本为[run_mineru_fixture.py](run_mineru_fixture.py)和[run_paddle_demo_state_slot.py](run_paddle_demo_state_slot.py)，只在显式运行时上传本目录合成样本；它们使用2026-09-29公开接口，Demo升级后需重新核对契约，不属于产品适配器。

正式横评还需：同一授权样本集、所有服务精确模式、目标GPU峰值资源、重复请求/并发、跨页续表、文件上限与部分失败、费用账单核对。没有这些数据前不承诺最优供应商。
