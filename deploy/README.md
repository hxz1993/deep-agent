# DeepAgent 部署目录

本地与镜像统一使用 `src/` 源码树、仓库根工作目录及显式 `PYTHONPATH`。依赖继续通过
`requirements.txt` 安装，不增加 Python wheel 或 Console 构建流程。

## 固定依赖镜像（2026-10-20）

当前运维已将依赖预装到固定基础镜像，常规代码发布约2–3分钟。依赖调整须另行交接运维更新镜像；规则、文件入口和交接模板见[依赖镜像与运维交接](dependency-images.md)。仓库内现有Jenkins示例仍含逐次装包步骤，不代表已同步同事最新流水线；以实际Job和镜像为准，本次仅记录规则，不修改构建配置。

## 当前入口

| 文件 | 用途 |
| --- | --- |
| `gunicorn.conf.py` | API Gunicorn 配置，固定单 worker |
| `docker-compose.yml` | 本地 PostgreSQL 辅助服务，不是完整应用部署 |
| `jenkins/Jenkinsfile.gateway` | 同一容器运行 API 与渠道网关 |
| `jenkins/Jenkinsfile.api-only` | 仅运行 API，并覆盖此前网关启动命令 |
| `jenkins/Jenkinsfile.diagnose` | Test 只读发布诊断 |

部署脚本统一维护在本目录。旧根目录包、旧网关模块和旧 Jenkins 路径已移除。
最初的发布脚本按原字节保存在 [`历史归档`](../docs/archive/jenkins/README.md)，不能发布当前源码树。

## 本地与镜像的运行方式

MySQL 部署要求应用连接及业务文本字段使用 `utf8mb4`。旧库或旧字段不能只依赖服务器默认值；
字符损失可能触发会话保存失败，不能视为仅显示异常。上线前按
[`MySQL 字符集部署与排查`](../docs/guides/mysql-character-set.md) 检查并验证 Emoji 完整存取。
全局配置 → 运维 → 数据库连接提供只读字符集检查，包含应用连接及已有业务文本字段；
页面检查不自动转换字符集，不替代部署后的真实会话验收。

从仓库根执行，`PYTHONPATH` 指向该工作目录下的 `src`，不继承旧的源码路径：

```bash
export PYTHONPATH="$PWD/src"
python3.12 -m pip install -r requirements.txt
gunicorn -c deploy/gunicorn.conf.py deep_agent.main:app
```

生产网关模式使用同一环境下的 `python -m deep_agent.combined_runtime`，先等待 API 健康再启动
`deep_agent.channels.gateway`。本地 SQLite 联调使用统一监督入口并保留本地身份与文件能力：

```bash
export PYTHONPATH="$PWD/src"
python3.12 -m deep_agent.combined_runtime \
  --local-config ~/.config/deep-agent/sqlite.env \
  --api-port 8000
```

两份发布镜像均声明 `WORKDIR /deep-agent` 和 `ENV PYTHONPATH=/deep-agent/src`。
两种发布模式的 Kubernetes patch 同时设置目标容器的 `workingDir`、`command`、`args` 和
`PYTHONPATH`，清除该变量旧的 `valueFrom`，避免 Deployment 遗留配置覆盖镜像默认值。
仅 API 模式仍将 `DEEP_AGENT_CHANNEL_GATEWAY_URL` 置空；网关模式由监督进程向 API 子进程注入地址。
容器内健康检查复用同一源码路径和工作目录，不依赖兼容链接。

## 源码归档和现有 Jenkins 任务

源码归档须包含完整的 `src/`、`console/`、`mcp_servers/`、`migrations/`、`deploy/`、
`scripts/`、`tests/` 及根工程配置。当前两份发布脚本使用所选提交的 `git archive` 构建镜像，
继续先安装内部 DeepCube wheel，再安装应用依赖和沙箱环境。`src/deep_agent/agent_packages/builtin/` 中纳入版本控制的
完整 ZIP 会一并发布，不需预先解压；API 直接读取 ZIP 并复用上传安装的校验流程，不在启动时安装。
包目录与替换方法见[自带 Agent 安装包](../src/deep_agent/agent_packages/builtin/README.md)。

现有 Jenkins Job 需要使用当前完整脚本：粘贴模式替换脚本正文；SCM 模式设置
`deploy/jenkins/Jenkinsfile.gateway`、`deploy/jenkins/Jenkinsfile.api-only` 或
`deploy/jenkins/Jenkinsfile.diagnose` 为 Script Path。活动目录中只有这三个入口。
详情见 [`Jenkins 发布与诊断`](jenkins/README.md)。

本地 PostgreSQL 仍使用原项目名称和数据卷。若原来从仓库根执行 Compose，改用以下命令以保持
Compose 项目目录不变，不要直接切入 `deploy/` 创建另一个项目：

```bash
docker compose --project-directory "$PWD" -f deploy/docker-compose.yml up -d
```

`data/`、仓库外 Profile、密钥、`tmp/` 和输出文件不属于发布源码。现有稳定数据库和本地文件
保留原地；源码路径整理不迁移、不重建业务数据。完整目录说明见
[`项目结构与职责`](../docs/guides/project-structure.md)。

## v0.9.7 / 数据库 003 发布要求

完整备份 → 停止旧 API 与 Gateway 的写入 → 执行 `alembic upgrade head` 并核验003、10类逻辑引用及业务数据 → 启动新版 → 冒烟测试。
003移除10条外键，新版代码负责原有引用与级联语义，不能滚动混跑旧写入实例。
MySQL DDL中断不等于回滚：保持维护状态，修复故障后重跑迁移，禁止手动修改Revision。
如需回退，先停写并确认逻辑引用有效，再执行 `alembic downgrade 002` 恢复约束后启动旧版。
迁移不会修改字符集；详见[逻辑引用与验收](../docs/guides/application-references.md)。
