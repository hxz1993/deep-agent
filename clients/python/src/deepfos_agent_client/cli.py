from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, is_dataclass
from importlib.metadata import version
from importlib.resources import files
import json
from pathlib import Path
import sys

from .client import AgentClient, AgentServiceError, WaitTimeout

SKILL_NAME = "deepfos-agent-call"


def add_output_options(parser, *, root=False):
    default = None if root else argparse.SUPPRESS
    parser.add_argument("--json", action="store_true", default=default, help="Write structured JSON")
    parser.add_argument("--app", default=default, help="Override DEEPFOS_AGENT_APP")
    parser.add_argument("--url", default=default, help="Override DEEPFOS_AGENT_URL")


def parser():
    cli = argparse.ArgumentParser(prog="deepfos-agent")
    cli.add_argument("--version", action="version", version=version("deepfos-agent-client"))
    add_output_options(cli, root=True)
    groups = cli.add_subparsers(dest="group", required=True)
    commands = {
        group: groups.add_parser(group).add_subparsers(dest="action", required=True)
        for group in ("agents", "tasks", "artifacts", "skill")
    }

    def command(group, name):
        sub = commands[group].add_parser(name)
        add_output_options(sub)
        return sub

    command("agents", "list")
    for name in ("submit", "reply"):
        sub = command("tasks", name)
        sub.add_argument("agent_code" if name == "submit" else "task_id")
        sub.add_argument("--context-id" if name == "submit" else "--reply-to")
        sub.add_argument("--text")
        sub.add_argument("--attach", action="append", default=[])
        sub.add_argument("--data", type=json.loads, help="JSON object")
        sub.add_argument("--message-id", help="Supply the same ID when retrying a request")
    for name in ("get", "wait", "cancel"):
        sub = command("tasks", name)
        sub.add_argument("task_id")
        if name == "get":
            sub.add_argument("--history-length", type=int, default=None)
        if name == "wait":
            sub.add_argument("--timeout", type=float, default=120.0)
    sub = command("tasks", "list")
    sub.add_argument("--agent", required=True)
    sub.add_argument("--state")
    sub.add_argument("--context-id")
    sub.add_argument("--page-size", type=int, default=20)
    sub.add_argument("--cursor")
    sub = command("artifacts", "download")
    sub.add_argument("task_id")
    sub.add_argument("artifact_id")
    sub.add_argument("-o", "--output", required=True, type=Path)
    command("skill", "install").add_argument("--dir", type=Path, required=True)
    return cli


def install_skill(root: Path) -> dict:
    target = root / SKILL_NAME / "SKILL.md"
    bundled = files("deepfos_agent_client").joinpath("skills", SKILL_NAME, "SKILL.md").read_bytes()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as stream:
            stream.write(bundled)
    except FileExistsError:
        if target.read_bytes() != bundled:
            raise ValueError(f"A different Skill already exists at {target}")
    return {"path": str(target), "skill": SKILL_NAME}


async def execute(args):
    if args.group == "skill":
        return install_skill(args.dir)
    async with AgentClient.from_env(app=args.app, url=args.url, client_label="cli") as client:
        if args.group == "agents":
            return await client.list_agents()
        if args.group == "artifacts":
            path = await client.download_artifact(args.task_id, args.artifact_id, destination=args.output)
            return {"path": str(path)}
        if args.action in {"submit", "reply"}:
            if args.data is not None and not isinstance(args.data, dict):
                raise ValueError("--data must be a JSON object")
            message = dict(text=args.text, attachments=args.attach, data=args.data, message_id=args.message_id)
            if args.action == "submit":
                return await client.submit(agent_code=args.agent_code, context_id=args.context_id, **message)
            return await client.reply(args.task_id, reply_to=args.reply_to, **message)
        if args.action == "get":
            return await client.get_task(args.task_id, history_length=args.history_length)
        if args.action == "wait":
            return await client.wait(args.task_id, timeout=args.timeout)
        if args.action == "cancel":
            return await client.cancel(args.task_id)
        return await client.list_tasks(agent_code=args.agent, state=args.state, context_id=args.context_id,
                                       page_size=args.page_size, cursor=args.cursor)


def main() -> int:
    args = parser().parse_args()
    try:
        result = asyncio.run(execute(args))
    except (AgentServiceError, WaitTimeout, OSError, ValueError) as exc:
        error = {"error": str(exc)}
        for field in ("status", "task_id", "message_id"):
            if (value := getattr(exc, field, None)) is not None:
                error[field] = value
        if args.json:
            print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        else:
            print(" · ".join(str(value) for value in error.values()), file=sys.stderr)
        return 4 if isinstance(exc, WaitTimeout) else 3 if isinstance(exc, AgentServiceError) else 2
    failed_task = getattr(result, "state", None) in {"failed", "canceled", "rejected"}
    if is_dataclass(result):
        result = asdict(result)
    print(json.dumps(result, ensure_ascii=False, indent=None if args.json else 2))
    return 5 if failed_task else 0
