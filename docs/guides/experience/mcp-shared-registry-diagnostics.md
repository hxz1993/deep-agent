# MCP 共享 Registry 诊断日志 — 实现经验

> 本文档提炼自 2026-07-16 早间 deepcube 全量报错事故，记录 **`McpSharedRegistry` / `McpLeaseManager` 诊断日志** 的目录、含义、grep 用法与排障 runbook，供后续类似事故快速定位。

相关文档：[`../../../src/deep_agent/AGENTS.md`](../../../src/deep_agent/AGENTS.md)（`capabilities/mcp/` 索引）、[`../../../mcp_servers/AGENTS.md` 中 DeepCube 段](../../../mcp_servers/AGENTS.md#deepcube)builtin-server 配置。

---

## 1. 事故复盘（一句话）

2026-07-16 10:09 左右，**两个 gunicorn worker 同时抢 deepcube 共享文件锁**，worker A 在 `McpSharedRegistry.ensure_server` 持锁段停留 >30s（subprocess bind 卡顿），worker B 的 `ensure_server` 在 `filelock.FileLock.acquire(timeout=30s)` 等满 30s 后抛 `TimeoutError`，两次 Agent 请求失败、所有涉及 deepcube 的能力不可用。重启 Pod 后恢复，但孤儿锁文件未清理。

修复方式见第 3 节"修复内容"。

---

## 2. 设计原则

| 原则 | 说明 |
|------|------|
| **可追溯** | 每条 WARNING/ERROR 日志都带 server_name + session_id 或 path，事故复盘时能 grep 出因果链 |
| **不刷屏** | 成功路径只打 DEBUG；超过阈值（默认 5s）的慢操作才升 INFO/WARNING |
| **可定位耗时** | `ensure_server` 关键节点（锁等待、子进程 spawn、bind）都有 `time.monotonic()` 测量 |
| **PID 与 TCP 双查** | `_record_is_live` 加 PID 活性探针，防止"端口被新进程占用但旧 pid 仍在册"的孤儿记录 |

---

## 3. 修复内容（commit 后的关键改动）

### 3.1 重试 + 锁竞争吸收（`lease_manager.py`）

`lease()` 入口的 `ensure_server` 调用加 1 次 `TimeoutError` 重试，复用现有 `mcp_acquire_retries`（默认 1）。锁等满后先 `asyncio.sleep(0.5s)`，再走第二次。第二次仍超时则按原行为抛 `TimeoutError`。

### 3.2 PID 活性检查（`shared_registry.py:_record_is_live`）

在原有的 `_tcp_open` 之前，先用 `os.kill(pid, 0)` 查记录的子进程是否还活着。返回 `False` 时打 WARNING 并把该记录视作 stale，由下次 `ensure_server` 走 `drop_stale_record` 清理。`None` / 缺字段视为"未知"，保持向后兼容（老记录不被强制驱逐）。

### 3.3 诊断日志编排

按 log 级别分层：

| 级别 | 触发场景 | 出现频率 |
|------|---------|---------|
| `DEBUG` | 入口、锁获取后、子进程 PID | 每次调用 |
| `INFO` | 子进程启动、就绪（带 spawn / 总耗时）、重用现有 record 时 >5s 或重试后成功 | 少量 |
| `WARNING` | 锁等待 ≥5s、slow 阶段超时后重试、staleness 检测触发、孤儿 PID | 异常路径 |

`_ENSURE_SLOW_LOCK_ACQUIRE_SECONDS = 5.0` 是默认阈值，可在 `shared_registry.py` 头部调。

---

## 4. 日志目录（grep 直接抄）

### 4.1 Registry 入口与生命周期

| 日志前缀 | 文件:行 | 含义 | 关键字段 |
|----------|--------|------|---------|
| `MCP record <name!r> at <path> marked stale (pid=<pid> port=<port>); will attempt cleanup` | `shared_registry._drop_stale_record` | 检测到死亡/孤儿记录，准备清理 | name, path, pid, port |
| `Dropped stale MCP registry record at <path>` | 同上 | 实际 unlink 成功 | path |
| `Skipped stale MCP record cleanup: lock busy (<path>)` | 同上（DEBUG） | 别的 worker 正在持锁，跳过本次清理 | path |
| `MCP ensure_server <name>: about to wait for shared lock at <path> (timeout=<X.X>s)` | `ensure_server`（DEBUG） | 即将抢锁 | name, path, timeout |
| `MCP ensure_server <name> acquired shared lock in <X.X>s` | 同上（DEBUG） | 抢到锁（快路径） | name, 等待秒数 |
| `MCP ensure_server <name> waited <X.X>s for shared lock at <path>` | 同上（**WARNING**, >5s） | 锁等待超过阈值，事故信号 | name, 等待秒数, path |
| `MCP ensure_server <name> timed out acquiring shared lock after <X.X>s at <path>` | 同上（WARNING, on TimeoutError） | 真的抢锁失败 | name, 等待秒数, path |
| `Started shared MCP server '<name>' at <url> (pid=<pid>)` | 同上（INFO） | 子进程已 spawn，监听中等待 bind | name, url, pid |
| `MCP shared server '<name>' pid=<pid> ready at <url> (spawn=<X.X>s total=<X.X>s)` | 同上（INFO） | bind 成功，可服务 | name, pid, url, spawn/total 秒数 |
| `MCP ensure_server '<name>' total <X.X>s` | 同上 `finally`（INFO, >5s） | 端到端耗时异常 | name, total |
| `MCP shared server '<name>' reused existing record url=<url> pid=<pid> (no spawn)` | 同上（DEBUG） | 命中已有活记录，跳过 spawn | name, url, pid |

### 4.2 Pid 活性（重点）

| 日志前缀 | 文件:行 | 含义 |
|----------|--------|------|
| `MCP shared registry record '<name>' port=<port> has dead pid=<pid>; treating as stale` | `_record_is_live`（**WARNING**） | 记录的 pid 已死，立即判 stale |

> 这是事故复盘的"凶手指纹"。`name != "deepcube"` 通常是测试/内置 MCP；`name == "deepcube"` 则代表 deepcube 子进程崩溃但 registry 仍指向它。

### 4.3 Lease 重试（重点）

| 日志前缀 | 文件:行 | 含义 |
|----------|--------|------|
| `MCP ensure_server timeout for '<name>' session='<sid>' attempt=<N>/<M> waited=<X.X>s; retrying in <Y.Y>s` | `_ensure_server_with_retry`（**WARNING**） | 第一次 `ensure_server` 抛 TimeoutError，准备重试 |
| `MCP ensure_server exhausted retries for '<name>' session='<sid>' attempts=<M> last_attempt=<X.X>s` | 同上（WARNING, on raise） | 重试也没救起，按原样抛 `TimeoutError` |
| `MCP ensure_server '<name>' session='<sid>' attempt=<N>/<M> took <X.X>s url=<url>` | 同上（INFO, retry 后成功 或 >5s） | 重试后的成功路径，慢操作留痕 |

---

## 5. 事故排障 runbook

### 5.1 "今天早上一片 deepcube 报错" 类（最常见）

**症状**：用户报"涉及 deepcube 的请求全部失败"，日志看到 `Agent stream failed for session ...` + 栈底是 `TimeoutError: Timed out acquiring MCP shared lock`。

**最短 grep 路径**：

```bash
# 1. 锁等待是否超 5s
grep 'MCP ensure_server .* waited [5-9]\|1[0-9]\|2[0-9]\.' pod.log

# 2. 是否真的 TIMEOUT
grep 'MCP ensure_server .* timed out acquiring shared lock' pod.log

# 3. 是否触发了重试并救活
grep -E 'MCP ensure_server .* (retrying|exhausted retries|attempt=2/2)' pod.log

# 4. 死的还是孤儿 pid
grep 'MCP shared registry record .* has dead pid' pod.log
```

**判别**：

| grep 结果 | 结论 |
|-----------|------|
| 看到 `exhausted retries`，且 worker 数量 >1 | A：worker 竞争 → 拉大 `MCP_SHARED_LOCK_TIMEOUT` 或先观察（重试已可吸收短时抖动） |
| 看到 `waited Xs for shared lock` 长时间持续 | B：A 内部 subprocess 卡了 → 比 B-1 进一步排查 |
| 看到 `has dead pid` 频繁 | C：deepcube 子进程反复崩 → 查 `dim_init_max_concurrency`、`numpy<2`、`deepcube-mcp` 包版本 |

### 5.2 锁内部 hang（`waited Xs` 但没 `timed out`）

**症状**：WARNING 持续刷 `waited Xs for shared lock`，但还没 30s 阈值抛 TimeoutError。

**进一步排查**（在 Pod 内执行）：

```bash
# 1. Pod 内子进程状态
ps -ef | grep -E 'deepcube|server\.py' | grep -v grep

# 2. 孤儿锁文件检查
ls -la /tmp/deep-agent-mcp/
fuser -v /tmp/deep-agent-mcp/builtin-deepcube.lock 2>/dev/null || echo 'no flock holder'

# 3. 子进程 bind 卡哪
/usr/local/bin/python -c "import time; from deepcube.mcp.server import mcp; t=time.time(); mcp; print('import', time.time()-t, 's')"

# 4. 上游服务可达性（已知 deepcube 依赖）
curl -m5 -sS -u admin123:12345678 http://eureka/eureka/apps -o /dev/null -w 'eureka:%{http_code}/%{time_total}s\n'
curl -m5 -sS -o /dev/null -w 'element-info:%{http_code}/%{time_total}s\n' -X POST \
  http://10.251.29.132:80/element-info/get-server-names \
  -H 'Content-Type: application/json' -d '{}'
```

> 期望：eureka < 1s；其余 < 1s。若 eureka 持续 >3s，找运维/网络；若是 `import deepcube.mcp.server` 慢，看是否混入了 numpy 2.x。

### 5.3 排查模板（直接复制）

```bash
# 在出问题的 Pod 内，一条命令打包：
echo "=== ensure_server slow ===" && \
grep -E 'MCP ensure_server .* (waited|total|ready)' pod.log | tail -20 && \
echo "=== ensure_server timeout ===" && \
grep -E 'MCP ensure_server .* timed out|exhausted' pod.log | tail -20 && \
echo "=== dead pid ===" && \
grep 'has dead pid' pod.log | tail -20 && \
echo "=== current state ===" && \
ps -ef | grep -E 'deepcube|server\.py' | grep -v grep && \
ls -la /tmp/deep-agent-mcp/
```

将上面输出贴回，下次事故定位时间从 30+ 分钟缩到 < 5 分钟。

---

## 6. 配置旋钮（已对齐 `OPTION.agent.*`）

| OPTION | 默认 | 何时调整 |
|--------|------|---------|
| `mcp_shared_lock_timeout` | `30s` | 出现 `exhausted retries` 持续无法吸收时拉到 60–120s（事故止血用） |
| `mcp_init_timeout` | `30s` | 子进程冷启需要更长时（首次扩容 / 慢机器） |
| `mcp_acquire_retries` | `1` | 当前重试模型基于此值；不推荐调高，会拉长最大延迟 |
| `mcp_runtime_dir` | `/tmp/deep-agent-mcp` | 锁/registry 文件位置，多 Pod 环境要确保 Pod-local |

阈值 `_ENSURE_SLOW_LOCK_ACQUIRE_SECONDS = 5.0` 与 `_ENSURE_SERVER_SLOW_THRESHOLD_SECONDS = 5.0` 是代码内常量（`shared_registry.py` / `lease_manager.py` 顶部），需要改需要发版。

---

## 7. 陷阱 / 已知 quirk

| 现象 | 检查 |
|------|------|
| Windows 上 `os.kill(huge_pid, 0)` 死锁 | 这只是 cpython 在 Windows 上的已知行为；生产 Linux 不受影响。`_pid_alive` 改用 `psutil.pid_exists`（`requirements.txt` 已加 `psutil>=5.9`），所有平台一致，Windows 测试全跑——之前的 7 个 skip 全部移除。 |
| 锁文件孤儿 | 持锁进程被 SIGKILL / OOM 时文件残留；当前靠 PID+TCP 双查自然恢复。如需立即清理：删 `/tmp/deep-agent-mcp/builtin-*.{lock,json}` 后 `kill -HUP $(cat /root/.gunicorn/gunicorn.pid)`。 |
| `_record_is_live` 老记录无 `pid` 字段 | 视为 `pid_alive=True`，仅靠 TCP 验证；与未改动前完全一致。 |
