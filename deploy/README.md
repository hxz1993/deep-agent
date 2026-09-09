# DeepAgent 部署目录

本地与镜像统一使用 `src/` 源码树、仓库根工作目录及显式 `PYTHONPATH`。依赖继续通过
`requirements.txt` 安装，不增加 Python wheel 或 Console 构建流程。

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
继续先安装内部 DeepCube wheel，再安装应用依赖和沙箱环境。

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
