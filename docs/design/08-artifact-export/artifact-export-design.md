# Artifact 与出站产物设计

> 更新：2026-08-13
> 状态：工具结果Artifact已实施；通用计算产物待真实需求触发

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
  → Harness按行/尾部/字面关键词返回受限切片
```

`read_tool_result`保留Harness原生实现。DeepAgent只实现 `OverflowStore.write/read`适配器，因此不复制
分页、关键词、单次行数和字符上限逻辑。

## 3. 权限与压缩

- `chat_messages`是Artifact授权事实源。
- Checkpoint会删除Tool Return metadata，只保存模型需要的预览投影；它不能恢复或扩大Artifact权限。
- 分支会话只在新Session原始History确实复制了对应Tool Return时获得句柄。
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

## 5. 当前非目标

- 通用下载中心、Artifact列表或CRUD。
- 独立Artifact数据库表、版本或File ID映射表。
- TTL、孤儿文件清理、平台级配额和统计。
- 沙箱输出文件自动晋升。
- MCP直接返回二进制Artifact。

这些能力只有出现真实业务需求后再设计，不能为了“完整Artifact平台”扩大v0.4.0的安全兜底范围。

## 6. 未来通用计算产物

未来若要求Python/Skill脚本输出Excel、图片或压缩包，应在宿主层把受控workdir中的文件上传到平台，
再向前端返回可下载元数据。该链路可以继续复用平台File ID，但必须另行确定文件归属、保留周期、
下载权限和清理策略；不会把二进制内容塞入 `read_tool_result`。
