"""Nacos password, static AK/SK and ECS RAM-role authentication."""
import asyncio
import base64
import hashlib
import hmac
import math
import time
from abc import ABC, abstractmethod
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional
from urllib.parse import quote

from aiohttp import ClientTimeout
from loguru import logger

from .httpcli import AioHttpCli


def hmac_sha1_base64(data: str, secret: str) -> str:
    digest = hmac.new(secret.encode('utf-8'), data.encode('utf-8'), hashlib.sha1).digest()
    return base64.b64encode(digest).decode('ascii')


def build_naming_sign_fields(
    *, access_key: str, secret_key: str, service_name: str = '', group_name: str = '',
    security_token: Optional[str] = None, timestamp_ms: Optional[int] = None,
) -> Dict[str, str]:
    ts = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    grouped = service_name
    if service_name and group_name and '@@' not in service_name:
        grouped = f'{group_name}@@{service_name}'
    data = f'{ts}@@{grouped}' if grouped else str(ts)
    fields = {'ak': access_key, 'data': data, 'signature': hmac_sha1_base64(data, secret_key)}
    if security_token:
        fields['Spas-SecurityToken'] = security_token
    return fields


def build_config_sign_fields(
    *, access_key: str, secret_key: str, tenant: str, group: str,
    security_token: Optional[str] = None, timestamp_ms: Optional[int] = None,
) -> Dict[str, str]:
    ts = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    resource = '+'.join(part for part in (tenant, group) if part)
    data = f'{resource}+{ts}' if resource else str(ts)
    fields = {'Spas-AccessKey': access_key, 'Timestamp': str(ts),
              'Spas-Signature': hmac_sha1_base64(data, secret_key)}
    if security_token:
        fields['Spas-SecurityToken'] = security_token
    return fields


def classify_endpoint(endpoint: str) -> str:
    prefix = endpoint.lstrip('/').split('/', 1)[0]
    return {'ns': 'naming', 'cs': 'config', 'auth': 'auth'}.get(prefix, 'other')


@dataclass(frozen=True, repr=False)
class StsCredentials:
    access_key_id: str
    access_key_secret: str
    security_token: Optional[str]
    expiration: float


class AuthProvider:
    def bind(self, api):
        self.api = api

    async def initialize(self):
        await self.prepare_request()

    async def prepare_request(self):
        pass

    async def refresh(self, schedule_renewal: bool = True) -> bool:
        return False

    def enrich(self, endpoint: str, body: dict) -> dict:
        return body.copy()

    async def close(self):
        pass


class PasswordAuthProvider(AuthProvider):
    def __init__(self, user: Optional[str] = None, password: Optional[str] = None):
        self.user = user
        self.password = password
        self._renewal_task = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def initialize(self):
        await self.refresh()

    async def refresh(self, schedule_renewal: bool = True) -> bool:
        if self._closed or not (self.user and self.password):
            return False
        async with self._lock:
            if self._closed:
                return False
            for attempt in range(3):
                try:
                    result = await self.api.login(self.user, self.password)
                    token, ttl = result['accessToken'], float(result['tokenTtl'])
                    if not isinstance(token, str) or not token.strip() or not math.isfinite(ttl) or ttl <= 0:
                        raise ValueError('Invalid login response')
                    break
                except Exception:
                    if attempt == 2:
                        raise RuntimeError('Nacos password login failed') from None
                    await asyncio.sleep(1)
            if self._closed:
                return False
            self.api._token = token
            if schedule_renewal:
                await self._cancel_renewal()
                if not self._closed:
                    delay = max(0.1, min(ttl * 2 / 3, ttl - 5))
                    self._renewal_task = asyncio.create_task(self._renew(delay))
            return not self._closed

    async def _renew(self, delay: float):
        try:
            await asyncio.sleep(delay)
            await self.refresh()
        except Exception:
            logger.warning('Nacos password token renewal failed.')

    async def _cancel_renewal(self):
        task = self._renewal_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._renewal_task = None

    def enrich(self, endpoint: str, body: dict) -> dict:
        fields = body.copy()
        if classify_endpoint(endpoint) != 'auth' and self.api._token:
            fields['accessToken'] = self.api._token
        return fields

    async def close(self):
        self._closed = True
        await self._cancel_renewal()


