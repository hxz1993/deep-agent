# 文档证据ZIP真实验证（2026-10-12）

本目录脚本调用生产解析、证据生成、ZIP打包、分页与File Store实现。测试输出写到Git忽略的私有目录，合同、原始响应、临时图片URL及模型消息不提交。验证不修改应用全局配置、不保存业务表单。

## 结果与边界

- 新Paddle在线：使用生产请求参数和本地只读配置，答案确定的一页样例仅提交一次；1页、2 Block、1张干净处理图，ZIP 52,599字节。
- GLM：复用2026-10-11一次真实成功的带图请求（`need_layout_visualization=true`、`return_crop_images=true`）；本轮不重复OCR收费。经当前生产解析器、实际URL下载和ZIP导出，1页、1 Block、1张原生带框可视化图，ZIP 117,832字节。不能把此前接口实测写成本轮新GLM线上请求。
- 两家小样都通过解包后的图片Hash、图片尺寸、完整JSON Schema、正文和Block引用一致检查。已查看基于ZIP中真实图像叠加JSON bbox的效果：Paddle的2段和GLM的1整段框均贴合；GLM已有的红色框与新画的蓝色框位置一致。
- 两家图像均是供应商自己的坐标平面。本次没有实现或验收回投原PDF的图像配准。

## 六份合同历史原生响应重放与实际图片下载

复用固定6份合同、两家产物，共12份解析、384解析页。Paddle从原始JSONL重新读取顶层`preprocessedImages`，GLM从原始`response.json`重新读取；均未重新OCR。图片下载使用生产限制，成功文件经过真实隔离LocalFileStore上传、下载、SHA256和JSON Schema核对。

| 来源页数 | Paddle图片保存 | Paddle ZIP字节 | Paddle 48K布局读取次数 | GLM图片 | GLM ZIP字节 | GLM 48K布局读取次数 |
|---:|---:|---:|---:|---|---:|---:|
| 6 | 6/6 | 1,582,277 | 1 | 原响应未提供 | 5,493 | 1 |
| 24 | 24/24 | 9,041,254 | 2 | 原响应未提供 | 34,080 | 1 |
| 30 | 30/30 | 10,967,035 | 2 | 原响应未提供 | 30,601 | 1 |
| 31 | 31/31 | 10,122,230 | 2 | 原响应未提供 | 39,979 | 2 |
| 46 | 46/46 | 18,718,803 | 3 | 原响应未提供 | 46,097 | 2 |
| 55 | **34/55，partial** | 20,291,295 | 3 | 原响应未提供 | 54,928 | 2 |

**55页图片不完整是本地现有20MiB上传预算触发，不是URL过期或OCR缺页。** 第35页触发`result_too_large`，其后20页明确`download_budget_exceeded`；完整55页JSON和741个Block仍保留。所有成功保存的Paddle图片尺寸均与供应商坐标平面一致（171张matched），没有伪装缺失图片为成功。

所有12份均核对：导出JSON正文、原生正文、Block及ID与打包前固定证据对象一致；48K分页逐字符连续重建；同一解析并发导出及后续读取只构建/上传一次、复用同一引用。GLM历史原响应未请求可视化，因此0/192图如实`not_provided`，未重新OCR补图。

## 真实模型与工具链

使用本地已有`deepseek-flash`、同一工具指令与新Run，供应商解析边界复用已记录的真实原始结果；**模型调用、生产工具、图片下载、ZIP打包、LocalFileStore上传下载均真实执行**。模型未收到金标准。使用既有`evidence-layout-20261011/run_agent_validation.py`。

| 场景 | 工具调用 | 连续读完整次数 | 导出上传 | 总耗时 | 检查 |
|---|---:|---:|---:|---:|---|
| Paddle一页 | 2 | 1 | 1 | 4.654秒 | 金额123.45、文件/Block引用、Hash正确 |
| GLM一页 | 2 | 1 | 1 | 9.736秒 | 金额123.45、文件/Block引用、Hash正确 |
| Paddle46页 | 4 | 3 | 1 | 24.838秒 | 全范围连续读完，446 Block，导出复用 |
| GLM46页 | 3 | 2 | 1 | 16.883秒 | 全范围连续读完，405 Block，导出复用 |

工具调用数包含完整读取后的第一页复读，验证文件引用复用。输入Token为累计多轮统计，Paddle46页245,170、GLM46页148,407，不是单次工具返回大小。四个Run均通过完整范围、最终引用和ZIP下载Hash验证；没有把最后一次`has_more=false`单独当作读完整证据。

当前这四次是**实际模型+冻结OCR结果**验收，不是四次新OCR，也不等于租赁金额业务映射或平台FileStore真实环境验收。长合同本次不重新评分OCR/业务准确率。

## 接口、回归与交付验证

- 完整单元回归：2310项、零失败、8项跳过；Console 778项通过。另有文档History与Runner相关集成4项、文件与附件／压缩后读取集成12项通过。
- 独立只读审计：bundle、export、layout、providers、evidence及真实History101项通过；旧v1 JSON历史实际序列化和文件授权引用恢复通过。
- 真实Test平台文件闭环：隔离一页样例PDF上传、下载，再通过生产打包和PlatformFileStore单次上传ZIP；并发与重复请求仅上传一次。下载字节、SHA256及v2 Schema一致，耗时1.403秒；源文件与ZIP均已删除。
- Alpha配置没有本地可信身份所需字段，因此未发起Alpha文件请求；本轮不部署或修改Alpha配置。
- 96次历史合同24K/48K普通与布局视图完整重建通过，正文及Block ID不因打包改变。

- 本地8000/8010统一服务已重启至0.13.1，健康与Console通过。真实HTTP上传一页样例→模型默认读取→开启布局与导出→再次导出，共3次工具调用；ZIP 52,621字节，整体14.399秒。默认不上传、两次导出同一引用、金额123.45、源文件Hash、图片Hash、Schema、Block和History核对通过；临时Agent、会话及文件全部清理。
- 两家合成v2样例的本地源ID已统一使用`local_`前缀，Schema同时限定源附件和导出文件的本地／平台身份；v1历史快照未改。

## 复现

在仓库根使用项目`.venv`（系统Python可能装有旧Pydantic AI）：

```bash
PYTHONPATH=src .venv/bin/python docs/design/37-document-parsing/evaluation/evidence-bundle-20261012/run_bundle_validation.py \
  --mode online-paddle --output 'docs/local-artifacts/Report 文档证据ZIP验证/TMP to delete validation'

PYTHONPATH=src .venv/bin/python docs/design/37-document-parsing/evaluation/evidence-bundle-20261012/run_bundle_validation.py \
  --mode replay \
  --fixtures docs/design/37-document-parsing/evaluation/lease-contract-parsers/product-runtime-20261002/local-private \
  --output 'docs/local-artifacts/Report 文档证据ZIP验证/TMP to delete validation'
```

`glm-sample`模式另传私有`--glm-response`和`--glm-source`。在线模式检测已有attempt即拒绝重新提交，失败或结果未知必须先检查记录；不能仅换输出目录自动重试。历史模式只重新下载已有产物URL，不提交OCR。在线和历史的原始产物、完整模型消息保留在本机私有归档供复核。
