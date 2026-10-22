"""Real NEW export -> frozen 552b7b2 OLD HTTP install, isolated on both sides.

Both servers use the same installed dependencies but their own product source,
ORM metadata, SQLite and file roots. No current parser simulates the old server.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

import httpx

from deep_agent.agent_packages import archive
from tests.unit.test_agent_output_configuration import OUTPUT_CONFIG
from tests.unit.test_agent_packages import package_files

OLD_SHA = "552b7b25291e85a23b22244d5178823f1c60570e"
REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "tests/integration/helpers/agent_package_http_server.py"
HEADERS = {"user": "compatibility-operator", "authorization": "Bearer synthetic-test"}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def state(path):
    """Independent persisted readback, including all resource and event rows."""
    with sqlite3.connect(path) as db:
        tables = sorted(row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        data = {name: sorted([list(row) for row in db.execute(f'SELECT * FROM "{name}"')], key=repr) for name in tables}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


class AgentPackageCrossVersionHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="TMP_to_delete_agent_compat_http_")
        self.root = Path(self.temp.name)
        self.processes = []
        self.clients = []
        self.evidence = []
        try:
            old = self.root / "old-552b7b2"
            old.mkdir()
            raw = subprocess.run(["git", "archive", OLD_SHA, "src", "tests/helpers", "tests/__init__.py"],
                cwd=REPO, check=True, capture_output=True).stdout
            with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
                tar.extractall(old, filter="data")
            self.old, self.old_ready, self.old_db = await self.start(old, "old-data")
            self.new, self.new_ready, self.new_db = await self.start(REPO, "new-data")
        except BaseException:
            await self.cleanup()
            raise

    async def start(self, source, name):
        data = self.root / name
        port = free_port()
        process = subprocess.Popen([sys.executable, "-P", str(SERVER), "--root", str(data), "--port", str(port)],
            cwd=source, env={**os.environ, "PYTHONPATH": f'{source / "src"}{os.pathsep}{source}'},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.processes.append(process)
        client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=HEADERS, timeout=30.0, trust_env=False)
        self.clients.append(client)
        for _ in range(120):
            if process.poll() is not None:
                self.fail("isolated HTTP server exited: " + process.communicate()[0].decode(errors="replace")[-4000:])
            try:
                response = await client.get("/_test/ready")
                if response.status_code == 200 and response.json().get("model_id"):
                    return client, response.json(), data / "config.sqlite"
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.1)
        self.fail("isolated HTTP server did not become ready within 12 seconds")

    async def cleanup(self):
        for client in self.clients:
            await client.aclose()
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            try:
                output, _ = await asyncio.to_thread(process.communicate, timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = await asyncio.to_thread(process.communicate, timeout=5)
            if b"Traceback" in output:
                print("Isolated compatibility server error: " + output.decode(errors="replace")[-6000:])
        self.temp.cleanup()

    async def asyncTearDown(self):
        try:
            if self.evidence:
                print("Cross-version evidence " + json.dumps({"old_sha": OLD_SHA, "scenarios": self.evidence}, ensure_ascii=False))
        finally:
            await self.cleanup()

    async def create(self, client, name, **kwargs):
        ready = self.new_ready if client is self.new else self.old_ready
        response = await client.post("/api/v1/agents", json={"name": name, "prompt": "synthetic compatibility fixture",
                                                               "model_id": ready["model_id"], **kwargs})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def export(self, client, agent_id):
        response = await client.get(f"/api/v1/agents/{agent_id}/export")
        self.assertEqual(response.status_code, 200, response.text[:200])
        return response.content

    async def install(self, client, raw, name, *, target=None, code=None):
        data = {"mode": "overwrite", "target_agent_id": target} if target else {}
        files = {"file": ("agent.zip", raw, "application/zip")} if code is None else None
        if code:
            data["builtin_code"] = code
        preview = await client.post("/api/v1/agent-packages/preview", data=data, files=files)
        self.assertEqual(preview.status_code, 200, preview.text)
        options = {"digest": preview.json()["digest"], "name": name,
                   **({"mode": "overwrite", "target_agent_id": target} if target else {})}
        result = await client.post("/api/v1/agent-packages/install", files=files,
            data={"options": json.dumps(options), **({"builtin_code": code} if code else {})})
        self.assertEqual(result.status_code, 201, result.text)
        agent = result.json()["agent"]
        readback = await client.get(f'/api/v1/agents/{agent["id"]}')
        self.assertEqual(readback.status_code, 200, readback.text)
        self.assertEqual(readback.json()["name"], name)
        return readback.json(), preview.json()

    def record(self, scenario, raw, **extra):
        self.evidence.append({"id": scenario, "zip_sha256": hashlib.sha256(raw).hexdigest(), **extra})

    async def test_formal_http_cross_version_install_matrix(self):
        # Seed actual portable Skill/UI resources, then generate the tested ZIP
        # through NEW's formal export endpoint; the seed archive is not the
        # compatibility export under test.
        seed_raw = archive.build_zip(package_files(resources=True))
        off, _ = await self.install(self.new, seed_raw, "new off with shared resources")
        draft = await self.create(self.new, "new draft", output_config=OUTPUT_CONFIG)
        on = await self.create(self.new, "new on", structured_output_enabled=True, output_config=OUTPUT_CONFIG)
        off_raw = await self.export(self.new, off["id"])
        draft_raw = await self.export(self.new, draft["id"])
        on_raw = await self.export(self.new, on["id"])
        for raw, expected in ((off_raw, {"manifest.json", "agent.json", "ui-tools.json", "skills/example-skill.zip"}),
                              (draft_raw, {"manifest.json", "agent.json", "ui-tools.json"})):
            with zipfile.ZipFile(io.BytesIO(raw)) as zip_file:
                self.assertEqual(set(zip_file.namelist()), expected)
                body = json.loads(zip_file.read("agent.json"))
                self.assertNotIn("output_config", body)
                self.assertNotIn("structured_output_enabled", body)
        # PK-X03/04: actual NEW disabled export, full OLD create/overwrite/chat.
        installed, _ = await self.install(self.old, off_raw, "old imported off")
        chat = await self.old.post("/api/v1/chat", json={"agent_id": installed["id"], "message": "synthetic hello"})
        self.assertEqual(chat.status_code, 200, chat.text)
        self.assertEqual(chat.json()["output"], "compatibility plain reply")
        overwritten_off, overwritten_preview = await self.install(self.old, off_raw, "old overwritten off", target=installed["id"])
        self.assertEqual(overwritten_off["skills"], ["example-skill"])
        self.assertEqual(overwritten_off["capabilities"], ["ui:package-fixture"])
        self.assertTrue(all(resource["status"] == "reuse" for resource in overwritten_preview["resources"]))
        self.record("PK-X03/04", off_raw, ordinary_chat="completed", shared_resources="reused")
        # PK-X05: disabled administrative drafts are absent in both OLD actions.
        draft_installed, _ = await self.install(self.old, draft_raw, "old imported draft")
        await self.install(self.old, draft_raw, "old overwritten draft", target=draft_installed["id"])
        self.record("PK-X05", draft_raw, create=True, overwrite=True)
        # PK-X06/07: both OLD endpoints reject before any resource or target write.
        for scenario, target in (("PK-X06", None), ("PK-X07", installed["id"])):
            before = state(self.old_db)
            data = {"mode": "overwrite", "target_agent_id": target} if target else {}
            rejected = await self.old.post("/api/v1/agent-packages/preview", data=data,
                files={"file": ("agent.zip", on_raw, "application/zip")})
            self.assertEqual(rejected.status_code, 422, rejected.text)
            options = {"digest": "0" * 64, "name": "must not write", **data}
            rejected_install = await self.old.post("/api/v1/agent-packages/install",
                data={"options": json.dumps(options)}, files={"file": ("agent.zip", on_raw, "application/zip")})
            self.assertEqual(rejected_install.status_code, 422, rejected_install.text)
            self.assertEqual(state(self.old_db), before)
            self.record(scenario, on_raw, preview_status=422, install_status=422, zero_writes=True)
        # PK-X08/10: NEW create/overwrite/second export and depth-preserving import.
        on_copy, _ = await self.install(self.new, on_raw, "new on import")
        await self.install(self.new, on_raw, "new on overwrite", target=on_copy["id"])
        config = await self.new.get(f'/api/v1/agents/{on_copy["id"]}/output-config')
        self.assertEqual(config.json()["output_config"], OUTPUT_CONFIG)
        self.assertEqual(on_copy["updated_by"], "compatibility-operator")
        chat = await self.new.post("/api/v1/chat", json={"agent_id": on_copy["id"], "message": "synthetic structured output"})
        self.assertEqual(chat.status_code, 200, chat.text)
        self.assertEqual(chat.json()["output"], {"text": "structured compatibility proof"})
        self.assertTrue(chat.json().get("output_info"))
        second_raw = await self.export(self.new, on_copy["id"])
        second, _ = await self.install(self.new, second_raw, "new second roundtrip")
        self.assertTrue(second["structured_output_enabled"])
        second_config = await self.new.get(f'/api/v1/agents/{second["id"]}/output-config')
        self.assertEqual(second_config.json()["output_config"], OUTPUT_CONFIG)
        with sqlite3.connect(self.new_db) as db:
            persisted = db.execute("SELECT a.structured_output_enabled,a.updated_by,c.name,c.description,c.json_schema "
                "FROM agents a JOIN agent_output_configs c ON c.agent_id=a.id WHERE a.id=?", (second["id"],)).fetchone()
        self.assertEqual(persisted[:4], (1, "compatibility-operator", OUTPUT_CONFIG["name"], OUTPUT_CONFIG["description"]))
        self.assertEqual(json.loads(persisted[4]), OUTPUT_CONFIG["json_schema"])
        self.record("PK-X08/10", second_raw, config_roundtrip=True, native_json_run="completed", independent_sql_readback=True)
        # PK-X09: disabling via portable overwrite clears the definition.
        session_id = chat.json()["session_id"]
        history_before = await self.new.get(f"/api/v1/sessions/{session_id}/history")
        self.assertEqual(history_before.status_code, 200, history_before.text)
        await self.install(self.new, off_raw, "new now disabled", target=on_copy["id"])
        cleared = await self.new.get(f'/api/v1/agents/{on_copy["id"]}/output-config')
        self.assertEqual(cleared.json(), {"structured_output_enabled": False, "allow_text_output": False, "output_config": None})
        history_after = await self.new.get(f"/api/v1/sessions/{session_id}/history")
        self.assertEqual(history_after.json(), history_before.json())
        self.record("PK-X09", off_raw, definition_removed=True, existing_output_history="unchanged")
        # PK-X11: source software version is informative; format stays integer 1.
        files = archive.read_zip(off_raw, [0, 0])
        manifest = archive.json_read(files["manifest.json"])
        manifest["exported_from"] = "99.42.7"
        files["manifest.json"] = archive.canonical(manifest)
        future_raw = archive.build_zip(files)
        await self.install(self.old, future_raw, "old future software")
        await self.install(self.new, future_raw, "new future software")
        self.record("PK-X11", future_raw, old=True, new=True)
        # PK-X12: safe extensions ignored, but known config and required survive.
        files = archive.read_zip(on_raw, [0, 0])
        body = archive.json_read(files["agent.json"])
        body["future_extension"] = {"value": 1}
        body["output_config"]["future_annotation"] = "ignored"
        files["agent.json"] = archive.canonical(body)
        files["extensions/future.txt"] = b"ignored extension, never executed"
        ext_raw = archive.build_zip(files)
        ext, _ = await self.install(self.new, ext_raw, "new extensible import")
        await self.install(self.new, ext_raw, "new extensible overwrite", target=ext["id"])
        self.assertEqual((await self.new.get(f'/api/v1/agents/{ext["id"]}/output-config')).json()["output_config"], OUTPUT_CONFIG)
        clean_files = archive.read_zip(await self.export(self.new, ext["id"]), [0, 0])
        self.assertNotIn("extensions/future.txt", clean_files)
        clean_agent = archive.json_read(clean_files["agent.json"])
        self.assertNotIn("future_extension", clean_agent)
        self.assertNotIn("future_annotation", clean_agent["output_config"])
        self.record("PK-X12", ext_raw, create=True, overwrite=True, extensions_not_registered=True)
        # PK-X01/02: the two immutable, real historical builtin ZIPs, unmodified.
        for code in ("deepcube-assistant", "deeptable-assistant"):
            historical_raw = (REPO / "src/deep_agent/agent_packages/builtin" / f"{code}.zip").read_bytes()
            created, _ = await self.install(self.new, historical_raw, f"historical {code}")
            self.assertFalse(created["structured_output_enabled"])
            self.assertIsNone((await self.new.get(f'/api/v1/agents/{created["id"]}/output-config')).json()["output_config"])
            target = await self.create(self.new, f"historical overwrite target {code}", structured_output_enabled=True, output_config=OUTPUT_CONFIG)
            target_chat = await self.new.post("/api/v1/chat", json={"agent_id": target["id"], "message": "synthetic historical compatibility"})
            self.assertEqual(target_chat.status_code, 200, target_chat.text)
            target_history_url = f'/api/v1/sessions/{target_chat.json()["session_id"]}/history'
            target_history = await self.new.get(target_history_url)
            self.assertEqual(target_history.status_code, 200, target_history.text)
            overwritten, preview = await self.install(self.new, historical_raw, f"historical overwritten {code}", target=target["id"])
            self.assertTrue(preview["structured_output"]["target_enabled"])
            self.assertFalse(overwritten["structured_output_enabled"])
            self.assertIsNone((await self.new.get(f'/api/v1/agents/{target["id"]}/output-config')).json()["output_config"])
            self.assertEqual((await self.new.get(target_history_url)).json(), target_history.json())
            self.record("PK-X01/02", historical_raw, builtin=code, create=True, overwrite=True, existing_history="unchanged")
