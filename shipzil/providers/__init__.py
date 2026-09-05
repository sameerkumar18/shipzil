"""Provider adapters — the surface for writing or configuring one.

Callers use `shipzil.Gateway`. This module is for adapter authors, so it also
re-exports `Quote`, the value an adapter returns from `rate_single`.

Most callers never import from here. `Gateway(shippo="token")` looks the adapter
up in `REGISTRY` by name, so a credential is all you need. Import an adapter
directly when you want to configure it — a custom timeout, a second account on the
same provider, or a different transport.
"""

from collections.abc import Callable

from ..models import Quote
from .base import Adapter, Capabilities
from .easyship import EasyshipAdapter
from .shippo import ShippoAdapter
from .shipstation_v1 import ShipStationV1Adapter
from .shipstation_v2 import ShipStationV2Adapter

#: Provider name → adapter factory, for `Gateway(**credentials)`. The name is also
#: the default source name, so `Gateway(shippo=...)` yields a source called "shippo".
#: Typed as a callable because adapters take different numbers of secrets:
#: ShipStation v1 needs a key and a secret, the others take one credential.
REGISTRY: dict[str, Callable[..., Adapter]] = {
    "shippo": ShippoAdapter,
    "easyship": EasyshipAdapter,
    "shipstation_v1": ShipStationV1Adapter,
    "shipstation_v2": ShipStationV2Adapter,
}

__all__ = [
    "REGISTRY",
    "Adapter",
    "Capabilities",
    "EasyshipAdapter",
    "Quote",
    "ShipStationV1Adapter",
    "ShipStationV2Adapter",
    "ShippoAdapter",
]
