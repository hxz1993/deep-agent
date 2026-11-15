---
name: deepfos-agent-call
description: Use the deepfos-agent CLI to discover published DeepAgent agents, submit and resume tasks, and retrieve artifacts.
---

Use this Skill when the user asks to call a published DeepAgent agent via CLI.

1. Run `deepfos-agent agents list --json`. Choose the `agent_code` that matches the user's request. Each task requires the target App. Use the configured App or the user's explicit choice.
2. Generate a fresh message ID and run `deepfos-agent tasks submit <agent_code> --message-id <id> --text '<request>' --json`. For a local attachment add `--attach <path>`. On an ambiguous transport failure retry the same message and ID.
3. Run `deepfos-agent tasks wait <task_id> --json`. On timeout keep the Task ID and retry waiting. For `input_required`, get the requested information and use `deepfos-agent tasks reply <task_id> --reply-to <question_id> --message-id <new_id> --text '<answer>' --json`. For `auth_required`, use refreshed credentials for the same platform identity.
4. Use `deepfos-agent artifacts download <task_id> <artifact_id> -o <path> --json` for files. Return the actual result or saved path. A follow-up after completion can pass `--context-id <context_id>` to a new submission.

The host supplies `DEEPFOS_AGENT_URL`, `DEEPFOS_AGENT_APP`, and either `DEEPFOS_AGENT_API_KEY` or `DEEPFOS_AGENT_TOKEN`. Keep credential values out of commands, logs and replies.
