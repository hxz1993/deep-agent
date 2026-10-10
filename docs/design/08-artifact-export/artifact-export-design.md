# Artifact 与出站产物设计

> 更新：2026-08-20
> 状态：工具结果Artifact与下载中心文本发布已实施；沙箱二进制产物待真实需求触发

## 1. 已实现对象

v0.4.0 的 `artifact_id`就是DeepFOS平台文件服务返回的File ID，不再增加一层内部ID或映射表。
该对象只表示“当前会话里某次工具调用被外置的完整文本结果”。

| 信息 | 保存位置 |
| --- | --- |
| 完整UTF-8结果 | 平台DL文件 |
| `artifact_id`、原始字节数、角色 | 原始Tool Return metadata |
| 模型可见预览与结构草图 | Tool Return content |
| Console展示元数据 | SSE/History的可选 `artifacts` |

不建立Artifact表。当前会话的Artifact数量很少，读取授权通过扫描该Session完整原始History即可，
避免多一次数据库索引和File ID映射。

## 2. 写入与读取链路

```text
Harness ToolOutputLimits.after_tool_execute
  → Token超过动态阈值
  → PlatformToolResultStore.write
  → 平台DL上传
  → Harness生成预览并把File ID写入ToolReturn.metadata
  → Pydantic AI持久化缩短后的Tool Return

模型调用 read_tool_result(handle, offset, limit, from_end, pattern)
  → Store确认handle存在于当前Session原始History
  → 同Run缓存命中则直接读取
  → 否则平台下载一次并缓存
  → DeepAgent读取工具按行/尾部/字面关键词返回Token预算内切片
```

Spill和Store继续复用Harness的保存协议。`read_tool_result`通过公开`get_toolset()`扩展点提供，
与`read_file`共用24K/48K估算Token分页，正文与包装一起计入；不修改Harness安装包。
模型窗口小于256000或未知用24K，至少256000用48K。默认不限制源行数，
显式`limit`和原来的授权、字面匹配、尾读、`next_read/previous_read`续读语义保留。
普通大结果外置阈值及2000字符预览不随回读量调整改变。

## 3. 权限与压缩

- 当前Session原始Tool Result Event是Artifact授权事实源。
- 摘要Evidence只保存模型需要的预览与来源索引，不能恢复或扩大Artifact权限。
- 未来分支只有在新Session事实源确实包含对应Tool Result时才能获得句柄。
- 平台下载仍携带当前Run可信转发Header；句柄本身不能绕过Session归属检查。
- 日志不记录句柄和完整内容。

## 4. 失败语义

| 场景 | 行为 |
| --- | --- |
| 平台上传失败 | Harness回退到8K字符截断，不生成Artifact |
| 模型猜测句柄 | `read_tool_result`返回无可用结果提示，不访问平台 |
| 平台文件已丢失 | 返回结果不可用提示，Run可继续或重跑原工具 |
| 取消写入/读取 | 原样传播取消，终止当前Run |
| 同Run并发读取 | single-flight，只下载一次 |

## 5. 下载中心发布（v0.4.18）

运行时系统工具：

```python
export_file_to_download_center(
    source={"type": "content", "content": "# 报告"},
    file_name="report.md",
)

export_file_to_download_center(
    source={"type": "platform_file", "file_id": "..."},
    file_name="existing-report.html",
)
```

- `content`只接受完整UTF-8文本；先上传平台`DL`文件，再保存下载中心业务记录。
- `platform_file`只接受当前会话附件或大工具Artifact；复用原File ID，不重新上传。
- 平台`fileDescription`实际是下载中心展示与下载使用的最终文件名，必须和`file_name`一致并保留扩展名。
- 当前请求必须有可信的user、App和Space；无上下文时工具不披露，执行时仍复检。
- 新上传后登记失败会尽力删除本次文件；已有文件登记失败不得删除来源文件。
- 结果是普通小型Tool Result，包含下载记录ID、File ID、文件名和字节数；Final协议不变。
- 工具成功说明文件已进入下载中心，模型最终正文应告知文件名和获取位置。

该链路不建立Artifact表。大工具Artifact授权仍从原始History恢复，下载中心记录不能反向扩大
`read_tool_result`或`read_file`权限。

## 6. 当前非目标

- 聊天内文件卡、Markdown下载链接或HTML预览。
- 下载中心列表、删除和转存管理工具。
- 独立Artifact数据库表、版本或File ID映射表。
- TTL、孤儿文件清理、平台级配额和统计。
- 沙箱输出文件自动晋升。
- MCP直接返回二进制Artifact。

这些能力只有出现真实业务需求后再设计，不能为了“完整Artifact平台”扩大v0.4.0的安全兜底范围。

## 7. 未来通用计算产物

未来若要求Python/Skill脚本输出Excel、图片或压缩包，应在宿主层把受控workdir中的文件上传到平台，
再向前端返回可下载元数据。该链路可以继续复用平台File ID，但必须另行确定文件归属、保留周期、
下载权限和清理策略；不会把二进制内容塞入 `read_tool_result`。
