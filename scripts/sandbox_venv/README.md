# Sandbox venv（`subprocess-dev` / 阶段 A′）

独立解释器，**不**与 `deep_agent` / DeepCube 宿主 `venv` 共用 site-packages。

## 创建

仓库根执行：

```bash
bash scripts/sandbox_venv/setup.sh
# 或指定解释器：
bash scripts/sandbox_venv/setup.sh /usr/bin/python3.12
```

默认目录：仓库根 `.venv-sandbox/`（已 gitignore）。

## 预装内容

见 [`requirements.txt`](requirements.txt)：

| 类别 | 包 |
| --- | --- |
| Profile `pydata` | `numpy>=1.26,<2`、`pandas`、`pydantic>=2,<3`（清单见 `deep_agent/sandbox/profiles/pydata.json`） |
| Worker harness | 同上 `pydantic`（worker 经 `PYTHONPATH` 加载 `deep_agent.sandbox.*` 时需要） |

**不要**把宿主业务依赖（DeepCube、`deepfos`、MCP 包等）装进此 venv。

## 配置

```text
DEEP_AGENT_SANDBOX_ENABLED=true
DEEP_AGENT_SANDBOX_BACKEND=subprocess-dev
DEEP_AGENT_SANDBOX_PYTHON=/abs/path/to/.venv-sandbox/bin/python
```

未设置 `SANDBOX_PYTHON` 时，默认解析为仓库根 `.venv-sandbox/bin/python`（Windows：`.venv-sandbox/Scripts/python.exe`）。

启用 `subprocess-dev` 时：解释器缺失或 Profile 探测失败会 **拒绝启动**。

## 部署（Jenkins / 镜像）

test / dev 应用镜像若保持 `SANDBOX_ENABLED=true` + `subprocess-dev`，构建阶段须执行本脚本
（或等价 `python -m venv` + `pip install -r requirements.txt`），并设置
`DEEP_AGENT_SANDBOX_PYTHON` 指向镜像内解释器。详见
[`docs/design/07-python-sandbox/ops-kubernetes-sidecar.md`](../../docs/design/07-python-sandbox/ops-kubernetes-sidecar.md) §5.3。

这与 go-judge sidecar 流水线无关：即使不部署 sidecar，**应用镜像**仍需要独立 sandbox venv。
