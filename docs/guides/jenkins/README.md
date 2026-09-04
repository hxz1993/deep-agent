# Jenkins 流水线 V2：API + 渠道网关

适用 DeepAgent `0.7.0` / Alembic `002`，以 2026-09-05 提供的现网流水线为基准。
一次 Jenkins 发布构建一份镜像，最终同时运行 API 和渠道网关两个独立服务。
**不是同时执行两个启动命令，也不是在一个容器里后台启动两个进程。**

## 版本与对照结论

- [`Jenkinsfile.v1`](Jenkinsfile.v1)：用户提供的旧版原文归档，仅用于对照，不修改旧 Jenkins Job。
- [`Jenkinsfile.v2`](Jenkinsfile.v2)：新的双服务发布流水线。
- [`deploy_v2.py`](deploy_v2.py)：V2 配套发布助手，与 V2 一起纳入同一源码提交，不依赖额外 Python 包。

核对时，当前分支的 `docs/guides/` 没有独立 Jenkins 流水线文件，不能认定存在一份与粘贴内容
完全一致的旧文档。已有 [`wecom-channel.md`](../wecom-channel.md) 说明的是 API/Gateway 两进程
部署；粘贴的 V1 只对 API 执行 `kubectl set image`，**没有部署网关，也没有等待发布就绪**。
这里保留 V1 并新增 V2，不覆盖任何已有流水线配置。

| 项目 | V1 | V2 |
| --- | --- | --- |
| Git / 构建节点 / Harbor / K8s 凭据 | 现有配置 | 沿用现有仓库、节点标签与凭据 ID |
| Python 镜像 / DeepCube wheel / sandbox | Python 3.11.9 + 环境 wheel | 沿用；网关 SDK 已在 requirements 中 |
| 打包 | 移动工作区内容，依赖 Job 名与目录名一致 | 仅 `git archive` 选定提交，固定 `/deep-agent`，排除未跟踪本地数据 |
| 镜像版本 | BUILD_TIMESTAMP | 构建号 + Git SHA，API/Gateway 使用同一标签 |
| 发布对象 | 只更新已有 API Deployment | 保留 API，创建/更新 Gateway 与两个内部 Service |
| 发布方式 | 提交更新命令即结束 | 预检 → 停旧网关 → API Ready → 网关 Ready → 双向内部健康检查 |
| 配置 / 日志 | `sh 'env'` 输出完整环境 | 共用 Kubernetes Secret，不打印完整环境或密钥 |
| `PROFILE` | 声明但没有使用 | 删除该无效参数，不改变现有平台服务发现配置 |
| Trivy | 发布后生成报告 | 发布前生成报告；命令失败阻断，沿用原漏洞报告策略 |

## 首次使用前准备

本次仅交付文件，**没有连接 Jenkins/Kubernetes，没有构建推送镜像、部署或执行数据库迁移**。
要投入使用，先完成以下一次性配置；之后一次 Build 即更新两个服务。

1. 将 V2 与 `deploy_v2.py` 发布到要构建的 Git 分支/标签。Jenkins 可使用 Pipeline script from SCM，
   Script Path 为 `docs/guides/jenkins/Jenkinsfile.v2`；也可复制 V2 到 Job，但所选 `BRANCH_TAG`
   仍必须包含配套助手和渠道源码。V2 默认选择已经包含配套文件的 `test`。
   参数只保留 V1 已使用的 `BRANCH_TAG`、`AGENT`、`RELOAD_PIPELINE` 三项，不再要求填写环境参数或勾选发布确认框。
2. 保留已有 API Deployment / Service / Ingress。确认 Deployment 和主容器名称；V2 默认仍取
   `JOB_BASE_NAME`。命名空间固定取 `JOB_NAME` 第一段。不要同时保留两个 Job
   发布同一环境；`disableConcurrentBuilds` 只保护同一个 Job。
