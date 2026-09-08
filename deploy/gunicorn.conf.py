from deepfos_platform.runtime.gunicorn_configs import load_gunicorn_configs

_confs = load_gunicorn_configs(
    server_name='deep-agent-server1-0',
    env_prefix="DEEP_AGENT_",
)

# Active Agent runs and their official Pydantic AI CancellationToken live in the
# application process. Multiple Gunicorn workers behind one Pod would let the
# cancel request reach a different registry and incorrectly report not_running.
# Keep one async worker per Pod; scale with Pods only when routing is sticky or a
# shared cancellation coordinator replaces the process-local registry.
_confs["workers"] = 1

_original_on_starting = _confs.get("on_starting")


def on_starting(server):
    from deep_agent.sandbox.venv import ensure_sandbox_interpreter_for_startup

    ensure_sandbox_interpreter_for_startup()
    if _original_on_starting is not None:
        _original_on_starting(server)


_confs["on_starting"] = on_starting
locals().update(_confs)
