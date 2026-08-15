from deepfos_platform.runtime.gunicorn_configs import load_gunicorn_configs

_confs = load_gunicorn_configs(
    server_name='deep-agent-server1-0',
    env_prefix="DEEP_AGENT_",
)

_original_on_starting = _confs.get("on_starting")


def on_starting(server):
    from deep_agent.sandbox.venv import ensure_sandbox_interpreter_for_startup

    ensure_sandbox_interpreter_for_startup()
    if _original_on_starting is not None:
        _original_on_starting(server)


_confs["on_starting"] = on_starting
locals().update(_confs)
