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
        self.assertIn("pdf_text", kinds)
        self.assertIn("text_file", kinds)

    def test_upload_capabilities_payload_shape(self) -> None:
        payload = upload_capabilities_payload()
        self.assertIn("kinds", payload)
        self.assertIn("labels", payload)
        self.assertIn("allowed_mimes", payload)

    def test_capability_block_lists_llm_and_upload_separately(self) -> None:
        spec = AgentSpec(
            name="test",
            model="deepseek-v4-flash",
            instructions="You are helpful.",
            input_modalities=["text"],
            capabilities_source="bundled",
        )
        block = build_capability_instruction_block(spec)
        self.assertIn("LLM native input modalities", block)
        self.assertIn("Platform upload pipeline", block)
        self.assertIn("NOT available", block)
        self.assertIn("Do not infer capabilities", block)
        self.assertNotIn("supports PDF/Office/audio/video native", block)

    def test_capability_block_mentions_no_vision_for_text_only(self) -> None:
        spec = AgentSpec(
            name="test",
            model="deepseek-v4-flash",
            instructions="x",
            input_modalities=["text"],
            capabilities_source="bundled",
        )
        block = build_capability_instruction_block(spec)
        self.assertIn("does NOT support native image input", block)

    def test_augment_instructions_appends_block(self) -> None:
        spec = AgentSpec(
            name="test",
            model="openai:gpt-4o",
            instructions="Base prompt.",
            input_modalities=["text", "image"],
            capabilities_source="user",
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
                    capabilities_source="user",
                )
            ),
            input_modalities=["text"],
            capabilities_source="user",
        )
        once = augment_instructions_with_capabilities(spec)
        twice = augment_instructions_with_capabilities(
            AgentSpec(
                name="test",
                model="openai:gpt-4o",
                instructions=once,
                input_modalities=["text"],
                capabilities_source="user",
            )
        )
        self.assertEqual(once.count("Session input capabilities"), 1)
        self.assertEqual(twice.count("Session input capabilities"), 1)


if __name__ == "__main__":
    unittest.main()
