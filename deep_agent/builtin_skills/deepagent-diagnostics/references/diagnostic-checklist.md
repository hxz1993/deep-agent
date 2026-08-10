# Diagnostic checklist

- Confirm Agent ID, Session ID, model ID, Run ID, and Conversation ID.
- Capture the last successful SSE phase and the actual terminal event.
- Compare tool calls and results by tool call ID in SSE and History.
- For compaction, compare source tokens, projected tokens, Checkpoint row version, and active state.
- For cancellation, verify `cancel_requested`, exactly one `cancelled`, no `final`, and interrupted History.
- For database incidents, distinguish readiness, pool checkout, transaction, and business-query errors.
- Record only sanitized identifiers and counters.
