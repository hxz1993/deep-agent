# MySQL 空闲连接超时配置与排障

> 适用范围：DeepAgent 使用 SQLAlchemy AsyncEngine + asyncmy + MySQL/MariaDB。
>
> 当前实现基线：DeepAgent v0.3.11、SQLAlchemy 2.0.51、asyncmy 0.2.11、uvloop 0.22.1。

## 先看结论

MySQL、数据库代理或中间网络设备可能主动关闭长时间空闲的 TCP 连接。应用连接池如果继续
复用这条连接，首次请求可能在 SQLAlchemy `pre_ping` 或连接关闭阶段失败。

DeepAgent v0.3.11 已实施三层保护：

1. `pool_pre_ping=True`：每次从连接池取出连接前检查是否仍然可用。
2. MySQL `pool_recycle=90`：连接创建超过 90 秒后，在下一次取出时主动换新。
3. 在 `pre_ping` 阶段精确识别 asyncmy/uvloop 的
   `TCPTransport closed=True` / `handler is closed` RuntimeError，并交给 SQLAlchemy
   废弃连接和重新建连。

其中 `90` 秒是针对 Test MySQL `wait_timeout=120` 的止血值，目前是代码常量，不是环境
配置项。它在 Test 安全，但不代表所有环境的长期最佳值。

## 典型现象

常见特征：

- 页面刚打开时接口正常，静置一段时间后首次刷新出现一批 500 或超时，再刷新又恢复。
- Agent、模型、会话和工具等多个数据库接口同时失败，不局限于某一张表。
- 日志出现：

```text
Exception terminating connection
RuntimeError: unable to perform operation on <TCPTransport closed=True ...>;
the handler is closed
```

- 失败时间与 MySQL `wait_timeout` 基本对齐。
- Worker 没有退出，数据库也没有重启；问题集中在连接池复用了服务端已经关闭的连接。

这类特征更符合连接生命周期问题，不应先按表结构、索引或单个 API 代码错误处理。

## 三个参数分别解决什么

### MySQL `wait_timeout`

数据库端允许非交互连接空闲的最长时间，单位为秒。它可以是全局值，也可以被会话级设置
覆盖。

```sql
SELECT
  @@GLOBAL.wait_timeout AS global_wait_timeout,
  @@SESSION.wait_timeout AS session_wait_timeout,
  @@GLOBAL.interactive_timeout AS interactive_timeout;
```

应用实际受到的上限还可能更短：数据库代理、负载均衡、NAT 或防火墙都可能提前断开 TCP。
因此，不能只看 MySQL 全局值就认定连接一定能存活到该时间。

### SQLAlchemy `pool_pre_ping`

连接从池中取出时先做存活检查。检测到已断开的连接后，SQLAlchemy 会废弃它并重新建立
连接。这是跨环境的基础保护，应始终保留。

它不能保护已经取出、且在事务或 SQL 执行过程中突然断开的连接。此时当前事务已经失败，
业务层必须收到真实错误；DeepAgent 不做通用 SQL 重试，避免写操作重复执行。

### SQLAlchemy `pool_recycle`

连接创建时间超过指定秒数后，在下一次从池中取出时换新。它按“连接年龄”计算，不是按
“最后一次使用后的空闲时间”计算，也不会中断正在使用的连接。

`pool_recycle` 是主动预防，`pool_pre_ping` 是使用前兜底。两者可以同时使用。

## 当前值与未来配置项

### 当前 v0.3.11

当前代码只对 MySQL 使用：

```text
pool_pre_ping = true
pool_recycle = 90 秒
```

SQLite 和 PostgreSQL 不使用 MySQL 的 90 秒回收策略。当前没有可覆盖该值的环境变量。

### 建议后续增加

建议增加部署级配置：

```text
DEEP_AGENT_DBMS_POOL_RECYCLE_SECONDS
```

建议语义：

- 正整数：MySQL 连接主动回收秒数。
- `0`：关闭主动回收，仍保留 `pool_pre_ping` 和断链识别。
- 非 MySQL：忽略该配置。
- 第一版默认 `90`，保持 Test 的现有稳定行为；各环境确认超时后再显式覆盖。

不建议启动时自动查询 MySQL 并动态修改连接池，原因包括：

