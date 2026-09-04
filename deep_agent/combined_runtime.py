"""Run the API and optional channel gateway in one deployment container.

This entry point is intentionally selected by the Jenkins V2 image only.  The
normal image entry point remains Gunicorn, so environments that keep the V1
pipeline continue to run the API without a channel gateway.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from types import FrameType
from typing import Mapping, Sequence


API_COMMAND = (
    "gunicorn",
    "-c",
    "deep_agent/gunicorn.conf.py",
    "deep_agent.main:app",
)
GATEWAY_COMMAND = (sys.executable, "-m", "deep_agent.channel_gateway")
TOKEN_ENV = "DEEP_AGENT_CHANNEL_INTERNAL_TOKEN"
TOKEN_FILE_ENV = "DEEP_AGENT_COMBINED_TOKEN_FILE"
DEFAULT_TOKEN_FILE = "/tmp/deep-agent-channel-runtime/token"


def _port(value: str, *, name: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not 0 < port < 65536:
        raise ValueError(f"{name} must be between 1 and 65535")
    return port


def api_port_from_env(environ: Mapping[str, str]) -> int:
    bind = environ.get("DEEP_AGENT_PYTHON_BIND", "").strip()
    if bind:
        if ":" not in bind:
            raise ValueError("DEEP_AGENT_PYTHON_BIND must include a port")
        return _port(bind.rsplit(":", 1)[-1], name="API port")
    return _port(environ.get("DEEP_AGENT_PYTHON_PORT", "30098"), name="API port")


def gateway_port_from_env(environ: Mapping[str, str]) -> int:
    return _port(environ.get("DEEP_AGENT_CHANNEL_PORT", "8010"), name="Gateway port")


def build_child_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    child_env = dict(os.environ if environ is None else environ)
    token = child_env.get(TOKEN_ENV, "").strip() or secrets.token_urlsafe(48)
    api_port = api_port_from_env(child_env)
    gateway_port = gateway_port_from_env(child_env)
    child_env.update(
        {
            TOKEN_ENV: token,
            "DEEP_AGENT_CHANNEL_API_URL": f"http://127.0.0.1:{api_port}",
            "DEEP_AGENT_CHANNEL_GATEWAY_URL": f"http://127.0.0.1:{gateway_port}",
            "DEEP_AGENT_CHANNEL_HOST": "127.0.0.1",
            "DEEP_AGENT_CHANNEL_PORT": str(gateway_port),
        }
    )
    return child_env


def _token_path(environ: Mapping[str, str]) -> Path:
    return Path(environ.get(TOKEN_FILE_ENV, DEFAULT_TOKEN_FILE)).expanduser()


def write_runtime_token(environ: Mapping[str, str]) -> Path:
    token_path = _token_path(environ)
    token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, environ[TOKEN_ENV].encode("utf-8"))
    finally:
        os.close(descriptor)
    os.chmod(token_path, 0o600)
    return token_path


def read_runtime_token(environ: Mapping[str, str]) -> str:
    configured = environ.get(TOKEN_ENV, "").strip()
    if configured:
        return configured
    token = _token_path(environ).read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("Channel runtime token is empty")
    return token


def fetch_health(url: str, *, token: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=4) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise RuntimeError(f"Unhealthy response from {url}")
    return payload


def wait_for_health(
    label: str,
    url: str,
    process: subprocess.Popen,
    *,
    timeout: float,
    token: str | None = None,
    shutdown_requested=lambda: False,
) -> dict:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(f"{label} exited during startup with code {return_code}")
        if shutdown_requested():
            raise RuntimeError(f"{label} startup interrupted")
        try:
            return fetch_health(url, token=token)
        except Exception as exc:  # readiness failures are retried until the deadline
            last_error = exc
            time.sleep(0.5)
    detail = f": {last_error}" if last_error else ""
    raise RuntimeError(f"{label} did not become healthy within {timeout:g}s{detail}")


def _signal_process_group(process: subprocess.Popen, sig: int) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, sig)
    except (AttributeError, ProcessLookupError):
        if sig == signal.SIGKILL:
            process.kill()
        else:
            process.terminate()


def stop_processes(processes: Sequence[subprocess.Popen], *, timeout: float = 30.0) -> None:
    for process in processes:
        _signal_process_group(process, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while any(process.poll() is None for process in processes) and time.monotonic() < deadline:
        time.sleep(0.1)
    for process in processes:
        _signal_process_group(process, signal.SIGKILL)
    for process in processes:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def check_runtime(environ: Mapping[str, str] | None = None) -> None:
    runtime_env = dict(os.environ if environ is None else environ)
    api_port = api_port_from_env(runtime_env)
    gateway_port = gateway_port_from_env(runtime_env)
    token = read_runtime_token(runtime_env)
    fetch_health(f"http://127.0.0.1:{api_port}/health")
    payload = fetch_health(f"http://127.0.0.1:{gateway_port}/health", token=token)
    if payload.get("database_revision") != "002":
        raise RuntimeError("Channel Gateway database revision is not 002")


def wait_for_runtime_health(*, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            check_runtime()
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    detail = f": {last_error}" if last_error else ""
    raise RuntimeError(f"Combined runtime did not become healthy within {timeout:g}s{detail}")


def run_combined(*, startup_timeout: float) -> int:
    child_env = build_child_env()
    token_path = write_runtime_token(child_env)
    processes: list[subprocess.Popen] = []
    stopping = False

    def request_shutdown(_signum: int, _frame: FrameType | None) -> None:
        nonlocal stopping
        stopping = True

    previous_handlers = {
        sig: signal.signal(sig, request_shutdown) for sig in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        api_port = api_port_from_env(child_env)
        gateway_port = gateway_port_from_env(child_env)
        api = subprocess.Popen(API_COMMAND, env=child_env, start_new_session=True)
        processes.append(api)
        wait_for_health(
            "DeepAgent API",
            f"http://127.0.0.1:{api_port}/health",
            api,
            timeout=startup_timeout,
            shutdown_requested=lambda: stopping,
        )

        gateway = subprocess.Popen(GATEWAY_COMMAND, env=child_env, start_new_session=True)
        processes.append(gateway)
        wait_for_health(
            "Channel Gateway",
            f"http://127.0.0.1:{gateway_port}/health",
            gateway,
            timeout=startup_timeout,
            token=child_env[TOKEN_ENV],
            shutdown_requested=lambda: stopping,
        )
        print(
            f"DeepAgent API and Channel Gateway are ready on ports {api_port} and {gateway_port}",
            flush=True,
        )

        while not stopping:
            for label, process in (("DeepAgent API", api), ("Channel Gateway", gateway)):
                return_code = process.poll()
                if return_code is not None:
                    print(f"{label} exited with code {return_code}", file=sys.stderr, flush=True)
                    return return_code or 1
            time.sleep(0.5)
        return 0
    finally:
        stop_processes(processes)
        token_path.unlink(missing_ok=True)
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run DeepAgent API and Channel Gateway together")
    parser.add_argument("--check", action="store_true", help="check both local health endpoints")
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=float(os.environ.get("DEEP_AGENT_COMBINED_STARTUP_TIMEOUT", "180")),
    )
    args = parser.parse_args(argv)
    if args.startup_timeout <= 0:
        parser.error("--startup-timeout must be positive")
    if args.check:
        wait_for_runtime_health(timeout=args.startup_timeout)
        return 0
    return run_combined(startup_timeout=args.startup_timeout)


if __name__ == "__main__":
    raise SystemExit(main())
