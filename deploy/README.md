# DeepAgent 部署目录

`deploy/` 集中保存现有部署配置；本轮仅迁移文件位置，保留原有脚本内容、依赖安装与同一源码目录
打包发布方式，不引入 `pyproject.toml`、wheel 构建或前端构建。

## 文件职责

| 位置 | 职责 | 旧入口 |
| --- | --- | --- |
| `gunicorn.conf.py` | API 的 Gunicorn 配置，继续固定单 worker | `deep_agent/gunicorn.conf.py` |
| `docker-compose.yml` | 本地 PostgreSQL 辅助服务，不是完整应用部署 | 仓库根 `docker-compose.yml` |
| `jenkins/` | 已有 Jenkins 发布和诊断脚本及其说明 | `docs/guides/jenkins/` |

旧入口通过相对符号链接访问同一份文件，后续维护使用 `deploy/` 下的真实位置。
根 `deep_agent/` 本身指向 `src/deep_agent/`；其中 `gunicorn.conf.py` 再指向
`deploy/gunicorn.conf.py`，因此既有 Gunicorn 命令仍可从仓库根执行。

## Jenkins 基线与本轮范围

此次目录整理以已提交的 `Jenkinsfile.v1`、`Jenkinsfile.v2`、`Jenkinsfile.diagnose.v1` 为基线：

| 已提交基线文件名 | 职责 |
| --- | --- |
| `Jenkinsfile.v1` | 原始发布流水线 |
| `Jenkinsfile.v2` | 使用统一启动器运行 API 与渠道网关的发布流水线 |
| `Jenkinsfile.diagnose.v1` | Test 环境只读发布诊断 |

`docs/guides/jenkins/` 整体迁入 `deploy/jenkins/`，旧目录保留链接。工作区已有的 Jenkins
未提交重命名与脚本修改按原样保留，不属于此次迁移的功能变更，也不据此宣称发布了新版本。
上表说明已提交基线，不保证这些旧文件名在当前未提交工作区仍存在；具体脚本内容以原有工作区为准。
直接粘贴脚本的 Jenkins 任务无需修改流水线内容；本轮不修改集群资源或发布模式。
若任务使用 **Pipeline script from SCM + Lightweight checkout**，旧的
`docs/guides/jenkins/Jenkinsfile.*` 不是 Git 树中的实际文件路径，必须将 **Script Path**
改为 `deploy/jenkins/Jenkinsfile.*`。轻量检出按 Git 树路径读取脚本，不沿目录符号链接解析；
完整检出和源码 tar 解压后的文件系统兼容链接不受此限制。
依据：[Jenkins 流水线脚本读取](https://github.com/jenkinsci/workflow-cps-plugin/blob/master/plugin/src/main/java/org/jenkinsci/plugins/workflow/cps/CpsScmFlowDefinition.java#L116-L128)、
[Git SCM 文件读取](https://github.com/jenkinsci/git-plugin/blob/master/src/main/java/jenkins/plugins/git/GitSCMFile.java)。

## 源码打包与兼容要求

安装依赖、执行 Gunicorn、统一启动器、维护命令和测试时，工作目录仍为仓库根：

```bash
python3.12 -m pip install -r requirements.txt
gunicorn -c deep_agent/gunicorn.conf.py deep_agent.main:app
```

需要渠道网关的既有部署继续使用 `python -m deep_agent.combined_runtime`。
渠道网关实现位于 `src/deep_agent/channels/gateway/`；旧的
`python -m deep_agent.channel_gateway` 与逐模块导入由兼容层委托，不复制业务代码。

源码 tar 归档、镜像复制和解压须保留完整相对目录结构，同时包含兼容链接及其真实目标：

- Python 源码在 `src/`，同时保留根 `deep_agent`、`deepfos_platform`、`maintenance` 和
  `deepagent_files.py` 链接。
- 静态资源在 `console/`（含 `vendor/`），同时保留 `frontend/console` 和 `frontend/vendor` 链接。
- 内置 MCP、迁移和部署文件分别在 `mcp_servers/`、`migrations/`、`deploy/`；保留对应的
  `mcps`、`alembic`、根 `docker-compose.yml` 和 `docs/guides/jenkins` 链接。

不能只按旧目录名挑选链接打包而漏掉目标，也不能在归档或解压时把符号链接变成仅含路径的普通文本。
Windows 检出环境必须具备创建符号链接的权限和 Git 配置；不支持真实符号链接的检出不能直接沿用
这些旧命令。发布前应在实际解压目录核对链接、Python 导入和静态资源可读性。

稳定 `data/`、仓库外 Profile、密钥和 `tmp/` 不属于发布源码。目录整理保留现有稳定数据库和本地文件，
不重建、不迁移业务数据；已经标记的旧临时产物集中在 `tmp/TMP_to_delete-prior-artifacts/`。
项目完整边界见 [`项目结构与职责`](../docs/guides/project-structure.md)。
