# Artifact 交互语义 — 设计纲要

> 日期：2026-08-02  
> 状态：骨架（非实施计划）  
> 入口：[`README.md`](README.md)

## 1. 核心对象

**Artifact** = 宿主托管的不透明字节对象 + 元数据，供 Agent 用短句柄引用。

| 字段（示意） | 含义 |
| --- | --- |
| `artifact_id` | 不透明 id |
| `byte_size` / `content_type` / `filename?` | 元数据 |
| `scope` | `request` \| `session`（第一版偏短寿命） |
| `created_by` | `sandbox_execute` \| `agent_register` \| … |
| TTL | 过期删除 |

合法 id：当前会话原始历史或宿主登记表——**不能**仅从 Checkpoint 推断。

## 2. 交互语义（与后接无关）

建议 Agent 可见能力（名称可改），默认关闭的独立 Capability：

| 意图 | 示意 |
| --- | --- |
| 登记 | 小内容直接 `register_artifact(bytes|text, meta)` → `artifact_id`（可不经沙箱） |
| 发现 | 沙箱 `result.artifacts[]` 带 id；或 `describe_artifact(id)` |
| 使用 | 下游 tool 只收 `artifact_id`，由宿主读字节再做事 |

**不规定**「使用」必须等于「上传下载中心」。

## 3. 链路

### 3.1 语义主干（A0/A1）

```text
来源 A：execute_python 大产物 → 晋升 → artifact_id 进 result
来源 B：模型/tool 直接 register 小内容 → artifact_id
        ↓
后续任意消费者（另一 tool / sink / 前端）只认 artifact_id
```

### 3.2 可选 sink：下载中心（S0）

```text
publish_to_download_center(artifact_id, file_name?)
  → 宿主读 Artifact
  → PlatformAPI.file.upload(DL) + SpaceAPI.business.save
  → 返回 { file_id, ... }（平台侧引用，可与 artifact_id 并存）
```

其他 sink 同构：`publish_*(artifact_id) → 外部句柄`。

## 4. 与现有表面的分工

| 现有表面 | 角色 | 本课题 |
| --- | --- | --- |
| `POST /uploads` + `attachment` | **入站**会话附件 | 不合并；对照 `file_id` 纪律 |
| `python-sandbox` | 无状态计算 | 大结果只增加晋升 → Artifact |
| deepfos `export_file_for_download` | 脚本侧下载中心两步 | **仅作 S0 参考实现**，非课题必达 |
| Harness Spill | 大 tool 结果外置 | 概念对照；对象层是否共用待决 |

## 5. 与 07 workdir cleanup

见 README「交叉合同」。硬规则：`result` 中的 `artifact_id` 必须在 workdir 删除前完成晋升；晋升失败则不得留下半引用。

## 6. 开放问题

1. 存储：单实例本地 vs 多实例共享。  
2. A0 是否提供宿主内 `read_artifact` 分页（给模型），还是只允许 sink 读。  
3. 第一版是否上 S0，还是只交付句柄语义。  
4. `artifact_id` 与平台 `file_id` 是否允许在 publish 后建立映射表。  
5. 与 Spill / `OverflowingToolOutput` 是否共用 Store。  
6. MCP 是否允许直接产出 Artifact（可后置）。

## 7. 非目标

- 沙箱网络白名单  
- 把下载中心当成唯一成功标准  
- 用 stdout/`result` 分片传大二进制  
- 替代 06 入站附件产品  
