# 08 · Artifact 与出站产物

> 首次整理：2026-08-02
> 当前状态：**v0.4.0 已实现会话内大工具结果Artifact；v0.4.18 已实现主动发布文件到下载中心；v0.6.0 增加协议一致的本地文件运行时**

## 一句话

超大工具返回通过Harness `ToolOutputLimits`外置到平台文件服务，平台File ID直接作为当前会话的
`artifact_id`。v0.4.18在其上增加独立出站动作：模型可通过`export_file_to_download_center`新建文本
文件，或把当前会话已有平台文件登记到下载中心；两者不合并成新的Artifact平台。

## 已实现：工具结果Artifact

```text
工具返回超过阈值
  → 完整内容上传平台DL文件
  → Tool Return保存预览 + artifact_id
  → Harness read_tool_result按需分页/检索
```

稳定边界：

- 不增加Artifact数据库表，Tool Return metadata和平台文件是现阶段唯一事实。
- 权限从当前Session原始Tool Result Event恢复，不从摘要Evidence或模型参数恢复。
- 同一Run复用下载缓存；跨Run重新校验Event并按需下载。
- Console只提示完整结果已外置，不提供下载按钮。
- 当前不提供TTL、孤儿扫描、全局列表、统计或通用Artifact CRUD。

完整实现见
[`../05-tool-optimization/tool-optimization-design.md#92-tooloutputlimits-与大工具结果外置`](../05-tool-optimization/tool-optimization-design.md#92-tooloutputlimits-与大工具结果外置)。

## 已实现：发布到下载中心

```text
新UTF-8文本
  → 上传平台DL文件
  → 登记当前用户下载中心

当前会话附件或大工具Artifact
  → 复用原File ID
  → 只登记下载中心，不重复上传
```

工具只在可信user/App/Space上下文中直接披露，不进入Agent能力配置。已有File ID必须来自当前Session
原始History中的附件或大工具Tool Return；模型猜测的ID不会访问平台。平台模式仍不新增聊天内文件卡、
预览、下载链接、下载中心转存按钮或独立Artifact表。

v0.6.0在`local_dev + SQLite`中可显式选择本地FileStore：工具名、参数和返回结构保持不变，本地模式
写入Blob并创建`local_record_`下载记录，Console仅对`local_`文件展示鉴权下载链接。平台模式仍走
原Platform File与Space Download Center链路，且本地下载端点固定返回404。

## 尚未实施：通用二进制计算产物

`execute_python`或Skill脚本生成Excel、图片、压缩包等可下载文件，属于不同的出站产物需求。
未来若启动，仍应遵守：

```text
沙箱只写受控workdir
  → 宿主在清理workdir前上传平台文件
  → 返回可授权的产物句柄
```

它不能直接复用“工具返回文本过大”的全部语义，因为还涉及文件名、MIME、二进制、用户下载、
业务归档和清理策略。是否复用同一平台File ID、是否增加索引表，等真实下载需求出现后再决定。

## 与现有文件能力的分工

| 能力 | 方向 | 当前入口 | 是否已实现 |
| --- | --- | --- | --- |
| 会话附件/Skill资源 | 入站读取 | `read_file` | 是 |
| 超大工具文本结果 | 模型上下文外置与回读 | `read_tool_result` | v0.4.0 |
| 新生成UTF-8文本 | 出站下载中心 | `export_file_to_download_center` | v0.4.18 |
| 已有附件/大工具Artifact | 出站下载中心 | `export_file_to_download_center` | v0.4.18 |
| 沙箱生成二进制文件 | 出站下载/归档 | 尚无 | 否 |
| PDF/PPT专用解析 | 入站复杂文档理解 | 尚无 | 否 |

## 文档导航

- [`artifact-export-design.md`](artifact-export-design.md)：已实现链路和未来出站边界。
- [`../TODO.md`](../TODO.md)：跨课题状态台账。
