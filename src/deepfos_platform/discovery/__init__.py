from .discovery import ServiceDiscovery
from .eureka import Eureka
from .local import Local
from .nacos import Nacos
from .k8s import K8s

__all__ = ["ServiceDiscovery", "Eureka", "Local", "Nacos", "K8s", "poll_event"]

from .discovery import poll_event, CACHE_STRATEGY
