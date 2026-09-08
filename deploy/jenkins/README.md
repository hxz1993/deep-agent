# Jenkins 发布与诊断

两种在用发布方式都构建所选分支或 Tag 的最新代码，并只更新一个已有 Deployment。
区别只是是否启动渠道网关，不是应用代码版本或数据库版本。当前数据库仍为 Alembic `002`，
这次整理不新增迁移、不降级数据库、不删除渠道配置或历史。

## 三个活动入口

| 用途 | 文件 | 行为 |
| --- | --- | --- |
| 网关启动 | [`Jenkinsfile.gateway`](Jenkinsfile.gateway) | 同一容器运行 API + Gateway |
| 仅 API | [`Jenkinsfile.api-only`](Jenkinsfile.api-only) | 只启动 API，覆盖此前网关启动入口 |
| 只读诊断 | [`Jenkinsfile.diagnose`](Jenkinsfile.diagnose) | 收集 Test 失败 Pod 的状态、事件和日志，不发布 |

[`combined_runtime.py`](../../src/deep_agent/combined_runtime.py) 是镜像内启动器，不在 Jenkins 节点运行。
最初的发布脚本按原字节移至 [`历史归档`](../../docs/archive/jenkins/README.md)，不能发布当前 `src/`
源码树，也不能用于停用网关。活动目录不再保留旧文件名或旧目录兼容链接。

Jenkins 中已粘贴的脚本不会自动变化；发布当前代码前需要替换整份当前脚本。
使用 SCM Script Path 的 Job 需同步改为 `deploy/jenkins/` 下的对应路径。

## 怎么选、怎么切换

- 需要企微等渠道：粘贴 `Jenkinsfile.gateway`。
- 不需要网关（包括已经发过网关版的 Test）：粘贴 `Jenkinsfile.api-only`。
- 两者都选择同一个目标分支或 Tag，正常发布选 `RELOAD_PIPELINE=false`。

仅 API 版会在同一次按容器名的 strategic merge patch 中更新镜像、明确设置
`gunicorn -c deploy/gunicorn.conf.py deep_agent.main:app`，并把
`DEEP_AGENT_CHANNEL_GATEWAY_URL` 置空（移除该项旧的 `valueFrom`）。
两种发布模式同时把目标容器的 `workingDir` 固定为 `/deep-agent`、`PYTHONPATH` 固定为
`/deep-agent/src`，并移除 `PYTHONPATH` 旧的 `valueFrom`。这与镜像内的 `WORKDIR` 和 `ENV` 一致，
覆盖 Deployment 遗留的源码路径。除此之外保留其他环境变量、其他容器、副本数、Service 和 Ingress。
仅 API 模式既停止托管网关，也让 API 不再引用旧网关。
部署完成后只检查 API 健康，不要求网关健康，也不在 Jenkins 节点安装 Python。

再切回网关启动版时会明确恢复 `python -m deep_agent.combined_runtime`，监督进程会为 API 子进程
重新注入正确网关地址。数据库内已启用的机器人会重新连接；停用网关不等于删除或关闭账号配置。
切换需要滚动替换容器，旧 Pod 退出前旧网关可能仍在工作；如有任务执行中，请先停止任务并安排维护窗口。

两种在用脚本都使用当前 `src/` 源码树 / Alembic `002`。API 启动仍按代码执行正常迁移，
这里不是跳过迁移的开关；不要选用不认识现有数据库 Revision 的旧源码 Tag。

## 网关启动版实际做了什么

```text
检出 test → 构建并推送一个镜像 → Trivy 报告
→ 单次 kubectl patch 更新原 deep-agent Deployment 的镜像与启动入口
→ 原容器内先启动 API（30098）
→ API 完成 Alembic 001 → 002 并通过 /health
→ 再启动渠道网关（127.0.0.1:8010）
→ kubectl rollout status
→ 容器内检查 API /health 与网关鉴权 /health
```

API 和网关位于同一容器，因此天然复用原 Deployment 注入的数据库、平台和模型配置；不需要新增
Gateway Deployment、Service、Ingress、数据库 Secret 或 Jenkins 参数。8010 只监听 Pod 内回环地址，
不会对外暴露。

启动器会优先使用已有 `DEEP_AGENT_CHANNEL_INTERNAL_TOKEN`；没有配置时，每次容器启动自动生成一份
内部随机 Token，仅写入容器 `/tmp` 下权限为 `0600` 的临时文件，用于两个子进程及发布健康检查，
不会写入仓库或 Jenkins 日志。API 或网关任一进程退出时，启动器会停止另一个进程并退出，让
Kubernetes 按原策略重启容器。

