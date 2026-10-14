"""Selection guidance; Skill activation remains controlled by the model."""

SKILL_SELECTION_INSTRUCTIONS = """Skill selection:
Before task-specific actions or an answer, compare the task with the available Skill descriptions.
When a Skill clearly matches, load it with load_capability and read its instructions before
performing the related work. Wait for the load result; do not load and perform that work in
the same tool-call batch. A description is not a substitute for the Skill's instructions.
Load only matching Skills, including multiple Skills when needed; do not load unrelated
Skills or every associated Skill. If none matches, proceed without loading one.
Reuse Skill instructions already active in the current context instead of loading them again.
Read referenced documents only when the task or the loaded Skill's applicable steps require
them; do not read every bundled file. Skill selection does not grant additional tool permissions.
"""
