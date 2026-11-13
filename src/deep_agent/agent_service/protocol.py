from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from a2a import types as wire
from a2a.server.request_handlers.request_handler import RequestHandler

from .execution import RunnerExecution, prepare_input
from .publications import PublicationScope, PublicationStore
from .tasks import Owner, TaskStore


def agent_card(row, address: str, *, attachment_mimes: frozenset[str]) -> wire.AgentCard:
    return wire.AgentCard(
        name=row.name, description=row.description or row.name, version=str(row.revision),
        supported_interfaces=[wire.AgentInterface(url=address, protocol_binding="JSONRPC", protocol_version="1.0")],
        capabilities=wire.AgentCapabilities(streaming=True, push_notifications=False, extended_agent_card=False),
        default_input_modes=sorted(attachment_mimes | {"text/plain", "application/json"}),
        default_output_modes=["text/plain", "application/json", "application/octet-stream"],
        skills=[wire.AgentSkill(id=row.code, name=row.name, description=row.description or row.name, tags=["agent"])],
        security_schemes={
            "platformBearer": wire.SecurityScheme(http_auth_security_scheme=wire.HTTPAuthSecurityScheme(scheme="bearer")),
            "platformKey": wire.SecurityScheme(api_key_security_scheme=wire.APIKeySecurityScheme(
                location="header", name="X-Deepfos-Key",
            )),
        },
        security_requirements=[
            wire.SecurityRequirement(schemes={"platformBearer": wire.StringList()}),
            wire.SecurityRequirement(schemes={"platformKey": wire.StringList()}),
        ],
    )


@dataclass
class PublicationHandler(RequestHandler):
    ctx: object
    tasks: TaskStore
    store: PublicationStore
    code: str
    owner: Owner
    headers: dict[str, str]
    address: str

    async def _submit(self, params: wire.SendMessageRequest):
        if not params.HasField("message"):
            raise wire.InvalidParamsError(message="message is required")
        if params.configuration.HasField("task_push_notification_config"):
            raise wire.PushNotificationNotSupportedError()
        text, uploads = await prepare_input(self.ctx, params.message)
        async with self.tasks.admission:
            publication = await self.store.get(self.code)
            if (publication is None or publication.id != self.owner.publication_id
                    or not PublicationScope.model_validate(publication.scope).allows(self.owner.app, self.owner.space)):
                raise wire.TaskNotFoundError()
            return self.tasks.submit(
                self.owner, publication.agent_id, params.message, enabled=publication.enabled,
                execute=RunnerExecution(self.ctx, self.headers, self.address, self.code, text, uploads),
                input_size=len(text.encode()) + sum(len(upload.data) for upload in uploads),
            )

    async def on_message_send(self, params, context):
        entry = await self._submit(params)
        if not params.configuration.return_immediately:
            await self.tasks.wait(entry)
        size = params.configuration.history_length if params.configuration.HasField("history_length") else None
        return entry.snapshot(size)

    async def on_message_send_stream(self, params, context) -> AsyncIterator:
        entry = await self._submit(params)
        stream = self.tasks.observe(entry, new_send=True)
        try:
            async for event in stream:
                if isinstance(event, wire.Task) and params.configuration.HasField("history_length"):
                    size = params.configuration.history_length
                    del event.history[:max(0, len(event.history) - size)]
                yield event
        finally:
            await stream.aclose()

    async def on_get_task(self, params, context):
        size = params.history_length if params.HasField("history_length") else None
        return self.tasks.get(self.owner, params.id).snapshot(size)

    async def on_list_tasks(self, params, context):
        if params.tenant:
            raise wire.InvalidParamsError(message="应用范围由经过验证的 app 和 space Header 确定")
        return self.tasks.page(self.owner, params)

    async def on_cancel_task(self, params, context):
        return await self.tasks.cancel(self.tasks.get(self.owner, params.id))

    async def on_subscribe_to_task(self, params, context) -> AsyncIterator:
        stream = self.tasks.observe(self.tasks.get(self.owner, params.id))
        try:
            async for event in stream:
                yield event
        finally:
            await stream.aclose()

    async def on_create_task_push_notification_config(self, params, context):
        raise wire.PushNotificationNotSupportedError()

    on_get_task_push_notification_config = on_create_task_push_notification_config
    on_list_task_push_notification_configs = on_create_task_push_notification_config
    on_delete_task_push_notification_config = on_create_task_push_notification_config

    async def on_get_extended_agent_card(self, params, context):
        raise wire.UnsupportedOperationError()