网关入口固定 `workers=1, reload=False`，不继承容器原有 `WEB_CONCURRENCY`。否则 Uvicorn
在“应用对象 + 多 worker”组合下会拒绝启动；0.52.4 的表现是 import string 警告和退出码 3。
网关启动版在镜像构建内运行入口定向测试，覆盖继承多 worker 环境的情况；测试不启动业务应用或连接数据库，
并使用这次镜像实际安装的 Uvicorn。Jenkins 节点仍不需要安装 Python。

诊断结束后换回需要的发布脚本，选择目标分支或 Tag、`RELOAD_PIPELINE=false` 后重新构建。
从无参数的诊断脚本切回时，首轮缺失参数会回退到 `java21-mvn3` / `test` / 正常发布，
不必先构建一轮只为刷新参数；显式选择 `RELOAD_PIPELINE=true` 仍只刷新参数。

## Test 便捷部署与正式部署的区别

当前网关启动版适合先沿用已有 Job 和 Deployment 完成 Test 验证：一份镜像、一个容器、两个进程。
代价是 API 与 Gateway 共用容器生命周期，任一个退出都会整体重启，不能独立发布或做故障隔离。
本脚本保留已有副本数和更新策略；即使 replicas=1，RollingUpdate 也可能短暂并存新旧 Gateway。
在尚未实现网关选主前，不应让启用同一机器人账户的新旧网关并行运行。Test 初次发布成功后再启用
机器人；后续维护应采用单副本、先停旧再起新的策略（例如 Recreate），并接受短时中断。
更新策略的行为以 [Kubernetes Deployment 文档](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#recreate-deployment) 为准。

正式部署建议复用同一镜像，分别启动 API 与 Gateway 两个 Deployment；仍可在一次流水线中依次发布，
不需要重写渠道业务代码，但需补齐部署配置：

- 独立的启动命令、资源限制、健康探针、日志和重启策略；网关故障不连带关闭普通 API。
- 内部 Service 地址与共享 Secret 中的网关内部 Token，不能再依赖同一容器的回环地址和临时 Token。
- API 先完成数据库迁移并就绪，Gateway 再启动；当前 Gateway 保持单实例且发布时不重叠。
- 当前 API 的取消协调仍依赖进程内状态，不能仅靠增加 Pod/worker 声称支持多副本；需要另行设计。

这轮不改集群部署拓扑或更新策略；正式上线前应单独核验这些条件，不把“Test 冒烟通过”当作高可用验收。

## Jenkins 配置

网关启动版和仅 API 版都只保留三个参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `BRANCH_TAG` | `test` | 构建分支或标签 |
| `AGENT` | `java21-mvn3` | 沿用现有 Jenkins 节点 |
| `RELOAD_PIPELINE` | `false` | `true` 时只刷新参数 |

可将 Job 的 Script Path 设置为 `deploy/jenkins/Jenkinsfile.gateway` 或
`deploy/jenkins/Jenkinsfile.api-only`，也可以把对应文件内容复制到现有 Job。
只读诊断的 Script Path 为 `deploy/jenkins/Jenkinsfile.diagnose`。
两者沿用原始版的 Git、Harbor、Kubernetes 凭据 ID 和 Job 命名规则：`test/deep-agent`
会更新 `test` 命名空间内的 `deep-agent` Deployment。

构建节点只需具备原始版已经使用的 Git、tar/gzip、Docker、kubectl、DeepCube wheel 路径和 Trivy。
不再要求 Jenkins 节点安装 Python；Python 启动检查在镜像构建阶段和目标容器内执行。

## 源码路径与构建检查

本地与镜像使用同一套路径：Python 在 `src/`，Gunicorn 配置为 `deploy/gunicorn.conf.py`，
渠道网关模块为 `deep_agent.channels.gateway`。仓库根不再提供 Python 包或旧网关模块兼容入口。

两份镜像使用 `WORKDIR /deep-agent`、`ENV PYTHONPATH=/deep-agent/src`，继续安装
`requirements.txt` 和独立沙箱环境。构建时检查并编译真实路径，随后用模块导入验证 Python 路径；
网关版继续运行单 worker 入口测试。发布后的容器内 Python 健康探针继承相同 `PYTHONPATH`。
不增加应用 wheel 构建、数据库流程或额外发布阶段。

## 发布前确认

1. 原 Deployment 和容器名仍为 Jenkins `JOB_BASE_NAME`，即当前约定的 `deep-agent`。
2. 两种在用发布脚本都会按容器名用 strategic merge patch 同时更新镜像、`command` 和 `args`，
   并统一 `workingDir` 与 `PYTHONPATH`，因此可覆盖上次发布模式遗留的启动路径；不会替换同一个
   Pod 内的其他容器。
3. 原 Service、Ingress 和健康探针仍指向 API 端口。API 默认端口为 `30098`；若通过
   `DEEP_AGENT_PYTHON_BIND` 或 `DEEP_AGENT_PYTHON_PORT` 修改，启动器会使用同一端口等待 API。
4. 数据库是当前生产基线 `001` 或已经正确升级的 `002`。API 会自动执行 `upgrade head`；历史
   Revision `012` 必须先按维护文档校验并 rebaseline，不能只手工改版本号。
5. Test 与本地不要同时启用同一个企微机器人。当前约定是本地使用 `local测试`，Test 再配置原机器人。

## 成功与失败判断

仅 API 版要求 Deployment rollout 完成、容器内 API `/health` 返回 `ok`；不会检查或启动网关。
网关启动版要求以下三项都通过：Deployment rollout 完成、API `/health` 返回 `ok`、Gateway
鉴权 `/health` 返回 `ok` 且数据库 Revision 为 `002`。发布后再到 Console「渠道」新增或启用 Test
机器人，并执行渠道测试；流水线健康检查不会主动连接或发送机器人消息来制造业务副作用。

如果失败，先看日志中的第一个失败命令：

- 镜像构建失败：检查依赖、wheel、Docker 或镜像仓库。
- rollout 失败：检查 `kubectl describe pod` 和容器日志；常见原因是 API 数据库迁移失败。
- `combined_runtime --check` 失败：检查容器日志中 API 或 Channel Gateway 的启动错误。若提示缺少
  `/tmp/deep-agent-channel-runtime/token`，应结合日志检查统一入口是否执行或是否提前退出，
  以及 Deployment 的 `command` / `args` 是否被其他发布配置再次覆盖，不能只凭缺少文件确定原因。

流水线不会自动回滚数据库。只更新一个原 Deployment，因此不再涉及额外网关资源的创建、缩容或回滚。

## 只能粘贴脚本时，如何诊断失败的发布

无需登录服务器或手动执行 `kubectl`：

1. 先保留当前发布脚本，然后把 **`Jenkinsfile.diagnose` 的完整内容**替换到现有 Test Job 的
   Pipeline script，保存后点一次构建；现有参数保持原样即可，诊断脚本不读取它们。
2. 把这次构建的 **Console Output** 发回排查。脚本固定读取 `test/deep-agent`，沿用现有
   `java21-mvn3` 节点和 Kubernetes 凭据，不需要新增参数、Python、jq 或插件。
3. 诊断结束后，需恢复发布脚本才会重新发布；不要把诊断成功当作服务恢复。

诊断脚本不拉代码、不构建镜像、不更新 Deployment、不重启 Pod、不执行容器命令，也不连接数据库。
它按 Deployment/ReplicaSet 的 controller UID 精确关联新旧 Pod，输出工作目录、启动命令、状态、退出码、重启次数、
事件，以及目标 `deep-agent` 容器的当前/上次退出日志。没有上次日志或单项读取失败会明确提示并继续；
无法读取 Deployment、ReplicaSet 或 Pod 清单时直接报错，不伪装成收集成功。

不输出完整 YAML、环境变量或 Secret；日志会隐藏常见密钥赋值、带密码 URL 和配置初始化行。
脱敏并非万能，转发前仍请检查业务日志中是否含账号信息或对话正文。事件和上次容器日志可能已被集群
清理，脚本只能收集当前仍保留的证据，不会为了生成日志重新启动服务。

维护者可在仓库根运行定向冒烟：

```bash
export PYTHONPATH="$PWD/src"
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.unit.test_jenkins_deploy -v
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.unit.test_jenkins_api_only -v
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.unit.test_jenkins_diagnostics -v
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.unit.test_channel_gateway_entrypoint -v
```

参考契约：[渠道指南](../../docs/guides/wecom-channel.md)、[API Gunicorn](../gunicorn.conf.py)、
[网关入口](../../src/deep_agent/channels/gateway/__main__.py)、
[网关健康/迁移门禁](../../src/deep_agent/channels/gateway/app.py)。
