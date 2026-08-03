#!/usr/bin/env bash
# WSL host go-judge qualification: prepare mounts, restart sidecar, run probe.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOST_DIR="${GO_JUDGE_HOST_DIR:-/tmp/deep-agent-go-judge-host}"
ENDPOINT="${GO_JUDGE_ENDPOINT:-http://127.0.0.1:5050}"
REPORT="${GO_JUDGE_REPORT:-/tmp/go-judge-wsl.json}"
VENV_SITE="$REPO_ROOT/venv/lib/python3.11/site-packages"

if [[ ! -x "$SCRIPT_DIR/go-judge" ]]; then
  echo "missing seccomp go-judge binary: $SCRIPT_DIR/go-judge" >&2
  exit 1
fi
if [[ ! -d "$VENV_SITE" ]]; then
  echo "missing sandbox venv site-packages: $VENV_SITE" >&2
  echo "hint: python3.11 -m venv venv && venv/bin/pip install -r scripts/sandbox_venv/requirements.txt" >&2
  exit 1
fi

mkdir -p "$HOST_DIR/site-packages"
rsync -a --delete "$VENV_SITE/" "$HOST_DIR/site-packages/"
cp -f "$SCRIPT_DIR/go-judge" "$SCRIPT_DIR/seccomp.yaml" "$HOST_DIR/"
if [[ ! -f "$HOST_DIR/containerPasswd.txt" ]]; then
  printf 'root:x:0:0:root:/root:/sbin/nologin\n' >"$HOST_DIR/containerPasswd.txt"
fi
if [[ ! -f "$HOST_DIR/dotenv" ]]; then
  printf 'PATH=/usr/local/bin:/usr/bin:/bin\n' >"$HOST_DIR/dotenv"
fi

"$REPO_ROOT/venv/bin/python" - <<PY
from pathlib import Path

repo = Path(${REPO_ROOT@Q})
script_dir = Path(${SCRIPT_DIR@Q})
host_dir = Path(${HOST_DIR@Q})
overlay = (script_dir / "mount.wsl-host.overlay.yaml").read_text().replace(
    "@HOST_DIR@", str(host_dir)
)
text = (script_dir / "mount.yaml").read_text()
marker = "proc: true"
if marker not in text:
    raise SystemExit("mount.yaml missing proc: true marker")
text = text.replace(marker, overlay + marker, 1)
(host_dir / "mount.yaml").write_text(text)
PY

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy || true
export no_proxy='*' NO_PROXY='*'

if pgrep -f "$HOST_DIR/go-judge" >/dev/null 2>&1; then
  sudo pkill -9 -f "$HOST_DIR/go-judge" || true
  sleep 1
fi
if ss -ltn | grep -q ':5050 '; then
  sudo fuser -k 5050/tcp 2>/dev/null || true
  sleep 1
fi
sudo systemctl stop gojudge.scope 2>/dev/null || true
sudo systemctl reset-failed gojudge.scope 2>/dev/null || true
sleep 0.5

cd "$HOST_DIR"
nohup sudo ./go-judge \
  -http-addr=127.0.0.1:5050 \
  -mount-conf=./mount.yaml \
  -seccomp-conf=./seccomp.yaml \
  >"$HOST_DIR/go-judge-host.log" 2>&1 &

for _ in $(seq 1 50); do
  if curl -fsS --max-time 2 "$ENDPOINT/version" >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done
if ! curl -fsS --max-time 2 "$ENDPOINT/version" >/dev/null 2>&1; then
  echo "go-judge failed to start; see $HOST_DIR/go-judge-host.log" >&2
  tail -20 "$HOST_DIR/go-judge-host.log" >&2 || true
  exit 1
fi
curl -fsS "$ENDPOINT/version"
echo

cd "$REPO_ROOT"
set +e
"$REPO_ROOT/venv/bin/python" scripts/sandbox_probe/go_judge.py \
  --endpoint "$ENDPOINT" \
  --python python3.11 \
  --env PYTHONPATH=/sandbox/site-packages \
  --binary "$SCRIPT_DIR/go-judge" \
  --report "$REPORT"
probe_rc=$?
set -e

cp -f "$REPORT" "$SCRIPT_DIR/go-judge-wsl-report.json"
echo "report: $REPORT"
exit "$probe_rc"
