"""Turn-frozen output contracts. Final JSON is a result, never a UI approval."""
from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from typing import Annotated, Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import WithJsonSchema
from pydantic_ai import DeferredToolRequests, ModelRetry, StructuredDict, ToolOutput
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart

CONTRACT = "deepagent_output_contract"
CONTRACT_REF = "deepagent_output_contract_ref"
BUDGET = "deepagent_output_budget"
DISPATCH = "deepagent_dispatch"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def output_id_for(session_id: str, run_id: str, ordinal: int, part_index: int) -> str:
    return uuid5(NAMESPACE_URL, f"deep-agent-output:{session_id}:{run_id}:{ordinal}:{part_index}").hex


@lru_cache(maxsize=128)
def _output_type(serialized: str):
    contract = json.loads(serialized)
    schema = contract["json_schema"]
    # StructuredDict's Any-valued dictionary keeps omission/null semantics.
    # Other roots use the SDK's transparent `response` argument wrapper.
    value_type = (StructuredDict(schema) if schema.get("type") == "object"
                  else Annotated[Any, WithJsonSchema(schema)])
    return ToolOutput(value_type, name=contract["name"], description=contract.get("description") or None,
                      strict=False, max_retries=2)


class StructuredOutputState(AbstractCapability):
    def __init__(self, contract: dict[str, Any], *, source_event_id: str | None = None,
                 rejected_keys: list[str] | None = None, attempts: int = 0):
        from deep_agent.structured_output.schema import normalize_schema, schema_digest, validate_output_name
        schema = normalize_schema(contract["json_schema"])
        validate_output_name(contract["name"])
        self.contract = {"name": contract["name"], "description": contract.get("description"),
                         "json_schema": schema, "schema_digest": schema_digest(schema)}
        if contract.get("schema_digest") not in (None, self.contract["schema_digest"]):
            raise ValueError("Output contract snapshot digest does not match")
        self.source_event_id = source_event_id
        self.rejected_keys = list(dict.fromkeys(rejected_keys or []))
        self.attempt = attempts
        self.progress = None
        self.queue = None
        self.pending: dict[str, Any] | None = None
        self.accepted: dict[str, Any] | None = None
        self._checked: set[int] = set()

    @property
    def output_types(self):
        return [_output_type(_json(self.contract)), DeferredToolRequests]

    @property
    def budget(self):
        return {"correction_limit": 2, "rejected_response_keys": list(self.rejected_keys),
                "rejected_responses": len(self.rejected_keys),
                "remaining_corrections": max(0, 2 - len(self.rejected_keys))}

    def prepare_request(self, request: ModelRequest):
        metadata = dict(request.metadata or {})
        if self.source_event_id:
            metadata[CONTRACT_REF] = {"event_id": self.source_event_id,
                                      "schema_digest": self.contract["schema_digest"]}
        if self.rejected_keys:
            metadata[BUDGET] = self.budget
        request.metadata = metadata or None

    async def before_model_request(self, ctx, request_context):
        parameters = request_context.model_request_parameters
        names = {tool.name for tool in parameters.function_tools}
        if self.contract["name"] in names:
            raise ValueError("Output name conflicts with an authorized tool")
        return request_context

    async def after_model_request(self, ctx, *, request_context, response):
        if self.progress is None:
            await self.check_response(ctx, response)
        return response

    async def check_response(self, ctx, response: ModelResponse, progress=None):
        """Inspect the complete response before *any* host tool is dispatched."""
        if id(response) in self._checked:
            return
        self._checked.add(id(response))
        calls = [(i, part) for i, part in enumerate(response.parts) if isinstance(part, ToolCallPart)]
        outputs = [(i, part) for i, part in calls if part.tool_name == self.contract["name"]]
        if outputs:
            response.metadata = {**(response.metadata or {}), "deepagent_output": {
                "name": self.contract["name"], "part_indices": [i for i, _ in outputs],
                "schema_digest": self.contract["schema_digest"],
            }}
        native = [i for i, part in enumerate(response.parts)
                  if getattr(part, "part_kind", "") in {"builtin-tool-call", "native-tool-call"}]
        reason = None
        errors = []
        value = None
        if len(outputs) > 1:
            reason = "multiple_output_calls"
        elif outputs and (len(calls) != 1 or native):
            reason = "mixed_output_calls"
        elif not calls and not native:
            reason = "missing_output"
        elif outputs:
            try:
                args = outputs[0][1].args
                if isinstance(args, str):
                    def reject_constant(value):
                        raise ValueError("Non-finite JSON number")
                    def unique_object(pairs):
                        result = {}
                        for key, item in pairs:
                            if key in result:
                                raise ValueError("Duplicate JSON object key")
                            result[key] = item
                        return result
                    args = json.loads(args, parse_constant=reject_constant, object_pairs_hook=unique_object)
                value = args if self.contract["json_schema"].get("type") == "object" else args["response"]
                from deep_agent.structured_output.validation import validate_output, OutputValidationServiceError
                try:
                    errors = await validate_output(self.contract["json_schema"], value)
                except OutputValidationServiceError:
                    if progress is not None:
                        progress._record(response)
                        await progress._save(ctx, hook="structured_output_service_failure")
                    raise
                if errors:
                    reason = "schema_validation_failed"
            except (ValueError, KeyError, TypeError):
                reason = "invalid_json"
        if reason:
            response.metadata = {**(response.metadata or {}), DISPATCH: {
                "rejected": True, "reason": reason,
                "not_dispatched_part_indices": [i for i, _ in calls],
            }}
        if outputs or reason:
            response.metadata = {**(response.metadata or {}), "deepagent_output": {
                "name": self.contract["name"], "part_indices": [i for i, _ in outputs],
                "schema_digest": self.contract["schema_digest"],
            }}
        if reason:
            ordinal = self._ordinal(response, progress)
            key = f"{ctx.deps.run_id}:{ordinal}"
            if key not in self.rejected_keys:
                self.rejected_keys.append(key)
            response.metadata[BUDGET] = self.budget
        if progress is not None:
            progress._record(response)
            await progress._save(ctx, hook="structured_output_response")
        if outputs or reason:
            self.attempt += 1
            index, call = outputs[0] if outputs else (-1, None)
            self.pending = {"name": self.contract["name"], "tool_call_id": call.tool_call_id if call else None,
                            "attempt": self.attempt, "schema_digest": self.contract["schema_digest"],
                            "source": {"message_ordinal": self._ordinal(response, progress), "part_index": index},
                            "accepted": False}
            if reason:
                self.pending["errors"] = errors or [{"code": reason, "message": reason}]
                await self._append_attempt(ctx.deps, accepted=False)
            else:
                self.pending["output"] = deepcopy(value)
        if reason:
            if len(self.rejected_keys) > 2:
                raise UnexpectedModelBehavior("output_retry_exhausted: output correction limit reached")
            raise ModelRetry(_json({"code": reason, "errors": errors})[:6000])

    @staticmethod
    def _ordinal(response, progress):
        if progress is not None:
            return next((int(message.metadata["deepagent_message_ordinal"])
                         for message in progress._with_ordinals(progress._ledger)
                         if progress._same_message(message, response)), len(progress._ledger))
        return int((response.metadata or {}).get("deepagent_message_ordinal", 0))

    async def after_output_validate(self, ctx, *, output_context, output):
        if ctx.partial_output:
            return output
        # The raw JSON already passed full validation before dispatch. Preserve
        # it exactly rather than inheriting any SDK field/default conversion.
        return deepcopy(self.pending["output"]) if self.pending and "output" in self.pending else output

    async def _append_attempt(self, deps, *, accepted: bool):
        if self.pending is None:
            raise RuntimeError("Output has no complete model response")
        payload = {**self.pending, "accepted": accepted}
        row = await deps.output_store.append_output_attempt(
            deps.session_id, agent_id=deps.agent_id, user_id=deps.user,
            turn_id=deps.turn_id, run_id=deps.run_id, payload=payload,
        )
        if accepted:
            # Acceptance is a committed database fact. Set it before optional
            # transport notifications so a disconnected observer cannot reverse
            # completion or cause a contradictory execution/error to be saved.
            self.accepted = {"name": payload["name"], "output_id": row.event_id,
                             "tool_call_id": payload["tool_call_id"], "attempt": payload["attempt"],
                             "turn_id": deps.turn_id}
        public = row.public_dict()
        if self.queue is not None:
            try:
                self.queue.put_nowait({"type": "session_event_appended", "persisted": True, "event": public})
                self.queue.put_nowait({"type": "output", "session_id": deps.session_id,
                    "turn_id": deps.turn_id, "run_id": deps.run_id, "output_id": row.event_id,
                    "sequence": row.sequence, **{k: v for k, v in row.payload.items() if k != "source"},
                    "status": "completed" if accepted else "error"})
            except Exception:
                # History/Event API remain authoritative if an observer has
                # stopped consuming. Transport failure is not model failure.
                pass
        return row

    async def accept(self, deps):
        return await self._append_attempt(deps, accepted=True)
