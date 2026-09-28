"""Read-only permission diagnostics for the saved Feishu application."""
from __future__ import annotations

from urllib.parse import quote

import aiohttp

NAME_SCOPE = "contact:user.base:readonly"
DOCS_URL = "https://open.feishu.cn/document/server-docs/contact-v3/user/get"
BASE_URL = "https://open.feishu.cn/open-apis"


async def check_user_details_permissions(account):
    result = {
        "status": "error", "message": "权限检测失败，请稍后重试。",
        "permissions": [],
        "console_url": f"https://open.feishu.cn/app/{quote(account.bot_id, safe='')}/auth",
        "docs_url": DOCS_URL,
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10, connect=3)) as client:
            async with client.post(f"{BASE_URL}/auth/v3/tenant_access_token/internal",
                                   json={"app_id": account.bot_id, "app_secret": account.secret},
                                   allow_redirects=False) as response:
                token = await response.json(content_type=None)
                if response.status != 200 or not isinstance(token, dict) or token.get("code") != 0 or not token.get("tenant_access_token"):
                    result["message"] = "无法验证应用凭据，请检查已保存的 App ID 和 App Secret。"
                    return result
            async with client.get(f"{BASE_URL}/application/v6/scopes",
                                  headers={"Authorization": f"Bearer {token['tenant_access_token']}"},
                                  allow_redirects=False) as response:
                payload = await response.json(content_type=None)
                if response.status != 200 or not isinstance(payload, dict) or payload.get("code") != 0:
                    result["message"] = "无法读取应用授权状态，请前往飞书后台核对；本次不能确定是否缺少姓名权限。"
                    return result
        scopes = (payload.get("data") or {}).get("scopes")
        if not isinstance(scopes, list):
            return result
        granted = {row.get("scope_name") for row in scopes if isinstance(row, dict)
                   and row.get("scope_type") == "tenant" and row.get("grant_status") == 1}
        authorized = NAME_SCOPE in granted
        result.update(status="authorized" if authorized else "missing",
                      permissions=[{"scope": NAME_SCOPE, "name": "获取用户基本信息", "authorized": authorized}],
                      message=("姓名读取权限已授权。可查询人员仍受通讯录数据范围限制，请确认配置已发布生效。"
                               if authorized else
                               "未检测到已授权的应用身份“获取用户基本信息”权限。请前往权限管理开通并发布，确认通讯录数据范围。"))
    except (aiohttp.ClientError, TimeoutError, ValueError, TypeError):
        result["message"] = "暂时无法完成权限检测，请检查网络后重试。"
    return result
