"""DeepAgent authentication and authorization helpers."""

from deep_agent.local_security import LOCAL_CURRENT_USERNAME_SCOPE_KEY

from .console_admin import (
    ConsoleAdminAccess,
    ConsoleAdminAuthenticationError,
    ConsoleAdminAuthorizer,
    ConsoleAdminUnavailableError,
    ENTERPRISE_ADMIN_ROLE,
    LOCAL_CONSOLE_ADMIN_SCOPE_KEY,
    PlatformEnterpriseAdminChecker,
)
from .current_user import (
    CurrentUserAuthenticationError,
    CurrentUserProfile,
    CurrentUserProfileResolver,
    CurrentUserUnavailableError,
    PlatformCurrentUserFetcher,
)

__all__ = [
    "ConsoleAdminAccess",
    "ConsoleAdminAuthenticationError",
    "ConsoleAdminAuthorizer",
    "ConsoleAdminUnavailableError",
    "ENTERPRISE_ADMIN_ROLE",
    "LOCAL_CONSOLE_ADMIN_SCOPE_KEY",
    "PlatformEnterpriseAdminChecker",
    "CurrentUserAuthenticationError",
    "CurrentUserProfile",
    "CurrentUserProfileResolver",
    "CurrentUserUnavailableError",
    "LOCAL_CURRENT_USERNAME_SCOPE_KEY",
    "PlatformCurrentUserFetcher",
]
