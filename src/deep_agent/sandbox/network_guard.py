"""Best-effort public-network guard used by the subprocess development worker."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any

_installed = False
_original_getaddrinfo = socket.getaddrinfo
_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex


def _validate_ip(value: str) -> None:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError as exc:
        raise OSError("sandbox network target did not resolve to an IP address") from exc
    if not address.is_global:
        raise PermissionError(f"sandbox blocks non-public network target: {address}")


def _guarded_getaddrinfo(
    host: str | bytes | None,
    port: str | int | None,
    family: int = 0,
    type: int = 0,
    proto: int = 0,
    flags: int = 0,
) -> list[tuple[Any, ...]]:
    if host is None:
        raise PermissionError("sandbox network target is required")
    results = _original_getaddrinfo(host, port, family, type, proto, flags)
    for item in results:
        sockaddr = item[4]
        if isinstance(sockaddr, tuple) and sockaddr:
            _validate_ip(str(sockaddr[0]))
    return results


def _guarded_connect(sock: socket.socket, address: Any) -> Any:
    _validate_address(sock, address)
    return _original_connect(sock, address)


def _validate_address(sock: socket.socket, address: Any) -> None:
    if sock.family in {socket.AF_INET, socket.AF_INET6}:
        if not isinstance(address, tuple) or not address:
            raise PermissionError("sandbox network target is invalid")
        host = str(address[0])
        try:
            _validate_ip(host)
        except OSError:
            # Direct socket.connect may receive a hostname. Resolve and validate
            # every result before allowing the original connection.
            _guarded_getaddrinfo(host, address[1], sock.family, sock.type, sock.proto)


def _guarded_connect_ex(sock: socket.socket, address: Any) -> int:
    try:
        _validate_address(sock, address)
    except OSError as exc:
        return int(getattr(exc, "errno", None) or 13)
    return _original_connect_ex(sock, address)


def install_public_network_guard() -> None:
    """Permit public destinations while blocking local/private address space.

    This Python-level guard is intentionally reported as best-effort. It covers
    the normal ``requests``, ``httpx`` and ``urllib`` stacks, including each
    redirect connection, but is not a hostile-code isolation boundary.
    """
    global _installed
    if _installed:
        return
    socket.getaddrinfo = _guarded_getaddrinfo
    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
    _installed = True
