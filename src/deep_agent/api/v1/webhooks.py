"""Public protocol endpoints; only verified messages cross the gateway boundary."""
from __future__ import annotations

import asyncio
import time
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from deep_agent.agent.observability import log_event
from deep_agent.core.context import AppContextDep
from deep_agent.services.channel_gateway_client import ChannelGatewayClient, ChannelGatewayUnavailableError, ChannelWebhookConflictError
from deep_agent.services.wecom_webhook import (
    MAX_WEBHOOK_BYTES, WebhookError, application_id, decrypt, parse_message,
    signature_params, xml_fields,
)

router = APIRouter()
_slots = asyncio.Semaphore(32)
_log_window = 0.0
_log_count = 0
_log_suppressed = 0


def _admit_log() -> bool:
    """Bound anonymous ingress amplification, without collecting arbitrary IPs."""
    global _log_window, _log_count, _log_suppressed
    now = time.monotonic()
    if now - _log_window >= 60:
        if _log_suppressed:
            log_event("webhook_log_limited", requested_count=_log_suppressed, reason="ingress_log_budget")
        _log_window, _log_count, _log_suppressed = now, 0, 0
    _log_count += 1
    if _log_count > 1000:
        _log_suppressed += 1
        return False
    return True


async def _body(request):
    result = bytearray()
    async for chunk in request.stream():
        result.extend(chunk)
        if len(result) > MAX_WEBHOOK_BYTES:
            raise WebhookError("body_too_large", 413)
    return bytes(result)


@router.get("/webhooks/channels/{account_id}", summary="Verify WeCom application Webhook URL")
@router.post("/webhooks/channels/{account_id}", summary="Receive verified WeCom application messages")
@router.api_route("/webhooks/channels/{account_id}", methods=["PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"], include_in_schema=False)
async def channel_webhook(account_id: str, request: Request, ctx: AppContextDep):
    request_id = uuid4().hex  # Never trust anonymous caller-supplied correlation IDs.
    started = time.monotonic()
    verbose = _admit_log()
    facts = {"request_id": request_id, "account_id": account_id[:128], "http_method": request.method}
    def emit(event, **fields):
        if verbose:
            log_event(event, **facts, **fields)
    emit("webhook_received")
    response = None
    acquired = False
    try:
        if request.method not in {"GET", "POST"}:
            raise WebhookError("method_not_allowed", 405)
        if _slots.locked():
            raise WebhookError("ingress_busy", 429)
        await _slots.acquire()
        acquired = True
        async with asyncio.timeout(4):
            account = await ctx.stores.channel.get_account(account_id) if ctx.stores.channel else None
            if account is None:
                raise WebhookError("channel_not_found", 404)
            if account.channel_type != "wecom_app" or account.transport != "webhook":
                raise WebhookError("webhook_not_supported", 422)
            if not account.webhook_token or not account.webhook_aes_key or not application_id(account):
                raise WebhookError("webhook_not_configured", 409)
            app = await ctx.stores.wecom.get_application(application_id(account))
            if app is None or not app.app_secret:
                raise WebhookError("application_unavailable", 409)
            emit("webhook_checked", phase="configuration", status="ready")
            sig, timestamp, nonce = signature_params(request.query_params)
            if request.method == "GET":
                echo = request.query_params.getlist("echostr")
                if len(echo) != 1:
                    raise WebhookError("invalid_parameters")
                plaintext = decrypt(account, app, echo[0], sig, timestamp, nonce)
                emit("webhook_verified", phase="url_verification")
                response = PlainTextResponse(plaintext)
            else:
                raw = await _body(request)
                emit("webhook_checked", phase="body_received", byte_size=len(raw))
                outer = xml_fields(raw)
                plaintext = decrypt(account, app, outer.get("Encrypt", ""), sig, timestamp, nonce)
                # Validate optional outer identity too; inner signed identity is authoritative.
                if outer.get("ToUserName", app.corp_id) != app.corp_id or outer.get("AgentID", str(app.wecom_agent_id)) != str(app.wecom_agent_id):
                    raise WebhookError("receiver_mismatch", 403)
                body = parse_message(plaintext, account, app, request_id=request_id)
                emit("webhook_verified", phase="message", message_type=body["message_type"] if body else "event")
                if not account.enabled:
                    raise WebhookError("channel_disabled", 403)
                if body is None:
                    emit("webhook_accepted", phase="event", status="ignored")
                else:
                    result = await ChannelGatewayClient().receive_webhook(account_id, body)
                    if result.get("status") == "conflict":
                        raise WebhookError("message_conflict", 409)
                    if result.get("status") not in {"accepted", "duplicate"}:
                        raise WebhookError("gateway_receive_failed", 503)
                    emit("webhook_accepted", phase="gateway", status=result["status"],
                         external_message_id=body["message_id"], channel_run_id=result.get("channel_run_id"))
                response = PlainTextResponse("success")
    except WebhookError as exc:
        emit("webhook_rejected", error_code=exc.code, http_status=exc.status)
        response = JSONResponse({"code": exc.code, "detail": "该渠道不支持 Webhook" if exc.code == "webhook_not_supported" else "Webhook 请求未通过，请按 request_id 查看服务日志", "request_id": request_id}, status_code=exc.status)
    except ChannelWebhookConflictError:
        emit("webhook_rejected", error_code="message_or_configuration_conflict", http_status=409)
        response = JSONResponse({"code": "message_or_configuration_conflict", "request_id": request_id}, status_code=409)
    except (TimeoutError, ChannelGatewayUnavailableError) as exc:
        emit("webhook_failed", error_code="receive_unavailable", error_type=type(exc).__name__)
        response = JSONResponse({"code": "receive_unavailable", "request_id": request_id}, status_code=503)
    except Exception as exc:
        # Exception text can include request URLs, XML and credentials.
        emit("webhook_failed", error_code="internal_error", error_type=type(exc).__name__)
        response = JSONResponse({"code": "internal_error", "request_id": request_id}, status_code=503)
    finally:
        if acquired:
            _slots.release()
        emit("webhook_completed", http_status=response.status_code if response is not None else 499,
             duration_ms=round((time.monotonic() - started) * 1000))
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "no-store"
    if response.status_code == 405:
        response.headers["Allow"] = "GET, POST"
    return response
