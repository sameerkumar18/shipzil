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
    Exclusion,
    ExclusionCode,
    Item,
    Label,
    Parcel,
    Quote,
    Rate,
    Shipment,
    Strategy,
)
from .units import Dimensions, Weight

__version__ = "0.1.0"

__all__ = [
    "Address",
    "AmbiguousPurchaseError",
    "AuthenticationError",
    "CapabilityError",
    "Client",
    "ConfigurationError",
    "Dimensions",
    "Exclusion",
    "ExclusionCode",
    "Item",
    "Label",
    "LabelPurchaseError",
    "Parcel",
    "ProviderError",
    "Quote",
    "Rate",
    "RateLimitError",
    "Shipment",
    "ShipzilError",
    "SpendLimitExceeded",
    "Strategy",
    "ValidationError",
    "Weight",
    "__version__",
]
