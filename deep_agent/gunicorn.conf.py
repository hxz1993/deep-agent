from deepfos_platform.runtime.gunicorn_configs import load_gunicorn_configs

_confs = load_gunicorn_configs(
    server_name='deep-agent-server1-0',
    env_prefix="DEEP_AGENT_",
)
locals().update(_confs)
