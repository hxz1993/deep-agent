from typing import Dict

from .discovery import ServiceDiscovery
from ..errors.classes import ServerNotFound


DNS_ENV_BY_SERVER: Dict[str, str] = {
    'WEB-GATEWAY-SERVER': 'EXPORT_DNS_WEB_GATEWAY',
    'DEEPFOS-PLATFORM': 'EXPORT_DNS_DEEPFOS_PLATFORM',
    'SEEPLN-ACCOUNT': 'EXPORT_DNS_ACCOUNT',
    'APP-SERVER': 'EXPORT_DNS_APP',
    'SPACE-SERVER': 'EXPORT_DNS_SPACE',
    'SYSTEM-SERVER': 'EXPORT_DNS_SYSTEM',
    'PLATFORM-FILE-SERVER': 'EXPORT_DNS_PLATFORM_FILE',
    'MENU-SERVER1-0': 'EXPORT_DNS_MENU',
    'MESSAGE-SERVER': 'EXPORT_DNS_MESSAGE',
    'OS-SPACE-TENANT': 'EXPORT_DNS_OS_SPACE_TENANT',
    'DEEPFOS-PLANNING1-0': 'EXPORT_DNS_DEEPFOS_PLANNING',
    'DIMENSION-SERVER1-2': 'EXPORT_DNS_DIMENSION_1_2',
    'FINANCIAL-MODEL-SERVER1-0': 'EXPORT_DNS_FINANCIAL_MODEL',
    'MEMORY-FINANCIAL-MODEL-SERVER1-0': 'EXPORT_DNS_MEMORY_FINANCIAL_MODEL',
    'DEEP-TABLE-SERVER2-0': 'EXPORT_DNS_DEEP_TABLE',
    'LIST-TABLE-SERVER1-2': 'EXPORT_DNS_LIST_TABLE_1_2',
    'SMART-LIST-SERVER1-0': 'EXPORT_DNS_SMART_LIST',
    'VARIABLE-SERVER1-0': 'EXPORT_DNS_VARIABLE',
    'ROLE-STRATEGY-SERVER1-0': 'EXPORT_DNS_ROLE_STRATEGY',
    'UX-SERVER1-0': 'EXPORT_DNS_UX',
    'APPROVAL-PROCESS-SERVER1-0': 'EXPORT_DNS_APPROVAL_PROCESS',
    'BUSINESS-FORM-SERVER1-0': 'EXPORT_DNS_BUSINESS_FORM_1_0',
    'BUSINESS-FORM-SERVER1-1': 'EXPORT_DNS_BUSINESS_FORM_1_1',
    'BUSINESS-MODEL-SERVER1-1': 'EXPORT_DNS_BUSINESS_MODEL_1_1',
    'CHART-SERVER2-0': 'EXPORT_DNS_CHART_2_0',
    'DASHBOARD-SERVER2-0': 'EXPORT_DNS_DASHBOARD_2_0',
    'DEEP-MODEL-SERVER': 'EXPORT_DNS_DEEP_MODEL',
    'DOMAIN-MODEL-SERVER3-0': 'EXPORT_DNS_DOMAIN_MODEL',
    'DEEP-CONNECTOR-SERVER': 'EXPORT_DNS_DEEP_CONNECTOR',
    'DATA-TABLE-CLICKHOUSE-SERVER1-0': 'EXPORT_DNS_DATA_TABLE_CLICKHOUSE',
    'DATA-TABLE-DAMENG-SERVER1-0': 'EXPORT_DNS_DATA_TABLE_DAMENG',
    'DATA-TABLE-DEEPENGINE-SERVER1-0': 'EXPORT_DNS_DATA_TABLE_DEEPENGINE',
    'DATA-TABLE-DEEPMODEL-SERVER1-0': 'EXPORT_DNS_DATA_TABLE_DEEPMODEL',
    'DATA-TABLE-KINGBASE-SERVER1-0': 'EXPORT_DNS_DATA_TABLE_KINGBASE',
    'DATA-TABLE-MYSQL-SERVER1-0': 'EXPORT_DNS_DATA_TABLE_MYSQL',
    'BUSINESS-FLOW-SERVER2-0': 'EXPORT_DNS_BUSINESS_FLOW',
    'DEEPFLOW-SERVER3-0': 'EXPORT_DNS_DEEPFLOW',
    'DEEP-PIPELINE-SERVER3-0': 'EXPORT_DNS_DEEP_PIPELINE',
    'ADAPTER-ENGINE-SERVER3-0': 'EXPORT_DNS_ADAPTER_ENGINE',
    'PYTHON-SERVER2-0': 'EXPORT_DNS_PYTHON',
    'DEEP-AGENT-SERVER1-0': 'EXPORT_DNS_DEEP_AGENT',
    'DEEPFOS-MCP': 'EXPORT_DNS_DEEPFOS_MCP',
    'CONSOLIDATION-PROCESS-SERVER1-0': 'EXPORT_DNS_CONSOLIDATION_PROCESS',
    'GROUP-REPORT-SERVER1-0': 'EXPORT_DNS_GROUP_REPORT',
    'JOURNAL-SERVER1-0': 'EXPORT_DNS_JOURNAL',
    'CONSOLIDATION-SERVER1-0': 'EXPORT_DNS_CONSOLIDATION_1_0',
    'JOURNAL-TEMPLATE-SERVER1-0': 'EXPORT_DNS_JOURNAL_TEMPLATE',
    'RECONCILIATION-REPORT-SERVER1-0': 'EXPORT_DNS_RECONCILIATION_REPORT',
    'RECONCILIATION-SERVER1-0': 'EXPORT_DNS_RECONCILIATION',
    'WORKING-PAPER-SERVER1-0': 'EXPORT_DNS_WORKING_PAPER',
}


class DNSConfigurationError(RuntimeError):
    pass


def get_dns_url(server_name: str, environ=None) -> str:
    if environ is None:
        import os
        environ = os.environ

    try:
        env_name = DNS_ENV_BY_SERVER[server_name.upper()]
    except KeyError:
        raise ServerNotFound(server_name) from None

    value = environ.get(env_name)
    if value is None or not value.strip():
        raise DNSConfigurationError(
            f"DNS environment variable '{env_name}' for service "
            f"'{server_name}' is missing or empty."
        )
    return value


def get_dns_records(environ=None) -> Dict[str, str]:
    existing = {}
    for server_name in DNS_ENV_BY_SERVER:
        try:
            existing[server_name] = get_dns_url(server_name, environ)
        except DNSConfigurationError:
            pass
    return existing


def _noop():
    pass


class DNS(ServiceDiscovery):
    do_refresh = False
    has_instance_cache = False

    async def on_interval(self):
        pass

    async def on_startup(self):
        pass

    async def update_service_cache(self, server_name: str):
        pass

    async def update_instance_cache(self, server_name: str):
        pass

    async def get_url(self, server_name: str):
        return _noop, _noop, get_dns_url(server_name)
