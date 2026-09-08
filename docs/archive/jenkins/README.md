# DeepAgent Jenkins 历史归档

`Jenkinsfile.original` 按原字节保存最初的发布脚本，仅用于历史对照。它依赖旧的根目录 Python 包和
Gunicorn 路径，不能用于发布当前 `src/` 源码树，也不能用于从网关模式切回仅 API 模式。

当前活动入口只有 [`deploy/jenkins/`](../../../deploy/jenkins/README.md) 中的
`Jenkinsfile.gateway`、`Jenkinsfile.api-only`、`Jenkinsfile.diagnose`。
旧脚本的改动历史可通过 Git 追溯；不要在归档脚本中补写当前发布功能。
