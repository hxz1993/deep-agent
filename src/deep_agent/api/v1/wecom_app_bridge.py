"""Private Gateway bridge. Account/run facts select the app and recipient."""
import base64
import binascii
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from deep_agent.files.upload_settings import load_upload_settings
from deep_agent.agent.observability import log_event
from deep_agent.api.v1.channel_auth import ChannelAccountDep, ChannelControlAccountDep
from deep_agent.api.v1.wecom_applications import WecomRoute
from deep_agent.core.context import AppContextDep
from deep_agent.services.wecom_webhook import application_id, account_fingerprint

router = APIRouter(prefix='/internal/channels/wecom-app', route_class=WecomRoute)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    channel_run_id: str = Field(min_length=1, max_length=32)


class MediaRequest(RunRequest):
    media_id: str = Field(min_length=1, max_length=1024)


class SendRequest(RunRequest):
    message_type: Literal['text', 'markdown']
    text: str = Field(min_length=1, max_length=65536)


class ComponentRequest(RunRequest):
    kind: Literal['image', 'file', 'audio', 'video', 'textcard', 'news']
    data_base64: str | None = Field(default=None, max_length=27962028)
    filename: str = Field(default='', max_length=512)
    payload: dict | None = None

    @model_validator(mode='after')
    def validate_component(self):
        from deep_agent.services.wecom_client import MEDIA_LIMITS
        from deep_agent.channels.message_payloads import validate_message_payload
        if self.kind in MEDIA_LIMITS:
            if self.payload is not None or not self.data_base64 or not self.filename:
                raise ValueError('Media requires data and filename, without structured payload')
            if len(self.data_base64) > 4 * ((MEDIA_LIMITS[self.kind] + 2) // 3):
                raise ValueError('Media exceeds channel limit')
        else:
            if self.data_base64 is not None or self.filename:
                raise ValueError('Structured messages cannot contain media upload fields')
            self.payload = validate_message_payload(self.kind, self.payload)
        return self


async def _app(ctx, account):
    if account is None:
        raise HTTPException(401, 'Channel authentication required')
    if account.channel_type != 'wecom_app' or account.transport != 'webhook':
        raise HTTPException(422, 'Channel does not support application Webhook')
    app = await ctx.stores.wecom.get_application(application_id(account))
    if app is None or not app.app_secret:
        raise HTTPException(409, 'Application configuration unavailable')
    return app


async def _run(ctx, account, run_id):
    app = await _app(ctx, account)
    run = await ctx.stores.channel.get_run(run_id)
    binding = await ctx.stores.channel.get_binding_by_id(run.binding_id) if run else None
    metadata = (run.source_metadata or {}) if run else {}
    if (run is None or run.account_id != account.id or binding is None
            or binding.account_id != account.id or binding.peer_type != 'single'
            or not run.sender_id or binding.peer_id != run.sender_id
            or metadata.get('wecom_app_application_id') != app.id
            or metadata.get('wecom_app_revision') != app.revision
            or metadata.get('wecom_app_corp_id') != app.corp_id
            or metadata.get('wecom_app_agent_id') != str(app.wecom_agent_id)
            or metadata.get('wecom_app_fingerprint') != account_fingerprint(account)):
        raise HTTPException(409, 'Channel message or application configuration changed')
    if run.session_id:
        session = await ctx.stores.channel.get_channel_session(account.id, run.session_id)
        if (session is None or binding.session_id != run.session_id
                or session.agent_id != account.agent_id or session.user_id != account.session_user_id):
            raise HTTPException(409, 'Channel session changed')
    return app, run


@router.get('/info')
async def info(ctx: AppContextDep, account: ChannelControlAccountDep):
    app = await _app(ctx, account)
    await ctx.get_wecom_profiles().client.token(app)
    return {'application_id': app.id, 'corp_id': app.corp_id,
            'agent_id': app.wecom_agent_id, 'revision': app.revision}


@router.post('/media')
async def media(body: MediaRequest, ctx: AppContextDep, account: ChannelAccountDep):
    app, run = await _run(ctx, account, body.channel_run_id)
    metadata = run.source_metadata or {}
    if metadata.get('wecom_app_media_id') != body.media_id:
        raise HTTPException(403, 'Media does not belong to this channel message')
    policy = await load_upload_settings(ctx.stores.global_setting)
    fields = dict(account_id=account.id, channel_run_id=run.id,
                  request_id=metadata.get('wecom_app_request_id'), phase='media')
    try:
        data, filename, mime = await ctx.get_wecom_profiles().client.media(app, body.media_id, limit=policy.max_bytes)
        # Recheck the current account/app snapshot after the network operation.
        current = await ctx.stores.channel.get_account(account.id)
        if current is None or not current.enabled:
            raise HTTPException(409, 'Channel configuration changed')
        await _run(ctx, current, run.id)
        filename = PurePosixPath((filename or '').replace('\\', '/')).name[:180]
        headers = {'Content-Disposition': "attachment; filename*=UTF-8''" + quote(filename, safe='')} if filename else {}
        log_event('webhook_bridge_completed', **fields, byte_size=len(data))
        return Response(data, media_type=mime, headers=headers)
    except Exception as exc:
        log_event('webhook_bridge_failed', **fields, error_type=type(exc).__name__, error_code=getattr(exc, 'code', None))
        raise


@router.post('/send-message')
async def send_message(body: SendRequest, ctx: AppContextDep, account: ChannelAccountDep):
    app, run = await _run(ctx, account, body.channel_run_id)
    fields = dict(account_id=account.id, channel_run_id=run.id,
                  request_id=(run.source_metadata or {}).get('wecom_app_request_id'), phase='send', message_type=body.message_type)
    try:
        async def before_send():
            current = await ctx.stores.channel.get_account(account.id)
            if current is None or not current.enabled:
                raise HTTPException(409, 'Channel configuration changed')
            await _run(ctx, current, run.id)

        result = await ctx.get_wecom_profiles().client.send_message(
            app, run.sender_id, body.text, message_type=body.message_type, before_send=before_send)
        log_event('webhook_bridge_completed', **fields, count=result['part_count'])
        return result
    except Exception as exc:
        log_event('webhook_bridge_failed', **fields, error_type=type(exc).__name__, error_code=getattr(exc, 'code', None))
        raise


@router.post('/send-component')
async def send_component(body: ComponentRequest, ctx: AppContextDep, account: ChannelAccountDep):
    from deep_agent.services.wecom_client import validate_outbound_media, WecomError
    app, run = await _run(ctx, account, body.channel_run_id)
    data = None
    if body.data_base64 is not None:
        policy = await load_upload_settings(ctx.stores.global_setting)
        if len(body.data_base64) > 4 * ((policy.max_bytes + 2) // 3):
            raise HTTPException(422, 'Media exceeds current upload size limit')
        try:
            data = base64.b64decode(body.data_base64, validate=True)
            if len(data) > policy.max_bytes:
                raise ValueError("Media exceeds upload size limit")
            validate_outbound_media(body.kind, data)
        except (ValueError, binascii.Error, WecomError):
            raise HTTPException(422, 'Invalid media format or size') from None
    async def before_send():
        current = await ctx.stores.channel.get_account(account.id)
        if current is None or not current.enabled:
            raise HTTPException(409, 'Channel configuration changed')
        await _run(ctx, current, run.id)
    fields = dict(account_id=account.id, channel_run_id=run.id,
                  phase='send', message_type=body.kind)
    try:
        result = await ctx.get_wecom_profiles().client.send_component(
            app, run.sender_id, body.kind, data=data, filename=body.filename,
            payload=body.payload, before_send=before_send)
        log_event('webhook_bridge_completed', **fields, count=1)
        return result
    except Exception as exc:
        log_event('webhook_bridge_failed', **{**fields, 'phase': getattr(exc, 'stage', 'send')}, error_type=type(exc).__name__,
                  error_code=getattr(exc, 'code', None))
        raise
