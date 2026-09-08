"""Preserve ``python -m deep_agent.channel_gateway`` for existing deployments."""
from deep_agent.channels.gateway.__main__ import main

if __name__ == "__main__":
    main()
