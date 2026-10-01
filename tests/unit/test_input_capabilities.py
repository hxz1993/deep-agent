from __future__ import annotations

import unittest

from deep_agent.agent.input_capabilities import (
    augment_instructions_with_capabilities,
    build_capability_instruction_block,
    upload_capability_kinds,
    upload_capabilities_payload,
)
from deep_agent.agent.spec import AgentSpec


class TestInputCapabilities(unittest.TestCase):
    def test_upload_capability_kinds_includes_defaults(self) -> None:
        kinds = upload_capability_kinds()
        self.assertIn("image", kinds)
        self.assertNotIn("pdf", kinds)
        self.assertIn("text_file", kinds)
        self.assertIn("docx", kinds)

    def test_upload_capabilities_payload_shape(self) -> None:
        payload = upload_capabilities_payload()
        self.assertIn("kinds", payload)
        self.assertIn("labels", payload)
        self.assertIn("allowed_mimes", payload)
        self.assertEqual(payload["max_count"], 10)
        self.assertEqual(payload["max_size_mb"], 20)
        self.assertIn("LOG", payload["labels"]["text_file"])

    def test_capability_block_lists_upload_without_local_model_gate(self) -> None:
        spec = AgentSpec(
            name="test",
            model="deepseek-v4-flash",
            instructions="You are helpful.",
            input_modalities=["text"],
        )
        block = build_capability_instruction_block(spec)
        self.assertIn("Platform upload pipeline", block)
        self.assertNotIn("LLM native input modalities", block)
        self.assertIn("Formats outside the upload list are blocked", block)
        self.assertNotIn("read_document", block)
        self.assertIn("Do not infer capabilities", block)
        self.assertNotIn("supports PDF/Office/audio/video native", block)

    def test_capability_block_leaves_image_acceptance_to_provider(self) -> None:
        spec = AgentSpec(
            name="test",
            model="deepseek-v4-flash",
            instructions="x",
            input_modalities=["text"],
        )
        block = build_capability_instruction_block(spec)
        self.assertIn("Provider determines whether the selected model accepts", block)
        self.assertNotIn("rejected", block)

    def test_capability_block_claims_automatic_read_file_independent_of_packages(self) -> None:
        empty = build_capability_instruction_block(
            AgentSpec(
                name="test",
                model="test",
                instructions="",
                input_modalities=["text"],
                capabilities=[],
            )
        )
        selected = build_capability_instruction_block(
            AgentSpec(
                name="test",
                model="test",
                instructions="",
                input_modalities=["text"],
                capabilities=["builtin:sandbox"],
            )
        )
        self.assertIn("read_file system tool", empty)
        self.assertIn("read_file system tool", selected)
        self.assertNotIn("builtin:attachment", empty)
        self.assertNotIn("read_text_attachment", selected)

    def test_augment_instructions_appends_block(self) -> None:
        spec = AgentSpec(
            name="test",
            model="openai:gpt-4o",
            instructions="Base prompt.",
            input_modalities=["text", "image"],
        )
        merged = augment_instructions_with_capabilities(spec)
        self.assertTrue(merged.startswith("Base prompt."))
        self.assertIn("Session input capabilities", merged)

    def test_augment_instructions_idempotent(self) -> None:
        spec = AgentSpec(
            name="test",
            model="openai:gpt-4o",
            instructions=augment_instructions_with_capabilities(
                AgentSpec(
                    name="test",
                    model="openai:gpt-4o",
                    instructions="Once.",
                    input_modalities=["text"],
                )
            ),
            input_modalities=["text"],
        )
        once = augment_instructions_with_capabilities(spec)
        twice = augment_instructions_with_capabilities(
            AgentSpec(
                name="test",
                model="openai:gpt-4o",
                instructions=once,
                input_modalities=["text"],
            )
        )
        self.assertEqual(once.count("Session input capabilities"), 1)
        self.assertEqual(twice.count("Session input capabilities"), 1)

    def test_pdf_is_not_an_available_upload_or_native_modality(self) -> None:
        spec = AgentSpec(
            name="test",
            model="test",
            instructions="",
            input_modalities=["text", "image"],
        )
        block = build_capability_instruction_block(spec)
        self.assertNotIn("PDF", block)
        self.assertNotIn("read_document", block)
        self.assertIn("Professional document parsing is available only when its tool is explicitly enabled", block)


if __name__ == "__main__":
    unittest.main()
