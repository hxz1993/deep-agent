from fastapi import APIRouter, HTTPException, Response
from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.schemas.channel import FeishuRegistrationCreate
from deep_agent.services.channel_registration import RegistrationError
from deep_agent.services.feishu_registration import registration_service

router = APIRouter(prefix="/channel-accounts/feishu/registrations", dependencies=CONSOLE_ADMIN_DEPENDENCIES)


@router.post("", status_code=201)
async def start_registration(user: RequestUserDep, payload: FeishuRegistrationCreate):
    try:
        return await registration_service.start(user, app_name=payload.app_name)
    except RegistrationError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/{registration_id}")
async def registration_status(registration_id: str, user: RequestUserDep):
    try:
        return registration_service.snapshot(registration_id, user)
    except RegistrationError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.delete("/{registration_id}", status_code=204)
async def cancel_registration(registration_id: str, user: RequestUserDep):
    try:
        await registration_service.cancel(registration_id, user)
    except RegistrationError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return Response(status_code=204)
