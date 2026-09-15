"""Reviewable deletion snapshots. Confirmation is scoped to the exact child set."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class DeletionImpact:
    token: str
    counts: dict[str, int]
    session_ids: tuple[str, ...] = ()

    @classmethod
    def build(cls, kind: str, resource_id: str, user_id: str, *, sessions=(), bindings=(), runs=()):
        sessions, bindings, runs = sorted(sessions), sorted(bindings), sorted(runs)
        fingerprint = json.dumps([kind, resource_id, user_id, sessions, bindings, runs], ensure_ascii=True, default=str)
        own = sum(row[1] == user_id for row in sessions)
        return cls(hashlib.sha256(fingerprint.encode()).hexdigest(), {
            "session_count": len(sessions), "own_session_count": own,
            "other_session_count": len(sessions) - own,
            "binding_count": len(bindings), "run_count": len(runs),
        }, tuple(row[0] for row in sessions))


class DeletionConfirmationRequired(ValueError):
    def __init__(self, impact: DeletionImpact):
        super().__init__("删除会同时清理关联记录，请确认影响范围后再次删除")
        self.impact = impact

    def detail(self):
        return {"code": "delete_confirmation_required", "message": str(self),
                "impact": self.impact.counts, "confirmation_token": self.impact.token}
