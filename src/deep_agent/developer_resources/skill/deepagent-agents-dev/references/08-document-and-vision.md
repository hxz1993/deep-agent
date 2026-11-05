# 文档、图片与视觉核验

## 按目标选择工具

| 任务 | 首选 |
| --- | --- |
| 文本、代码、DOCX/XLSX、PDF文字层等读取 | read_file，按返回的双游标续读 |
| 扫描件、复杂文档结构和专业解析 | 已授权的parse_document，按供应商能力 |
| 图片、PDF整页布局、关键字段视觉核验 | 当前主模型支持视觉时使用view_image |

read_file对文本源码不按后缀封死，Python/JS/SQL等可读；二进制不会伪装成文本。PDF没有文字层或提取为空不能断言页面空白。专业解析失败也不应引导模型无休止编写Python重复OCR。

存在has_more时，必须同时把next_offset和next_char_offset复制为下一次的offset和char_offset，防止长项内的正文被跳过或重复。offset从1开始，char_offset从0开始；limit是源项数，不是字符数。文本按行、DOCX/PPTX按逻辑块、XLSX按物理行、PDF按物理页，不能把一种文件的游标套到另一种文件。

## 图片与PDF原页

```json
{
  "items": [
    {"resource": {"type": "attachment", "file_id": "<授权PDF ID>"}, "pages": [1, 3]},
    {"resource": {"type": "skill", "skill_code": "<已关联Skill code>", "path": "assets/chart.png"}}
  ]
}
```

view_image的PDF页码从1开始，指物理页；省略默认第1页，不自动展开全文。图片不传pages。可按目标限额一次多图比较，并在同一Run多次查看后继续分析；下轮需要原图时重新调用。GIF首帧等格式边界以工具说明为准。

公网URL来源仍需Web授权，不能因为新工具支持URL就访问私网、转发Cookie或自定义Header。平台File ID必须属于授权会话或可信产物；Skill来源仅限当前已关联资源。

view_image不在PTC内。Python/Skill/PTC生成PNG或PDF后先上传得到File ID，再由外层调用；不要把Base64塞入普通JSON冒充主模型已经看见。

## 解析结果如何核验

保留文档ID、物理页、block/source引用、表头、单位和原始字符。合计与关键数字用确定性计算核对，模糊字段标不确定；不能拿清晰版历史补出当前模糊页没看清的数字。

专业解析的布局坐标不一定对应原始PDF画布。要回原页确认时使用真实原文件/页码，不能将供应商展平结果的坐标直接画回原页。

parse_document的include_layout/export_file等按目标工具说明传入；导出的完整产物和结果分页不同。已上传但未完成登记下载中心、格式准备成功但模型未核验，均分别陈述。

## 用例建议

含文字层PDF、无文字层矢量PDF、纯扫描PDF、旋转页、模糊数字、多页表格、损坏/加密文件、权限失效及多图来源对比。负向用例应明确拒绝或标不确定，不造数、不补零，也不悄悄切到另一个辅助视觉模型。
