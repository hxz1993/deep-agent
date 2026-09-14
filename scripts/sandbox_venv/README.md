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
| Profile `pydata` | `numpy>=1.26,<2`、`pandas`、`pydantic>=2,<3`（清单见 `src/deep_agent/sandbox/profiles/pydata.json`） |
| Worker harness | 同上 `pydantic`（worker 经 `PYTHONPATH` 加载 `deep_agent.sandbox.*` 时需要） |

**不要**把宿主业务依赖（DeepCube、`deepfos`、MCP 包等）装进此 venv。

## 配置

```text
DEEP_AGENT_SANDBOX_ENABLED=true
DEEP_AGENT_SANDBOX_BACKEND=subprocess-dev
DEEP_AGENT_SANDBOX_PYTHON=/abs/path/to/.venv-sandbox/bin/python
DEEP_AGENT_SANDBOX_ENSURE_VENV=true
```

未设置 `SANDBOX_PYTHON` 时，默认解析为仓库根 `.venv-sandbox/bin/python`（Windows：`.venv-sandbox/Scripts/python.exe`）。

### test / gunicorn 部署（临时 venv）

固定镜像、Pod 每次启动在可写目录重建 venv 时：

```text
DEEP_AGENT_SANDBOX_ENSURE_VENV=true
DEEP_AGENT_SANDBOX_PYTHON=/tmp/.venv-sandbox/bin/python
```

默认使用清华源（`pypi.tuna.tsinghua.edu.cn`）；可覆盖：

```text
DEEP_AGENT_SANDBOX_PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
DEEP_AGENT_SANDBOX_PIP_TRUSTED_HOST=mirrors.aliyun.com
```

设 `SANDBOX_PIP_INDEX_URL=` 为空可改回官方 PyPI。`TRUSTED_HOST` 留空时从 index URL 自动解析。

gunicorn 主进程在 `on_starting`（fork worker 前）执行一次 `setup.sh`，避免多 worker 并发创建。
`local_dev` 仍默认自动 setup（`--no-sandbox-env` 可关），不依赖该开关。

启用 `subprocess-dev` 时：解释器缺失或 Profile 探测失败会 **拒绝启动**。

## 部署（Jenkins / 镜像）

test / dev 应用镜像若保持 `SANDBOX_ENABLED=true` + `subprocess-dev`，可二选一：

1. **构建时**执行本脚本（或等价 `python -m venv` + `pip install -r requirements.txt`），并设置
   `DEEP_AGENT_SANDBOX_PYTHON` 指向镜像内解释器。
2. **启动时**（固定镜像、临时 venv 目录）设置 `DEEP_AGENT_SANDBOX_ENSURE_VENV=true` 与
   `DEEP_AGENT_SANDBOX_PYTHON`（如 `/tmp/.venv-sandbox/bin/python`）；gunicorn 主进程在 fork worker
   前自动执行 setup，无需为 pydata 依赖重建镜像。

两个工具的运行语义和边界见
[`docs/design/07-python-sandbox/README.md`](../../docs/design/07-python-sandbox/README.md)。

## 中文绘图

Python 中文字符串、变量和文件名原本就可使用。Matplotlib 的中文图表还需要 CJK 字体；
仓库现随 `src/deep_agent/sandbox/fonts/` 附带 Noto Sans CJK SC 2.004 完整字体及 OFL 许可证。
沙箱在脚本首次导入 Matplotlib 时自动注册并设为默认 sans-serif，使用 Agg 后端；
`execute_python` 与 Skill 脚本共用，普通 Python 调用不加载绘图库。

部署应完整包含这个目录；发布源码/镜像并重启 API 后生效，不需为字体重装 venv、运行 apt
或让工具联网下载。用户显式修改字体或调用会重置字体的样式时，应保留 `Noto Sans CJK SC`。
Pillow 等库可以 `from deep_agent.sandbox.plotting import FONT_PATH` 后显式使用该文件；
此默认设置只针对 Matplotlib，不声称所有文档/图像库都自动使用中文字体。

回归：`PYTHONPATH=src python3.12 -m unittest tests.unit.test_sandbox_plotting -v`，
使用独立沙箱子进程生成 PNG，检查中文/繁体及负号字形、无缺字警告、中文文件名与 Skill 模式。
