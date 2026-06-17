from __future__ import annotations

import unittest
from uuid import UUID

from deep_agent.db.ids import AGENT_ID_LENGTH, new_agent_id, normalize_agent_id


class AgentIdHelpersTests(unittest.TestCase):
    def test_new_agent_id_is_canonical_uuid_string(self) -> None:
        agent_id = new_agent_id()
        self.assertEqual(len(agent_id), AGENT_ID_LENGTH)
        self.assertEqual(agent_id, str(UUID(agent_id)))

    def test_normalize_agent_id_accepts_hyphenated_and_compact(self) -> None:
        canonical = "550e8400-e29b-41d4-a716-446655440000"
        compact = "550e8400e29b41d4a716446655440000"
        self.assertEqual(normalize_agent_id(canonical), canonical)
        self.assertEqual(normalize_agent_id(compact), canonical)
        self.assertEqual(normalize_agent_id(UUID(canonical)), canonical)


if __name__ == "__main__":
    unittest.main()
