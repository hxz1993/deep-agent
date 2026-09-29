# 本地部署核验（2026-09-29）

## 版本与运行路径

- 本机实测：Apple M4、16GiB unified memory、macOS 27 arm64、Python 3.12.13、MinerU 4.0.9。
- 官方最新MinerU主线4.0，basic为布局/OCR/表格/公式小模型完整管线；standard/advanced另加VLM。
- 本次basic用ONNX CPU；standard用相同小模型+官方代码指定MinerU2.5-Pro-2605-1.2B-Q8_0 GGUF/llama.cpp。不是纯文本Flash，也不是只有裸VLM。
- 小模型仓库文件总大小858,220,651 bytes；GGUF main531,066,496 bytes+mmproj709,409,600 bytes。实际weights模型约2.10GB，另有Python依赖/cache。
- 所有venv、模型、缓存、配置都在当前TMP目录；没有修改产品环境、全局依赖或数据库。
- Context7解析返回旧pipeline命令；实际操作以4.0.9本地CLI --help及官方主线为准。

## 资源与许可证

MinerU:
- 官方建议Apple Silicon至少16GB统一内存；NVIDIA约8GB显存只是规划起点，不是所有负载保障；basic可CPU。
- https://github.com/opendatalab/MinerU/blob/master/docs/en/usage/tiers.md
- 新版许可是基于Apache2的MinerU自定义许可；合并关联方MAU超1亿或月收入超2000万美元需单独许可；对第三方提供在线服务必须在界面或公开文档显著声明使用MinerU。不要再写成AGPL或纯Apache2。
- https://github.com/opendatalab/MinerU/blob/master/LICENSE.md

PaddleOCR:
- 官方最新VL-1.6，架构仍0.9B，完整管线先布局裁块、再VLM；直接给模型整页或只部署vLLM并不等于官方完整pipeline。
- https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.en.md
- macOS Apple Silicon有官方完整本地运行文档，M4已验证；PaddlePaddle>=3.2.1 + paddleocr[doc-parser]；Apple Silicon无官方Docker路径；加速可使用MLX-VLM服务但仍通过PaddleOCRVL pipeline调用。
- https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL-Apple-Silicon.en.md
- 代码Apache2；VL1.6模型卡也标Apache2。未做本地Paddle实测，不能把0.9B权重大小当成实际全pipeline峰值内存或显存要求。
- https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE
- https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6

Docling:
- 代码MIT；模型单独授权，docling-models同时列CDLA-Permissive2.0/Apache2；不能仅凭代码MIT声称所有组件MIT。
- https://github.com/docling-project/docling/blob/main/LICENSE
- https://huggingface.co/docling-project/docling-models
- CPU/MPS/CUDA/XPU可配置；本次未实測Docling，不給固定最低内存或吞吐承诺。
- https://docling-project.github.io/docling/_generated/examples/run_with_accelerator/