3. API 必须是一个副本、一个 Gunicorn worker；Gateway 固定一个副本，两者均不能由 HPA 自动扩容。
   两个 Deployment 使用 `Recreate`，有维护窗口和短暂停机，不承诺零停机。发布会等待旧 Gateway
   Pod 完全退出，避免新旧网关重复连接、消费消息；不要使用强制删 Pod 来绕过该等待。
4. 确认原 API **容器监听端口**。V2 按当前 Test 约定固定为源码 Gunicorn 默认的 `30098`，
   不是文档本地示例的 `8000`。助手在旧 API 容器内只读解析配置，比较端口与数据库地址/库名/用户/密码；
   不一致即中止。V2 显式设置 `DEEP_AGENT_PYTHON_BIND`，保留原外部 Service/Ingress。
   若原环境通过自定义 Gunicorn 参数、TLS、代理 sidecar 等改变实际监听方式，先适配这份模板，
   不要直接套用；配置比较不等于验证所有自定义启动方式。
5. 运维在**同一命名空间**预建 `<Job基础名>-channel-runtime` Secret；当前 `test/deep-agent` Job
   默认就是 `test` 命名空间的 `deep-agent-channel-runtime`。内容必须对应原 API 的同一数据库：

   | Secret key | 要求 |
   | --- | --- |
   | `DEEP_AGENT_DBMS_NAME` | `mysql` 或 `postgres` |
   | `DEEP_AGENT_DBMS_HOSTNAME` | 原 API 使用的主机名 |
   | `DEEP_AGENT_DBMS_PORT` | 原 API 使用的端口 |
   | `DEEP_AGENT_DBMS_DATABASE` | 原 API 使用的库名 |
   | `DEEP_AGENT_DBMS_USER` | 原 API 使用的数据库用户 |
   | `DEEP_AGENT_DBMS_PASSWORD` | 对应数据库密码，通过安全方式配置 |
   | `DEEP_AGENT_CHANNEL_INTERNAL_TOKEN` | 两服务共用的高强度随机密钥 |
   | `DEEP_AGENT_CHANNEL_PLATFORM_AUTHORIZATION` / `DEEP_AGENT_CHANNEL_PLATFORM_COOKIE` | 可选；业务需要访问平台服务时配置有效认证信息 |

   不要将真实值写到仓库、Jenkins 参数或日志。V2 只引用 Secret；不会自动创建/轮换密钥。
   API 和 Gateway 显式引用同一组 DBMS key，避免 API 的旧 `env` 遮蔽 `envFrom`。
   不支持两个 Pod 各自使用 SQLite，也不会自动替换数据库/创建空库。若采用 Secret 轮换，
   API/Gateway 必须一起重发；环境变量不会随 Secret 更新自动刷新。
6. 在受控运维流程中完成源版本备份、源码 migration graph、现有数据库 Revision/物理表结构、
   已部署镜像四方核对后再点击构建。流水线不再增加一层容易漏选的 `CONFIRM_RELEASE` 参数；
   点击普通发布构建本身就是发布意图，脚本仍会在任何集群修改前强制执行 Secret、数据库、端口、
   单副本、HPA、资源归属和 Kubernetes server dry-run 检查。API 启动执行迁移，网关只检查 `002`。
   早期渠道开发版可能同为 `002` 而物理字段不同，**版本号相同不代表可以直接发布**；
   V2 不修复结构、不 stamp 版本、不自动执行维护脚本、不自动回滚数据库。
7. 构建节点需有 Python 3.9+、Git、GNU tar/gzip、Docker、kubectl、原有 wheel 路径和 Trivy。
   Jenkins 沿用 Git Parameter / Docker Pipeline / Kubernetes CLI / Credentials Binding 插件。
   K8s 凭据需允许读取 Deployment/Secret/HPA/Pod/Service/ServiceAccount、服务端 dry-run、创建/更新 Service
   与 Gateway Deployment、patch/scale Deployment、watch Pod 和 `pods/exec`。
   `ShangHaiOffineK8SApiServer` 在 V2 按 Secret text 凭据读取，需与原 Job 凭据类型一致。
