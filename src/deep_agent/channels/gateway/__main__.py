from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the optional DeepAgent Channel Gateway")
    parser.add_argument("--config", type=Path, help="External dotenv profile")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    if args.config:
        config_path = args.config.expanduser().resolve()
        if not config_path.is_file():
            raise FileNotFoundError(f"Config file does not exist: {config_path}")
        load_dotenv(config_path, override=True)
        # An explicit Gateway profile is the complete runtime configuration,
        # matching local_dev semantics.  Do not merge the repository .env
        # afterwards and accidentally switch databases.
        os.environ["DEEP_AGENT_LOAD_DOTENV"] = "false"
    if args.host:
        os.environ["DEEP_AGENT_CHANNEL_HOST"] = args.host
    if args.port:
        os.environ["DEEP_AGENT_CHANNEL_PORT"] = str(args.port)

    from deep_agent.channels.gateway.config import GatewayConfig
    from deep_agent.channels.gateway.app import app

    config = GatewayConfig.from_env()
    # A gateway owns one set of channel connections and queues. Do not inherit
    # WEB_CONCURRENCY from the API/container and start competing gateway workers.
    uvicorn.run(app, host=config.host, port=config.port, workers=1, reload=False)


if __name__ == "__main__":
    main()
