# SSE and History consistency

SSE describes the live Run. History is the durable source after refresh.

- A completed Run ends in `final`.
- A cancelled Run ends in `cancelled` and must not also emit `final`.
- A broadcast tool call must later have a result, an interrupted state, or an explicit removal event.
- Deferred Pending is not an active Run and uses its own result-resume flow.
- Automatic and manual compaction update one lifecycle Event in place; failed events do not replace the latest successful Surface and original Message Events are never rewritten.
