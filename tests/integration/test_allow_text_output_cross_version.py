"""Formal final-version exports against the actual pre-0.14 HTTP installer.

Each side runs its own source tree, SQLite and FileStore. The frozen server is
552b7b2; no current parser or hand-edited ZIP substitutes for that old runtime.
"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import unittest
import zipfile

from deep_agent.agent_packages import archive
from tests.integration import test_agent_package_cross_version_http as original
from tests.unit.test_agent_packages import package_files


OUTPUT = {"name": "portable_result", "description": "合成完整定义，保留内部必须 🧪",
          "json_schema": {"type": "object", "properties": {
              "text": {"type": "string"}, "record": {"type": "object", "properties": {
                  "id": {"type": "integer"}}, "required": ["id"]}},
              "additionalProperties": False}}


class AllowTextOutputCrossVersionTests(unittest.IsolatedAsyncioTestCase):
    # Reuse only transport/lifecycle helpers, not the old test class and tests.
    asyncSetUp = original.AgentPackageCrossVersionHttpTests.asyncSetUp
    asyncTearDown = original.AgentPackageCrossVersionHttpTests.asyncTearDown
    start = original.AgentPackageCrossVersionHttpTests.start
    cleanup = original.AgentPackageCrossVersionHttpTests.cleanup
    create = original.AgentPackageCrossVersionHttpTests.create
    export = original.AgentPackageCrossVersionHttpTests.export
    install = original.AgentPackageCrossVersionHttpTests.install
    record = original.AgentPackageCrossVersionHttpTests.record

    def assert_same_export_contents(self, actual, expected):
        # ZIP headers legitimately use each export's wall-clock timestamp.
        # Compare the untouched formal artifacts' complete member contents,
        # not their timestamp-bearing archive bytes or a rebuilt substitute.
        with zipfile.ZipFile(io.BytesIO(actual)) as current, zipfile.ZipFile(io.BytesIO(expected)) as previous:
            self.assertEqual(current.namelist(), previous.namelist())
            current_hashes, previous_hashes = {}, {}
            for current_info, previous_info in zip(current.infolist(), previous.infolist(), strict=True):
                name = current_info.filename
                current_bytes, previous_bytes = current.read(current_info), previous.read(previous_info)
                self.assertEqual(current_bytes, previous_bytes, f"disabled draft changed exported member {name}")
                self.assertEqual((current_info.file_size, current_info.CRC, current_info.compress_type),
                                 (previous_info.file_size, previous_info.CRC, previous_info.compress_type))
                current_hashes[name] = hashlib.sha256(current_bytes).hexdigest()
                previous_hashes[name] = hashlib.sha256(previous_bytes).hexdigest()
            self.assertEqual(json.loads(current.read("manifest.json")), json.loads(previous.read("manifest.json")))
            self.assertEqual(current_hashes, previous_hashes, "formal manifest and resource SHA256 must match")

    async def check_final_config(self, agent_id, *, enabled, allow_text, definition):
        response = await self.new.get(f"/api/v1/agents/{agent_id}/output-config")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"structured_output_enabled": enabled,
            "allow_text_output": allow_text, "output_config": definition})
        ordinary = await self.new.get(f"/api/v1/agents/{agent_id}")
        self.assertEqual(ordinary.status_code, 200, ordinary.text)
        self.assertIs(ordinary.json()["allow_text_output"], allow_text)
        self.assertEqual(ordinary.json()["updated_by"], "compatibility-operator")
        with sqlite3.connect(self.new_db) as db:
            self.assertNotIn("allow_text_output", {column[1] for column in db.execute("PRAGMA table_info(agents)")})
            self.assertEqual(db.execute("SELECT structured_output_enabled,updated_by FROM agents WHERE id=?", (agent_id,)).fetchone(),
                             (int(enabled), "compatibility-operator"))
            row = db.execute("SELECT name,description,json_schema,allow_text_output FROM agent_output_configs WHERE agent_id=?", (agent_id,)).fetchone()
        if definition is None:
            self.assertIsNone(row)
        else:
            self.assertEqual(row[:2], (definition["name"], definition["description"]))
            self.assertEqual(json.loads(row[2]), definition["json_schema"])
            self.assertEqual(row[3], int(allow_text))

    async def test_final_formal_export_old_and_new_complete_install_matrix(self):
        rejected = await self.new.post("/api/v1/agents", json={"name": "must not create policy without definition",
            "prompt": "synthetic", "allow_text_output": True})
        self.assertEqual(rejected.status_code, 422, rejected.text)
        plain = await self.create(self.new, "no output config defaults false")
        await self.check_final_config(plain["id"], enabled=False, allow_text=False, definition=None)
        rejected = await self.new.patch(f'/api/v1/agents/{plain["id"]}', json={"allow_text_output": True})
        self.assertEqual(rejected.status_code, 422, rejected.text)
        await self.check_final_config(plain["id"], enabled=False, allow_text=False, definition=None)
        # Resource seed is separate from the tested formal export artifact.
        seeded, _ = await self.install(self.new, archive.build_zip(package_files(resources=True)), "portable resources")
        off_export = None
        for allow_text in (False, True):
            patched = await self.new.patch(f'/api/v1/agents/{seeded["id"]}', json={
                "structured_output_enabled": False, "allow_text_output": allow_text, "output_config": OUTPUT})
            self.assertEqual(patched.status_code, 200, patched.text)
            await self.check_final_config(seeded["id"], enabled=False, allow_text=allow_text, definition=OUTPUT)
            if allow_text:
                rejected = await self.new.patch(f'/api/v1/agents/{seeded["id"]}', json={"output_config": None,
                    "allow_text_output": True})
                self.assertEqual(rejected.status_code, 422, rejected.text)
                await self.check_final_config(seeded["id"], enabled=False, allow_text=True, definition=OUTPUT)
                removed = await self.new.patch(f'/api/v1/agents/{seeded["id"]}', json={"output_config": None})
                self.assertEqual(removed.status_code, 200, removed.text)
                await self.check_final_config(seeded["id"], enabled=False, allow_text=False, definition=None)
                restored = await self.new.patch(f'/api/v1/agents/{seeded["id"]}', json={"allow_text_output": True,
                    "output_config": OUTPUT})
                self.assertEqual(restored.status_code, 200, restored.text)
                await self.check_final_config(seeded["id"], enabled=False, allow_text=True, definition=OUTPUT)
            raw = await self.export(self.new, seeded["id"])
            if off_export is None:
                off_export = raw
            else:
                self.assert_same_export_contents(raw, off_export)
            with zipfile.ZipFile(io.BytesIO(raw)) as package:
                self.assertEqual(set(package.namelist()), {"manifest.json", "agent.json", "ui-tools.json", "skills/example-skill.zip"})
                manifest, body = json.loads(package.read("manifest.json")), json.loads(package.read("agent.json"))
                member_hashes = {info.filename: hashlib.sha256(package.read(info)).hexdigest() for info in package.infolist()}
            self.assertIs(type(manifest["schema_version"]), int)
            self.assertEqual(manifest["schema_version"], 1)
            for key in ("structured_output_enabled", "allow_text_output", "output_config"):
                self.assertNotIn(key, body)
            old, _ = await self.install(self.old, raw, f"old final off {allow_text}")
            overwritten, preview = await self.install(self.old, raw, f"old final off overwrite {allow_text}", target=old["id"])
            self.assertEqual(overwritten["skills"], ["example-skill"])
            self.assertEqual(overwritten["capabilities"], ["ui:package-fixture"])
            self.assertTrue(all(resource["status"] == "reuse" for resource in preview["resources"]))
            reply = await self.old.post("/api/v1/chat", json={"agent_id": old["id"], "message": "synthetic legacy chat"})
            self.assertEqual(reply.status_code, 200, reply.text)
            self.assertEqual(reply.json()["output"], "compatibility plain reply")
            self.record(f"OFF-{allow_text}-TO-OLD", raw, create=True, overwrite=True,
                        all_output_keys_absent=True, real_legacy_chat=True, member_sha256=member_hashes)

        old_source = await self.create(self.old, "authentic old source")
        old_raw = await self.export(self.old, old_source["id"])
        for allow_text in (False, True):
            enabled = await self.create(self.new, f"new enabled {allow_text}", structured_output_enabled=True,
                allow_text_output=allow_text, output_config=OUTPUT)
            raw = await self.export(self.new, enabled["id"])
            with zipfile.ZipFile(io.BytesIO(raw)) as package:
                body = json.loads(package.read("agent.json"))
            self.assertTrue(body["structured_output_enabled"])
            self.assertEqual(body["output_config"], OUTPUT)
            if allow_text:
                self.assertIs(body["allow_text_output"], True)
            else:
                self.assertNotIn("allow_text_output", body)
            # Rejected by both old preview and install; all old tables stay equal.
            target = await self.create(self.old, f"old rejection target {allow_text}")
            for overwrite in (False, True):
                state_before = original.state(self.old_db)
                data = {"mode": "overwrite", "target_agent_id": target["id"]} if overwrite else {}
                preview = await self.old.post("/api/v1/agent-packages/preview", data=data,
                    files={"file": ("agent.zip", raw, "application/zip")})
                self.assertEqual(preview.status_code, 422, preview.text)
                installed = await self.old.post("/api/v1/agent-packages/install",
                    files={"file": ("agent.zip", raw, "application/zip")},
                    data={"options": json.dumps({"digest": "0" * 64, "name": "must not write", **data})})
                self.assertEqual(installed.status_code, 422, installed.text)
                self.assertEqual(original.state(self.old_db), state_before)
            copied, _ = await self.install(self.new, raw, f"new copied {allow_text}")
            await self.check_final_config(copied["id"], enabled=True, allow_text=allow_text, definition=OUTPUT)
            await self.install(self.new, raw, f"new overwritten {allow_text}", target=copied["id"])
            await self.check_final_config(copied["id"], enabled=True, allow_text=allow_text, definition=OUTPUT)
            reply = await self.new.post("/api/v1/chat", json={"agent_id": copied["id"], "message": "synthetic installed output"})
            self.assertEqual(reply.status_code, 200, reply.text)
            self.assertEqual(reply.json()["output"], {"text": "structured compatibility proof"})
            self.assertTrue(reply.json().get("output_info"))
            session_id = reply.json()["session_id"]
            history = await self.new.get(f"/api/v1/sessions/{session_id}/history")
            self.assertEqual(history.status_code, 200, history.text)
            again = await self.export(self.new, copied["id"])
            twice, _ = await self.install(self.new, again, f"new second import {allow_text}")
            await self.check_final_config(twice["id"], enabled=True, allow_text=allow_text, definition=OUTPUT)
            self.record(f"ON-{allow_text}-TO-OLD-AND-NEW", raw,
                        old_create_and_overwrite_rejected=True, old_all_tables_unchanged=True,
                        final_create_overwrite_reexport_preserved=True)
            # Real pre-0.14 export clears even a mixed enabled overwrite target.
            old_into_new, _ = await self.install(self.new, old_raw, f"authentic old import {allow_text}")
            await self.check_final_config(old_into_new["id"], enabled=False, allow_text=False, definition=None)
            await self.install(self.new, old_raw, f"authentic old overwrite {allow_text}", target=copied["id"])
            await self.check_final_config(copied["id"], enabled=False, allow_text=False, definition=None)
            after_overwrite = await self.new.get(f"/api/v1/sessions/{session_id}/history")
            self.assertEqual(after_overwrite.status_code, 200, after_overwrite.text)
            self.assertEqual(after_overwrite.json(), history.json(), "old package must not rewrite accepted output history")
            self.record(f"OLD-TO-FINAL-{allow_text}", old_raw, create=True, overwrite=True,
                        target_flags_cleared=True)
