"""Shared platform convention for service names written as HTTP URLs."""

import re


# Preserve BaseAPI's existing, deliberately narrow service-name convention.
# Real hosts, explicit ports and gateway paths must not become registry names.
_SERVICE_URL = re.compile(r"https?://([A-Za-z-]+server)", re.IGNORECASE)


def normalize_service_target(target: str) -> str:
    """Turn e.g. ``http://system-server`` into a discovery name.

    This is configuration normalization, not a fallback after a DNS failure.
    Transport and instance address are supplied by the configured discovery.
    """
    target = target.strip()
    match = _SERVICE_URL.fullmatch(target)
    if match:
        return match.group(1)
    from ..options import OPTION
    if OPTION.srvdis.implementation == 'dns':
        from .dns import DNS_ENV_BY_SERVER
        match = re.fullmatch(r'https?://([A-Za-z0-9-]+)', target, re.IGNORECASE)
        if match and match.group(1).upper() in DNS_ENV_BY_SERVER:
            return match.group(1)
    return target
