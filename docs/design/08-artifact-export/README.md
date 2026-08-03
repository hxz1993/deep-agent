# 08 · Artifact 交互语义

> 首次整理：2026-08-02  
> 当前状态：**骨架设计**（核心是 Artifact 句柄语义；后接 sink 可插拔，下载中心只是候选之一）

## 一句话

**算在沙箱，存经宿主，传用句柄。**  
课题本体是 Agent / Tool 之间的 **Artifact 交互语义**（登记、引用、读取范围、TTL、权限）。  
下载中心、对象存储、仅会话内二次读取等，都是挂在句柄后面的 **sink / 消费者**，不锁死第一种。

## 文档导航

| 文档 | 用途 |
| --- | --- |
| 本 README | 课题入口、原则、边界表、分期、与 06/07 交叉点 |
| [`artifact-export-design.md`](artifact-export-design.md) | 链路示意、语义草图、可选 sink、开放问题 |

跨课题待办见 [`../TODO.md`](../TODO.md)（`ART-*`）。

## 动机

- 沙箱 stdout/`result` 有硬限额，不适合当「完整产物」通道；Agent 需要 **短句柄** 指代字节对象。
- 产物落地后可能去：**下载中心**、平台文件、前端下载、下一轮 tool 再读、甚至只给人类看——**后接未定，语义要先稳**。
- 沙箱禁网；字节进出平台 / 对外系统必须在 **宿主** 完成。
- 入站 `attachment`（按 `file_id` 读会话附件）≠ 本课题的 **出站 / 计算产物** Artifact（可共享存储技术，产品语义分开）。

## 原则

1. **Artifact 是一等语义**：不透明 `artifact_id`、元数据、作用域、TTL；history 传句柄不传大包。
2. **Sink 可插拔**：下载中心只是一种 `publish(artifact_id) → 外部引用`；第一版可以只做「登记 + 宿主内读取」，不接任何外部系统。
3. 沙箱无网络、无平台 API；与 07 的咬合是 **workdir 生命周期 vs Artifact 晋升时机**。
4. Artifact 是**显式外置状态**，不破坏「每次 `execute_python` 无状态」。
5. 合法 id 不得从 Checkpoint 猜造（对齐附件 `file_id` 纪律）。

## 边界表

| 维度 | 本课题做 | 本课题不做 |
| --- | --- | --- |
| 核心 | Artifact 登记 / 引用 / TTL / 权限 /（可选）分页读 | 规定唯一必须接下载中心 |
| 沙箱 | 大产物晋升为 Artifact；`result` 只回句柄列表 | 沙箱白名单出网；沙箱写宿主任意路径 |
| 小摘要 | 仍可用 stdout/`result` 给模型看 | 用多轮 result 拼大二进制 |
| Sink | 可选适配器（下载中心、仅返回可读 URL、…） | 第一版就绑定全部平台产品形态 |
| 与附件 | 计算产物 Capability 独立；默认关闭 | 与 `attachment` 读工具合并成一个 tool |
| 与 07 | 晋升合同；07 继续禁网 | 把 Artifact 实现塞进 go-judge 主路径当唯一交付 |
| Harness Spill | 概念对照；评估是否共用对象层 | 未共享存储前把 Spill 当已交付 |

## 与 07 的交叉合同（必须对齐）

```text
execute 开始 → 创建请求级 workdir
  → worker 运行（大产物写入晋升候选）
  → framing 返回 result（可含 artifact_id 列表）
  → 【晋升】宿主登记 Artifact
  → 删除 workdir
  → 后续 tool / sink 只认 artifact_id
```

未晋升的文件随 workdir 删除。阶段 C 须同一晋升语义。

## 建议分期（先语义，后 sink）

| 阶段 | 内容 | 依赖 |
| --- | --- | --- |
| **A0 · Artifact 最小语义** | id、元数据、scope/TTL、宿主登记 API；tool 可 `register` / `describe`（可选极小读取） | 存储落点待决；**可不接任何外部 sink** |
| **A1 · 沙箱晋升** | 与 07 workdir cleanup 挂钩；大结果出 `artifact_id` | A0 + 07 阶段 A |
| **S0 · Sink：下载中心（可选）** | `publish_to_download_center(artifact_id)` → upload + `business.save` | A0；对齐 deepfos 两步 |
| **S\* · 其他 sink** | 按产品需要加（例如只签发短时下载、写入别的业务实体） | A0 |
| **G0 · 治理** | 配额、审计、多实例共享存储、与 Spill/压缩协同 | 运维就绪 |

推荐：**先 A0（语义可用）→ 需要给用户「拿走文件」时再加 S0**；不要让下载中心倒逼 Artifact 模型。

## 相关课题

| 课题 | 关系 |
| --- | --- |
| [`07-python-sandbox`](../07-python-sandbox/README.md) | 计算侧；禁网；workdir ↔ 晋升 |
| [`06-attachment-parsing`](../06-attachment-parsing/README.md) | 入站读；`file_id` 纪律可对照 |
| [`05-tool-optimization`](../05-tool-optimization/README.md) | Spill / 大结果句柄概念对照 |
| [`03` 研究结论](../03-context-compaction/context-compaction-research.md) | 「Artifact 作业务实体独立设计」 |
| [`TODO.md`](../TODO.md) `ATT-202` / `ART-*` | 外置与出站待办分流 |
