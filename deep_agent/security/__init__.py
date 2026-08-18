"""DeepAgent authentication and authorization helpers."""

from .console_admin import (
    ConsoleAdminAccess,
    ConsoleAdminAuthenticationError,
    ConsoleAdminAuthorizer,
    ConsoleAdminUnavailableError,
    ENTERPRISE_ADMIN_ROLE,
    PlatformEnterpriseAdminChecker,
)

__all__ = [
    "ConsoleAdminAccess",
    "ConsoleAdminAuthenticationError",
    "ConsoleAdminAuthorizer",
    "ConsoleAdminUnavailableError",
    "ENTERPRISE_ADMIN_ROLE",
    "PlatformEnterpriseAdminChecker",
]