- 全局值和当前账号的会话值可能不同。
- 中间网络设备的超时无法通过 MySQL 查询获得。
- 生产账号不一定有读取全局变量的权限。
- 运行中改变连接池内部参数容易依赖 SQLAlchemy 私有实现。

## 不同环境怎么取值

主动回收值必须低于整条链路中最短的空闲超时。建议至少留出 30 秒或 10% 的余量，取两者
中较大者。

| 已确认的最短空闲超时 | 建议起始值 | 说明 |
| --- | ---: | --- |
| 120 秒 | 90 秒 | 当前 Test 配置 |
| 480 秒（8 分钟） | 420 秒左右 | 留出至少 60 秒余量 |
| 8000 秒 | 3600～7200 秒 | 根据连接建立成本和网络设备策略选择 |
| 未知或经常变化 | 90 秒或显式关闭 | 稳定优先用保守值；确认 pre-ping 足够后可设为 0 |
| 小于 30 秒 | 优先修复数据库/代理配置 | 频繁重建连接会放大数据库压力，不建议只靠应用规避 |

90 秒设置在超时很长的环境中不会造成功能错误，但会更频繁地建立数据库连接。连接量较小
时成本通常有限，长期仍应按环境调大，避免无意义的握手和认证开销。

## 数据库配置恢复正常后怎么调整

如果运维提高了 `wait_timeout` 或修复了代理层超时，不要立即删除所有应用保护：

1. 重新读取全局和会话 `wait_timeout`。
2. 向运维确认数据库代理、负载均衡和防火墙的 TCP 空闲超时。
3. 保留 `pool_pre_ping=True` 和精确断链识别。
4. 将主动回收值提高到新超时之前，或者在完成验证后设为 `0`。
5. 使用与线上一致的 Worker 数量，完成至少两个“预热 → 空闲超过旧超时 → 并发只读请求”周期。
6. 确认没有 500、`Exception terminating connection`、Transport closed 或 Worker timeout，再发布配置。

如果新超时是 8 分钟，可先从 420 秒验证；如果是数千秒，可先提高到 1800 或 3600 秒，
不必一步到最大值。

## 标准排查步骤

### 1. 确认应用和依赖版本

```bash
python3.12 - <<'PY'
from importlib.metadata import version

for package in ("SQLAlchemy", "asyncmy", "uvloop"):
    print(f"{package}={version(package)}")
PY
```

### 2. 区分健康接口和数据库接口

`GET /health` 不访问数据库，只能证明进程存活。排查连接池必须同时访问 `/info` 或 Agent、
模型、会话、工具目录等数据库接口。

### 3. 搜索关键日志

```bash
rg -n \
  "TCPTransport closed=True|handler is closed|Exception terminating connection|WORKER TIMEOUT" \
  <deep-agent-log>
```

不要把 Token、Cookie、数据库密码或完整连接串复制进日志或排查文档。

### 4. 做空闲复现

使用只读接口：

```text
启动与线上相同数量的 Worker
→ 并发预热数据库接口
→ 空闲时间超过数据库 wait_timeout
→ 同时请求 Agent、模型、会话、工具和 /info
→ 再重复一轮
```

如果只为复现，可对专用测试连接设置较短的会话级 `wait_timeout`；不要修改共享数据库全局
配置，也不要在实验中执行写接口。

## 验收标准

- 空闲后首批数据库请求不返回 500。
- 所有 Worker 都能获得有效连接，不依赖用户再次刷新恢复。
- 日志不再出现未处理的 `TCPTransport closed=True` / `handler is closed`。
- 没有 `Exception terminating connection` 和 Worker timeout。
- 不对已经开始执行的业务 SQL 自动重试。
- SQLite 和 PostgreSQL 行为不受 MySQL 配置影响。

## 修改边界

- 调整 `pool_recycle` 不需要 Alembic，也不修改业务数据。
- MySQL `wait_timeout`、数据库代理和防火墙策略由运维管理；DeepAgent 不应自行修改全局值。
- 如果未来新增 `DEEP_AGENT_DBMS_POOL_RECYCLE_SECONDS`，需要同步 Options、全局配置只读展示、
  单元测试、部署配置和本文档。

参考：[SQLAlchemy 2.0 Connection Pooling](https://docs.sqlalchemy.org/en/20/core/pooling.html)、
[SQLAlchemy MySQL Dialect](https://docs.sqlalchemy.org/en/20/dialects/mysql.html)。
