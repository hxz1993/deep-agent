# deepfos_platform

`deepfos_platform` 是随 DeepAgent 源码携带的平台运行时支撑包，不是 Agent、
Skill、MCP 或业务能力目录。

DeepAgent 当前直接复用它提供的能力：

- `conf/`：环境变量与 `OPTION` 配置体系。
- `client/`：文件上传等 DeepFOS 平台 API 客户端。
- `discovery/`、`registry/`：服务发现与注册。
- `runtime/`：Gunicorn 和平台运行生命周期。
- `log.py`：平台日志桥接。

这个目录保留在仓库根，是因为部署时 DeepAgent 需要和它一起打包运行。业务
Agent 的运行循环、能力注册、会话和 API 都位于 `deep_agent/`；新增业务能力时
不要放进本目录。

该包目前按平台兼容代码维护。除非任务明确涉及平台配置、服务发现、文件客户端
或部署运行时，否则不要修改。
