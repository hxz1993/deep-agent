"""Keep five clearly labeled, synthetic PTC acceptance sessions in local SQLite.

New-format diagnostics come from real Monty executions with in-process tools.
No external MCP, model provider, or production data is used.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import sqlite3
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from pydantic_ai import Agent, FunctionToolset, ModelRetry
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart, INTERRUPTED_TOOL_RETURN_CONTENT
from pydantic_ai.models.function import FunctionModel

from deep_agent.agent.deps import AgentDeps
from deep_agent.db.dialect import DbDialect
from deep_agent.db.factory import create_engine
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.files.storage import LocalFileStore
from deep_agent.ptc import FilePayloadResolvingToolset, HybridCodeMode
from deep_agent.ptc.file_payload import build_code_mode_tool_package
from deep_agent.schemas.attachment import AttachmentRef


def session_id(name: str, user_id: str) -> str:
    return uuid5(NAMESPACE_URL, f"deepagent-ptc-diagnostics-v1:{user_id}:{name}").hex


def agent_for(snippets: list[str], *, wait_event: asyncio.Event | None = None):
    step = 0

    def respond(_messages, _info):
        nonlocal step
        if step < len(snippets):
            result = ModelResponse(parts=[ToolCallPart("run_code", {"code": snippets[step]}, f"ptc-accept-{step + 1}")])
            step += 1
            return result
        return ModelResponse(parts=[TextPart("合成工具验收已完成。")])

    async def prepare() -> dict:
        return {"seed": 7, "mode": "synthetic"}

    async def query(value: int) -> dict:
        await asyncio.sleep(0.005 if value % 2 else 0.01)
        return {"value": value, "score": value * 3}

    async def process(value: int) -> int:
        return value * value

    async def large_result() -> dict:
        return {"title": "合成大结果", "body": "数据" * 50_000, "rows": list(range(200))}

    async def small_json() -> dict:
        return {"ok": True, "count": 3, "labels": ["甲", "乙", "丙"]}

    async def upload_image(file_name: str, base64_content: str) -> dict:
        return {"media_id": "synthetic-media-001", "file_name": file_name,
                "received_bytes": len(base64.b64decode(base64_content))}

    async def finish(count: int) -> dict:
        return {"processed": count, "status": "done"}

    async def fail() -> str:
        raise ModelRetry("合成工具失败：用于检查已完成步骤")

    async def wait_forever() -> str:
        if wait_event is not None:
            wait_event.set()
        await asyncio.Event().wait()
        return "unreachable"

    wrapped = FilePayloadResolvingToolset(FunctionToolset(tools=[
        prepare, query, process, large_result, small_json, upload_image,
        finish, fail, wait_forever,
    ]).prefixed("demo"))
    return Agent(
        FunctionModel(respond), deps_type=AgentDeps,
        toolsets=[build_code_mode_tool_package().toolset, wrapped],
        capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True)],
    )


async def persist(stores, sid, title, agent_id, user_id, messages, *, run_id):
    await stores.coordinator.append(
        sid, messages, agent_id=agent_id, user_id=user_id,
        turn_id=run_id, run_id=run_id, terminal_status="completed",
    )


async def create(args):
    path = Path(args.db).resolve()
    with sqlite3.connect(path) as connection:
        revision = connection.execute("select version_num from alembic_version").fetchone()
    if revision is None or revision[0] != "007":
        raise RuntimeError("验收数据只允许写入 Alembic 007 的本地 SQLite")
    engine, factory = create_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
    stores = create_data_stores(DbDialect.SQLITE, factory)
    try:
        cases = [
            "PTC 验收｜完整流程（合成工具）",
            "PTC 验收｜部分失败（合成工具）",
            "PTC 验收｜取消与长列表（合成工具）",
            "PTC 验收｜历史兼容（合成夹具）",
            "PTC 验收｜图片与 JSON（合成工具）",
        ]
        for title in cases:
            sid = session_id(title, args.user_id)
            if await stores.chat_session.resolve_ref(sid, agent_id=args.agent_id, user_id=args.user_id):
                if "部分失败" in title:
                    # Older local acceptance data remains intact. Add one new
                    # real run to demonstrate the richer per-step error JSON.
                    error_run = uuid5(NAMESPACE_URL, sid + ":error-detail-v2").hex
                    with sqlite3.connect(path) as connection:
                        present = connection.execute(
                            "select 1 from chat_session_events where session_id=? and run_id=? limit 1",
                            (sid, error_run),
                        ).fetchone()
                    if not present:
                        code = "ready = await demo_prepare()\nawait demo_query(value=3)\nawait demo_fail()"
                        result = await agent_for([code]).run(
                            "再次触发合成失败，验证错误 JSON", deps=AgentDeps(session_id=sid, agent_id=args.agent_id, user=args.user_id),
                        )
                        await persist(stores, sid, title, args.agent_id, args.user_id, result.new_messages(), run_id=error_run)
                        print(f"APPENDED\t{title}\t{sid}")
                print(f"EXISTING\t{title}\t{sid}")
                continue
            if "完整流程" in title:
                code = (
                    "import asyncio\n"
                    "seed = await demo_prepare()\n"
                    "left, right = await asyncio.gather(demo_query(value=1), demo_query(value=2))\n"
                    "processed = []\n"
                    "for i in range(5):\n    processed.append(await demo_process(value=i))\n"
                    "large = await demo_large_result()\n"
                    "final = await demo_finish(count=len(processed))\n"
                    "{'seed': seed['seed'], 'queries': [left['value'], right['value']], 'processed': final['processed']}"
                )
                result = await agent_for([code]).run(title, deps=AgentDeps(session_id=sid, agent_id=args.agent_id, user=args.user_id))
                await persist(stores, sid, title, args.agent_id, args.user_id, result.new_messages(), run_id=uuid5(NAMESPACE_URL, sid + ":run").hex)
            elif "部分失败" in title:
                code = "ready = await demo_prepare()\nawait demo_query(value=3)\nawait demo_fail()"
                result = await agent_for([code]).run(title, deps=AgentDeps(session_id=sid, agent_id=args.agent_id, user=args.user_id))
                await persist(stores, sid, title, args.agent_id, args.user_id, result.new_messages(), run_id=uuid5(NAMESPACE_URL, sid + ":run").hex)
            elif "取消与长列表" in title:
                code = "values = []\nfor i in range(26):\n    values.append(await demo_process(value=i))\nlen(values)"
                result = await agent_for([code]).run(title, deps=AgentDeps(session_id=sid, agent_id=args.agent_id, user=args.user_id))
                await persist(stores, sid, title, args.agent_id, args.user_id, result.new_messages(), run_id=uuid5(NAMESPACE_URL, sid + ":list-run").hex)
                entered = asyncio.Event()
                deps = AgentDeps(session_id=sid, agent_id=args.agent_id, user=args.user_id)
                task = asyncio.create_task(agent_for(["await demo_wait_forever()"], wait_event=entered).run(
                    "取消一个等待中的合成工具", deps=deps,
                ))
                await asyncio.wait_for(entered.wait(), timeout=5)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                diagnostic = deps.ptc_diagnostics.get("ptc-accept-1")
                if diagnostic is None or diagnostic["summary"]["status"] != "interrupted":
                    raise RuntimeError("真实取消未产生诊断；拒绝写入合成取消记录")
                messages = [
                    ModelRequest(parts=[UserPromptPart("取消一个等待中的合成工具")]),
                    ModelResponse(parts=[ToolCallPart("run_code", {"code": "await demo_wait_forever()"}, "ptc-accept-1")], state="interrupted"),
                    ModelRequest(parts=[ToolReturnPart("run_code", INTERRUPTED_TOOL_RETURN_CONTENT, "ptc-accept-1", outcome="interrupted", metadata={"code_mode": True, "ptc_diagnostics": diagnostic})]),
                ]
                await stores.coordinator.append(
                    sid, messages, agent_id=args.agent_id, user_id=args.user_id,
                    turn_id=uuid5(NAMESPACE_URL, sid + ":cancel-turn").hex,
                    run_id=uuid5(NAMESPACE_URL, sid + ":cancel-run").hex,
                    terminal_status="interrupted",
                )
            elif "图片与 JSON" in title:
                # Retain an actual local image attachment. The fake receiving tool
                # proves the opaque file token is resolved inside PTC only.
                file_store = LocalFileStore(args.file_root)
                await file_store.initialize()
                image = await file_store.upload(
                    file_type="DL", file_name="ptc-acceptance-image.png", mime="image/png",
                    data=Path("console/assets/provider-logos/qwen.png").read_bytes(),
                    request_headers={"user": args.user_id},
                )
                code = (
                    "small = await demo_small_json()\n"
                    "large = await demo_large_result()\n"
                    f"image = await load_file_data(file_id='{image.id}', encoding='base64')\n"
                    "receipt = await demo_upload_image(file_name=image['name'], base64_content=image['content'])\n"
                    "{'small': small['count'], 'large_rows': len(large['rows']), 'image_bytes': receipt['received_bytes']}"
                )
                deps = AgentDeps(
                    session_id=sid, agent_id=args.agent_id, user=args.user_id,
                    file_store=file_store, request_headers={"user": args.user_id},
                    attachment_by_id={image.id: AttachmentRef(id=image.id, name=image.file_name, mime=image.mime)},
                )
                result = await agent_for([code]).run(title, deps=deps)
                returns = [part for message in result.new_messages() for part in message.parts
                           if isinstance(part, ToolReturnPart) and part.tool_name == "run_code"]
                if len(returns) != 1 or len(returns[0].metadata["ptc_diagnostics"]["steps"]) != 4:
                    raise RuntimeError("图片与 JSON 验收没有完成真实 PTC 调用")
                await persist(stores, sid, title, args.agent_id, args.user_id, result.new_messages(), run_id=uuid5(NAMESPACE_URL, sid + ":run").hex)
            else:
                messages = [
                    ModelRequest(parts=[UserPromptPart(title)]),
                    ModelResponse(parts=[
                        ToolCallPart("run_code", {"code": "await legacy()"}, "legacy-call"),
                        ToolCallPart("run_code", {"code": "1 + 1"}, "no-detail-call"),
                    ]),
                    ModelRequest(parts=[
                        ToolReturnPart("run_code", {"answer": "旧格式"}, "legacy-call", metadata={"code_mode": True, "nested_calls": [{"call_id": "legacy-call__1", "tool_name": "legacy", "status": "completed", "duration_ms": 12.3, "args": {"page": 1}, "result": {"ok": True}}]}),
                        ToolReturnPart("run_code", 2, "no-detail-call", metadata={"code_mode": True}),
                    ]),
                    ModelResponse(parts=[TextPart("历史兼容夹具：一条旧审计，一条无诊断。")]),
                ]
                await persist(stores, sid, title, args.agent_id, args.user_id, messages, run_id=uuid5(NAMESPACE_URL, sid + ":run").hex)
            print(f"CREATED\t{title}\t{sid}")
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/deep-agent-local.db")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--file-root", default="data/deep-agent-local-files")
    args = parser.parse_args()
    asyncio.run(create(args))


if __name__ == "__main__":
    main()
