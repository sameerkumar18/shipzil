"""MIT-licensed multi-provider shipping library for Python.

Multi-parcel everywhere, including the three provider surfaces of four that
cannot do it themselves. When a number is synthesized rather than quoted, or a
carrier is excluded rather than simply absent, the library says so.
"""

from .errors import (
    AmbiguousPurchaseError,
    AuthenticationError,
    CapabilityError,
    ConfigurationError,
    LabelPurchaseError,
    ProviderError,
    RateLimitError,
    ShipzilError,
    SpendLimitExceeded,
    ValidationError,
)
from .gateway import Gateway, GatewayQuote, SourceResult
from .models import (
    Address,
    AddressClass,
    DangerousGoods,
    DryIce,
    DutiesPaidBy,
    Exclusion,
    ExclusionCode,
    Item,
    Label,
    LithiumBatteryPacking,
    PackagingTemplate,
    Parcel,
    Rate,
    RegulationLevel,
    ServiceKey,
    Shipment,
    Strategy,
    TrackingLeg,
)
from .services import ServiceMap
from .units import Dimensions, Weight

__version__ = "0.1.0"

__all__ = [
    "Address",
    "AddressClass",
    "AmbiguousPurchaseError",
    "AuthenticationError",
    "CapabilityError",
    "ConfigurationError",
    "DangerousGoods",
    "Dimensions",
    "DryIce",
    "DutiesPaidBy",
    "Exclusion",
    "ExclusionCode",
    "Gateway",
    "GatewayQuote",
    "Item",
    "Label",
    "LabelPurchaseError",
    "LithiumBatteryPacking",
    "PackagingTemplate",
    "Parcel",
    "ProviderError",
    "Rate",
    "RateLimitError",
    "RegulationLevel",
    "ServiceKey",
    "ServiceMap",
    "Shipment",
    "ShipzilError",
    "SourceResult",
    "SpendLimitExceeded",
    "Strategy",
    "TrackingLeg",
    "ValidationError",
    "Weight",
    "__version__",
]
