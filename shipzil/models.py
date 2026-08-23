"""The shipzil data model.

Shipment-focused, always multi-parcel. A caller describes what they want to
ship; which provider resource satisfies that is the adapter's problem, exposed
only through `Quote.via` for debugging.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from .units import Dimensions, Weight

__all__ = [
    "Address",
    "Exclusion",
    "ExclusionCode",
    "Item",
    "Label",
    "Parcel",
    "Quote",
    "Rate",
    "Shipment",
    "Strategy",
]


@dataclass(frozen=True)
class Address:
    """A postal address. `country` is ISO 3166-1 alpha-2."""

    street1: str
    city: str
    postal_code: str
    country: str = "US"
    state: str | None = None
    street2: str | None = None
    name: str | None = None
    company: str | None = None
    phone: str | None = None
    email: str | None = None
    residential: bool | None = None

    def __post_init__(self) -> None:
        if len(self.country) != 2:
            raise ValueError(f"country must be ISO alpha-2, got {self.country!r}")


@dataclass(frozen=True)
class Item:
    """Contents of a parcel.

    Optional for domestic shipping on most providers, but **Easyship requires
    either `category` or `hs_code` on every item even for a domestic US
    shipment** — see docs/API-REALITY.md.

    `dimensions` is per item rather than per box, which is how Easyship derives a
    box when none is given: it rejects item-only parcels whose items carry no
    dimensions (`parcels[0].items[0].dimensions can't be blank`). No other
    provider reads this field.
    """

    description: str
    quantity: int = 1
    weight: Weight | None = None
    dimensions: Dimensions | None = None
    value: Decimal | None = None
    currency: str = "USD"
    sku: str | None = None
    hs_code: str | None = None
    category: str | None = None
    origin_country: str | None = None

    def __post_init__(self) -> None:
        if self.quantity < 1:
            raise ValueError(f"quantity must be >= 1, got {self.quantity}")


@dataclass(frozen=True)
class Parcel:
    """One physical package.

    Two ways to describe it:

    * **box-centric** — `weight` plus optional `dimensions`. What EasyPost,
      Shippo and ShipStation expect.
    * **item-centric** — `items` only, letting the provider pack and weigh.
      Only Easyship can do this; adapters that cannot will say so rather than
      inventing a bounding box.
    """

    weight: Weight | None = None
    dimensions: Dimensions | None = None
    items: tuple[Item, ...] = ()
    reference: str | None = None

    def __post_init__(self) -> None:
        if self.weight is None and not self.items:
            raise ValueError(
                "a parcel needs either a weight or items to derive one from; got neither"
            )

    @property
    def is_item_centric(self) -> bool:
        """True when the provider must derive weight/dimensions from items."""
        return self.weight is None

    @property
    def derived_weight(self) -> Weight | None:
        """Total item weight, when every item carries one. Never a guess."""
        if self.weight is not None:
            return self.weight
        if not self.items or any(i.weight is None for i in self.items):
            return None
        total = None
        for item in self.items:
            assert item.weight is not None
            for _ in range(item.quantity):
                total = item.weight if total is None else total + item.weight
        return total


@dataclass(frozen=True)
class Shipment:
    """What the caller wants to ship. `parcels` is always a list, even at one."""

    from_address: Address
    to_address: Address
    parcels: tuple[Parcel, ...]
    reference: str | None = None

    def __post_init__(self) -> None:
        if not self.parcels:
            raise ValueError("a shipment needs at least one parcel")

    @property
    def is_multi_parcel(self) -> bool:
        return len(self.parcels) > 1

    @property
    def is_international(self) -> bool:
        return self.from_address.country != self.to_address.country


class Strategy(str, Enum):
    """How a quote was produced. Surfaced for debugging, not for decisions."""

    NATIVE = "native"
    """Provider rated the whole shipment in one call."""

    ORDER = "order"
    """Provider has a distinct multi-parcel resource (EasyPost /orders)."""

    FANOUT = "fanout"
    """shipzil rated each parcel separately and combined the results.

    Used where the provider cannot rate multiple parcels at all — four of six
    surfaces. The amounts are a **sum of per-parcel quotes**, which is not
    always what a carrier would charge for one consignment.
    """


class ExclusionCode(str, Enum):
    """Why something could not be rated.

    The vocabulary is borrowed from ShipStation v2, which is the only provider
    that reports this in a structured, per-carrier way. Everyone else's prose is
    normalised onto it.
    """

    MULTIPACKAGE_NOT_SUPPORTED = "multipackage_not_supported"
    SERVICE_UNAVAILABLE = "service_unavailable"
    CARRIER_ACCOUNT_MISCONFIGURED = "carrier_account_misconfigured"
    DIMENSIONS_REQUIRED = "dimensions_required"
    ITEM_CLASSIFICATION_REQUIRED = "item_classification_required"
    ADDRESS_UNSUPPORTED = "address_unsupported"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Exclusion:
    """Something that could not be rated, and why.

    The point of the library: an empty rate list is never returned without
    reasons attached. `source` records whether the provider told us in
    structured form, or whether we inferred the code from prose.
    """

    code: ExclusionCode
    message: str
    carrier: str | None = None
    service: str | None = None
    source: Literal["provider", "shipzil"] = "provider"


@dataclass(frozen=True)
class Rate:
    """A price for carrying a shipment.

    `currency` and `delivery_days` are optional because **ShipStation v1 returns
    neither** — it gives four fields total. Code that requires them must degrade
    rather than crash.
    """

    carrier: str
    service: str
    amount: Decimal
    currency: str | None = None
    delivery_days: int | None = None
    guaranteed: bool | None = None
    provider: str = ""
    service_code: str | None = None
    strategy: Strategy = Strategy.NATIVE
    parcel_count: int = 1
    raw: Any = None

    @property
    def is_synthesized(self) -> bool:
        """True when this amount is a sum shipzil computed, not a provider quote."""
        return self.strategy is Strategy.FANOUT


@dataclass(frozen=True)
class Quote:
    """The result of asking for rates: what worked, and what didn't."""

    rates: tuple[Rate, ...] = ()
    excluded: tuple[Exclusion, ...] = ()
    via: str = ""
    strategy: Strategy = Strategy.NATIVE
    messages: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.rates)

    @property
    def cheapest(self) -> Rate | None:
        return min(self.rates, key=lambda r: r.amount) if self.rates else None

    @property
    def fastest(self) -> Rate | None:
        timed = [r for r in self.rates if r.delivery_days is not None]
        return min(timed, key=lambda r: r.delivery_days or 0) if timed else None

    def explain(self) -> str:
        """Human-readable summary — the answer to 'why did I get nothing?'."""
        lines = [f"{len(self.rates)} rate(s) via {self.via or 'unknown'} ({self.strategy.value})"]
        for exc in self.excluded:
            who = exc.carrier or "provider"
            tag = "" if exc.source == "provider" else " [inferred]"
            lines.append(f"  excluded {who}: {exc.code.value}{tag} — {exc.message}")
        return "\n".join(lines)


@dataclass(frozen=True)
class Label:
    """A purchased label.

    `is_test` exists because a test label and a real one are otherwise
    indistinguishable without inspecting `raw`, and mistaking one for the other
    means either a parcel that never ships or a charge nobody expected. It is
    deliberately three-state: True means definitely not a real purchase, False
    means definitely real, and **None means shipzil cannot tell** — which is the
    case for any provider whose credentials carry no test marker.
    """

    tracking_number: str
    label_url: str
    carrier: str
    service: str
    amount: Decimal
    currency: str | None = None
    provider: str = ""
    shipment_id: str = ""
    #: True = test label, False = real purchase, None = undeterminable.
    is_test: bool | None = None
    raw: Any = None