class SignedAuthProvider(AuthProvider, ABC):
    def __init__(self):
        self._credentials: Optional[StsCredentials] = None

    @abstractmethod
    async def prepare_request(self):
        pass

    def enrich(self, endpoint: str, body: dict) -> dict:
        fields = body.copy()
        kind = classify_endpoint(endpoint)
        if kind not in ('naming', 'config'):
            return fields
        credentials = self._credentials
        if credentials is None:
            raise RuntimeError('Nacos RAM credentials have not been initialized')
        keys = {'access_key': credentials.access_key_id,
                'secret_key': credentials.access_key_secret,
                'security_token': credentials.security_token}
        if kind == 'naming':
            fields.update(build_naming_sign_fields(
                **keys, service_name=body.get('serviceName', ''), group_name=body.get('groupName', ''),
            ))
        else:
            fields.update(build_config_sign_fields(
                **keys, tenant=body.get('tenant', ''), group=body.get('group', ''),
            ))
        return fields


class AKSKAuthProvider(SignedAuthProvider):
    def __init__(self, access_key: str, secret_key: str):
        super().__init__()
        if not access_key or not access_key.strip() or not secret_key or not secret_key.strip():
            raise ValueError('aksk requires NACOS_ACCESS_KEY and NACOS_SECRET_KEY')
        self._credentials = StsCredentials(access_key, secret_key, None, math.inf)

    async def prepare_request(self):
        pass


class RamRoleAuthProvider(SignedAuthProvider):
    def __init__(self, role_name: str):
        super().__init__()
        if not role_name or not role_name.strip():
            raise ValueError('ram requires EXPORT_NACOS_RAM_ROLE_NAME')
        self.role_name = role_name
        self._lock = asyncio.Lock()

    async def prepare_request(self):
        if self._credentials is None or time.time() >= self._credentials.expiration - 900:
            await self._update_credentials()

    async def refresh(self, schedule_renewal: bool = True) -> bool:
        await self._update_credentials(force=True)
        return True

    async def _update_credentials(self, force: bool = False):
        # Double-check under the lock and replace the entire credential snapshot atomically.
        async with self._lock:
            if force or self._credentials is None or time.time() >= self._credentials.expiration - 900:
                self._credentials = await self._fetch_credentials()

    async def _fetch_credentials(self) -> StsCredentials:
        base = 'http://100.100.100.200'
        timeout = ClientTimeout(total=3, connect=1)
        headers = {'Host': '100.100.100.200'}
        token = None
        try:
            response = await AioHttpCli.put(
                base + '/latest/api/token', timeout=timeout, allow_redirects=False,
                headers={**headers, 'X-aliyun-ecs-metadata-token-ttl-seconds': '21600'},
            )
            try:
                if response.status == 200:
                    token = (await response.text()).strip()
            finally:
                response.release()
        except Exception:
            # Older ECS metadata endpoints support IMDSv1 only.
            pass
        if token:
            headers['X-aliyun-ecs-metadata-token'] = token
        try:
            response = await AioHttpCli.get(
                base + '/latest/meta-data/ram/security-credentials/' + quote(self.role_name, safe=''),
                headers=headers, timeout=timeout, allow_redirects=False,
            )
            try:
                if response.status != 200:
                    raise ValueError('Metadata request failed')
                # ECS metadata may return JSON with a non-JSON Content-Type.
                payload = await response.json(content_type=None)
            finally:
                response.release()
            if payload['Code'] != 'Success':
                raise ValueError('Metadata credentials rejected')
            access_key, secret_key, security_token = (
                payload['AccessKeyId'], payload['AccessKeySecret'], payload['SecurityToken'],
            )
            if not all(isinstance(value, str) and value.strip()
                       for value in (access_key, secret_key, security_token)):
                raise ValueError('Incomplete metadata credentials')
            expiration = datetime.strptime(payload['Expiration'], '%Y-%m-%dT%H:%M:%SZ')
            expiration = expiration.replace(tzinfo=timezone.utc).timestamp()
            if expiration <= time.time():
                raise ValueError('Expired metadata credentials')
            return StsCredentials(access_key, secret_key, security_token, expiration)
        except Exception:
            raise RuntimeError('Unable to fetch valid Nacos RAM credentials') from None


def create_auth_provider(
    mode: str, user: Optional[str] = None, password: Optional[str] = None,
    access_key: Optional[str] = None, secret_key: Optional[str] = None,
    ram_role_name: Optional[str] = None,
) -> AuthProvider:
    if mode == 'password':
        return PasswordAuthProvider(user, password)
    if mode == 'aksk':
        return AKSKAuthProvider(access_key, secret_key)
    if mode == 'ram':
        return RamRoleAuthProvider(ram_role_name)
    raise ValueError('Unsupported Nacos authentication mode')