8. 网关仅创建 ClusterIP 8010，不建立公网入口；需要访问 API、共享 DB 和企微公网长连接。
   V2 复用 API 的 `imagePullSecrets`（模板为空时从其 ServiceAccount 读取）和调度约束，
   不复制 API 的 PVC、initContainer、业务 sidecar、ServiceAccount 权限或私有 CA 挂载。使用 NetworkPolicy、工作负载身份、私有 CA、
   mesh 注入或专用调度的环境，先为新网关确认/补齐相应配置。网关默认资源为 100m/256Mi request、
   1 CPU/1Gi limit，可在版本化助手里按环境调整。

## 发布流程与成功标准

```text
检出选定源码 → 构建并推送一个镜像 → Trivy 报告
→ 只读预检 + Kubernetes server dry-run
→ 停止旧 Gateway 并等 Pod 删除（首次发布跳过）
→ 创建/更新两个内部 Service
→ API Recreate 更新、等待 Ready 和 /health
→ Gateway Recreate 更新、等待鉴权 /health
→ API → Gateway、Gateway → API 两个方向的 Service 健康检查
→ 确认两个 Deployment 均 1/1 Ready、镜像标签一致
```

新建的内部 Service：`<API名>-channel-api:<API_PORT>` 与 `<API名>-channel-gateway:8010`。
网关 Pod 使用独立标签，避免被原 API selector 选中。同名新资源如果已存在且不属于 V2，
助手拒绝覆盖，需运维先确认归属。

Gateway 的 `/health` 需要 Bearer Token；探针在容器内读取环境变量，并禁用 HTTP 代理，
不会把 Token 明文写进 Kubernetes `httpHeaders`。健康通过仅表示宿主就绪和内部可达，
不代表所有机器人已认证成功。发布后在 Console「渠道」确认账号状态；流水线不会自动发送消息
或调用 `/accounts/{id}/test` 来占用真实机器人连接。

## 失败处理与验证边界

- 预检失败：尚未停服务，修正名称、凭据、共享 Secret、端口、单副本或准入问题后重试。
- API 更新失败：旧网关保持停止，不在未知 schema 状态下自动拉回旧网关。由运维检查 Pod 事件、
  受控日志和数据库状态，再选择修复或经过 schema 评估的回退。V2 的预检依赖在旧 API 容器内
  执行只读配置检查；若 API 已 CrashLoop，需先由运维恢复到可执行预检的运行状态，不能指望
  直接重跑流水线自动自救，也不要为绕过检查而自动恢复旧镜像。
- Gateway 更新或双向检查失败：API 可能已更新成功；流水线报失败不代表所有资源恢复原状。
  先检查网络、Secret、镜像拉取、`002` 物理结构和 Pod 状态；不要只回滚镜像就宣称数据库也回退。
- `RELOAD_PIPELINE=true` 仅更新参数，不检出构建、不连接集群。
- 本地验证仅做 V2 助手的离线定向冒烟、Shell 语法和文件一致性检查；没有跑业务全量回归。
  离线检查不能替代实际 Jenkins 插件校验、镜像构建或目标集群发布验证。

维护者可在仓库根运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.unit.test_jenkins_deploy_v2 -v
```

参考契约：[渠道指南](../wecom-channel.md)、[API Gunicorn](../../../deep_agent/gunicorn.conf.py)、
[网关入口](../../../deep_agent/channel_gateway/__main__.py)、[网关健康/迁移门禁](../../../deep_agent/channel_gateway/app.py)。
Jenkins 凭据与环境写法依据 [Jenkins Pipeline 文档](https://www.jenkins.io/doc/book/pipeline/jenkinsfile/)
和 [Kubernetes CLI 插件](https://www.jenkins.io/doc/pipeline/steps/kubernetes-cli/)；更新和等待方式依据
[Kubernetes Deployment](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/)
和 [rollout status](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_rollout/kubectl_rollout_status/)。
