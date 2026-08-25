"""shipzil — one honest shipping interface.

Multi-parcel everywhere, including the four provider surfaces out of six that
cannot do it themselves. When a number is synthesized rather than quoted, or a
carrier is excluded rather than simply absent, the library says so.
"""

from .client import Client
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
    Quote,
    Rate,
    RegulationLevel,
    ServiceId,
    Shipment,
    Strategy,
    TrackingLeg,
)
from .units import Dimensions, Weight

__version__ = "0.1.0"

__all__ = [
    "Address",
    "AddressClass",
    "AmbiguousPurchaseError",
    "AuthenticationError",
    "CapabilityError",
    "Client",
    "ConfigurationError",
    "DangerousGoods",
    "Dimensions",
    "DryIce",
    "DutiesPaidBy",
    "Exclusion",
    "ExclusionCode",
    "Item",
    "Label",
    "LabelPurchaseError",
    "LithiumBatteryPacking",
    "PackagingTemplate",
    "Parcel",
    "ProviderError",
    "Quote",
    "Rate",
    "RateLimitError",
    "RegulationLevel",
    "ServiceId",
    "Shipment",
    "ShipzilError",
    "SpendLimitExceeded",
    "Strategy",
    "TrackingLeg",
    "ValidationError",
    "Weight",
    "__version__",
]
