---
name: deepagent-diagnostics
description: Diagnose DeepAgent session, model, tool, compaction, cancellation, SSE, and history problems using a safe evidence-first checklist.
---

# DeepAgent diagnostics

Use this Skill when the user asks to diagnose a DeepAgent runtime problem.

1. Identify the exact Agent, Session, model, request phase, and expected terminal event.
2. Separate client rendering, DeepAgent orchestration, Provider, MCP, and database evidence.
3. Compare real-time SSE with persisted History before concluding that data was lost.
4. Treat identifiers, timestamps, error types, usage counters, and Checkpoint state as evidence.
5. Never request or repeat API keys, cookies, authorization headers, attachment bodies, or Context Reference payloads.
6. Prefer read-only checks. Do not execute Shell commands or read server logs unless another explicitly authorized tool provides that ability.
7. State the root cause confidence, the smallest safe fix, and the exact regression case.
