# Jenkins 流水线 V2：单 Deployment 启动 API + 渠道网关

适用 DeepAgent `0.7.0` / Alembic `002`。V2 以现网 V1 为基础，仍然只构建一份镜像、更新
一个已有 Deployment；区别仅是 V2 镜像的启动命令会在同一容器内托管 API 和渠道网关两个进程。

## 版本与适用环境

- [`Jenkinsfile.v1`](Jenkinsfile.v1)：旧流水线归档，镜像只启动 Gunicorn API，不启动渠道网关。
- [`Jenkinsfile.v2`](Jenkinsfile.v2)：Test 双进程流水线，镜像启动
  `python -m deep_agent.combined_runtime`。
- [`combined_runtime.py`](../../../deep_agent/combined_runtime.py)：镜像内启动器，不在 Jenkins 节点运行。

Alpha 可以继续使用 V1 发布同一套新代码：API 和其他功能会升级，应用启动也会执行数据库迁移，
但因为镜像入口仍是 Gunicorn，所以不会启动 8010 渠道网关。数据库一旦升级到 `002`，不要再发布
不认识 `002` 的旧源码镜像。

## V2 实际做了什么

```text
检出 test → 构建并推送一个镜像 → Trivy 报告
→ kubectl set image 更新原 deep-agent Deployment
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

## Jenkins 配置

V2 只保留三个参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `BRANCH_TAG` | `test` | 构建分支或标签 |
| `AGENT` | `java21-mvn3` | 沿用现有 Jenkins 节点 |
| `RELOAD_PIPELINE` | `false` | `true` 时只刷新参数 |

可将 Job 的 Script Path 设置为 `docs/guides/jenkins/Jenkinsfile.v2`，也可以把文件内容复制到现有
Test Job。V2 沿用 V1 的 Git、Harbor、Kubernetes 凭据 ID 和 Job 命名规则：`test/deep-agent`
会更新 `test` 命名空间内的 `deep-agent` Deployment。

构建节点只需具备 V1 已经使用的 Git、tar/gzip、Docker、kubectl、DeepCube wheel 路径和 Trivy。
不再要求 Jenkins 节点安装 Python；Python 启动检查在镜像构建阶段和目标容器内执行。

## 发布前确认

1. 原 Deployment 和容器名仍为 Jenkins `JOB_BASE_NAME`，即当前约定的 `deep-agent`。
2. 原 Deployment 没有显式覆盖镜像 `command` / `args`；否则 Dockerfile 的 V2 `CMD` 不会生效。
3. 原 Service、Ingress 和健康探针仍指向 API 端口。API 默认端口为 `30098`；若通过
   `DEEP_AGENT_PYTHON_BIND` 或 `DEEP_AGENT_PYTHON_PORT` 修改，启动器会使用同一端口等待 API。
4. 数据库是当前生产基线 `001` 或已经正确升级的 `002`。API 会自动执行 `upgrade head`；历史
   Revision `012` 必须先按维护文档校验并 rebaseline，不能只手工改版本号。
5. Test 与本地不要同时启用同一个企微机器人。当前约定是本地使用 `local测试`，Test 再配置原机器人。

## 成功与失败判断

流水线只有在以下三项都通过后才成功：Deployment rollout 完成、API `/health` 返回 `ok`、Gateway
鉴权 `/health` 返回 `ok` 且数据库 Revision 为 `002`。发布后再到 Console「渠道」新增或启用 Test
机器人，并执行渠道测试；流水线健康检查不会主动连接或发送机器人消息来制造业务副作用。

如果失败，先看日志中的第一个失败命令：

- 镜像构建失败：检查依赖、wheel、Docker 或镜像仓库。
- rollout 失败：检查 `kubectl describe pod` 和容器日志；常见原因是 API 数据库迁移失败。
- `combined_runtime --check` 失败：检查容器日志中 API 或 Channel Gateway 的启动错误。

流水线不会自动回滚数据库。只更新一个原 Deployment，因此不再涉及额外网关资源的创建、缩容或回滚。

维护者可在仓库根运行定向冒烟：

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.unit.test_jenkins_deploy_v2 -v
```

参考契约：[渠道指南](../wecom-channel.md)、[API Gunicorn](../../../deep_agent/gunicorn.conf.py)、
[网关入口](../../../deep_agent/channel_gateway/__main__.py)、
[网关健康/迁移门禁](../../../deep_agent/channel_gateway/app.py)。
