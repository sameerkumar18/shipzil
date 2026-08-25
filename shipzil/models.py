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

from .service_id import ServiceId
from .units import Dimensions, Weight

__all__ = [
    "Address",
    "AddressClass",
    "CustomsLine",
    "DangerousGoods",
    "DryIce",
    "DutiesPaidBy",
    "Exclusion",
    "ExclusionCode",
    "Item",
    "Label",
    "LithiumBatteryPacking",
    "PackagingTemplate",
    "Parcel",
    "Quote",
    "Rate",
    "RegulationLevel",
    "ServiceId",
    "Shipment",
    "Strategy",
    "TrackingLeg",
]


class AddressClass(str, Enum):
    """What kind of place an address is.

    Not a boolean, deliberately. Shippo's v2 address model replaced its boolean
    `is_residential` with an enum precisely because PO boxes and military
    addresses are neither residential nor commercial, and a boolean silently
    mislabels them.

    `UNKNOWN` is the default and is **not** the same as `COMMERCIAL`. Sending
    "commercial" on a caller's silence is a claim shipzil has no basis for, and
    it is worth roughly $6 per parcel: UPS charges a residential surcharge of
    $6.60 in the US, and Easyship reports `residential_full_fee` around 6.15.
    """

    UNKNOWN = "unknown"
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    PO_BOX = "po_box"
    MILITARY = "military"


class DutiesPaidBy(str, Enum):
    """Who settles import duty and tax. An incoterm, narrowed to the choice.

    shipzil used to hardcode DDU, which silently made the *recipient* liable for
    duty on every international shipment. That is a commercial decision and it
    belongs to the caller.

    `UNSPECIFIED` sends nothing and lets the provider apply its own default,
    which is the only honest behaviour when the caller has not said.
    """

    UNSPECIFIED = "unspecified"
    #: DDU / DAP — recipient pays duty and tax on arrival.
    RECIPIENT = "recipient"
    #: DDP — shipper pays duty and tax, so the buyer sees a landed cost.
    SENDER = "sender"


class LithiumBatteryPacking(str, Enum):
    """IATA packing instruction for lithium cells. Not interchangeable.

    PI966 and PI967 carry different labelling and documentation duties, so a
    single "contains batteries" boolean is not a lawful substitute. Easyship is
    the only provider that models the distinction, per item.
    """

    NONE = "none"
    #: PI966 — batteries packed *with* equipment, shipped alongside it.
    PACKED_WITH_EQUIPMENT = "pi966"
    #: PI967 — batteries *contained in* equipment.
    CONTAINED_IN_EQUIPMENT = "pi967"


class RegulationLevel(str, Enum):
    """How heavily regulated a dangerous good is. ShipEngine's vocabulary."""

    LIMITED_QUANTITIES = "limited_quantities"
    EXCEPTED_QUANTITY = "excepted_quantity"
    LIGHTLY_REGULATED = "lightly_regulated"
    FULLY_REGULATED = "fully_regulated"


@dataclass(frozen=True)
class DryIce:
    """Dry ice, which is itself a dangerous good (UN1845).

    `weight` is required whenever dry ice is present: Shippo marks it mandatory
    and rejects a weight greater than the parcel weight. Shippo accepts
    kilograms only while ShipEngine accepts four units, so shipzil holds a
    `Weight` and converts at each adapter boundary.
    """

    weight: Weight
    contains: bool = True


@dataclass(frozen=True)
class DangerousGoods:
    """A hazmat declaration.

    Providers disagree about where this belongs — ShipEngine puts a full
    IATA-style declaration on each product, Shippo puts booleans on the
    shipment, Easyship puts battery flags on each item — so shipzil accepts it
    per `Parcel` and each adapter maps it to the level its provider expects,
    reporting an exclusion when the provider cannot carry the detail.

    The regulated fields are optional because the aggregators demand different
    subsets, but **omitting them does not make a shipment compliant**. A carrier
    that accepts an undeclared hazmat parcel leaves the liability with the
    shipper.
    """

    #: Present at all. Set this even when nothing else is known.
    contains: bool = True
    lithium_batteries: LithiumBatteryPacking = LithiumBatteryPacking.NONE
    biological_material: bool = False
    contains_liquids: bool = False
    #: FedEx and UPS only, and FedEx additionally requires `alcohol_recipient`.
    contains_alcohol: bool = False
    #: "licensee" or "consumer". Mandatory for FedEx when alcohol is present.
    alcohol_recipient: Literal["licensee", "consumer"] | None = None
    dry_ice: DryIce | None = None

    # ── the fully regulated fields (ShipEngine per-product) ──────────
    #: UN number, e.g. "UN3481".
    un_number: str | None = None
    #: Proper shipping name, e.g. "Lithium ion batteries packed with equipment".
    shipping_name: str | None = None
    technical_name: str | None = None
    #: Hazard class, e.g. "9".
    hazard_class: str | None = None
    #: Packing group I, II or III.
    packing_group: Literal["i", "ii", "iii"] | None = None
    #: e.g. "PI966".
    packing_instruction: str | None = None
    regulation_level: RegulationLevel | None = None
    #: IATA, DOT, ADR…
    regulation_authority: str | None = None
    #: ground | water | cargo_aircraft_only | passenger_aircraft
    transport_mode: str | None = None
    radioactive: bool = False
    reportable_quantity: bool = False
    #: Required by several carriers whenever hazmat is present.
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None

    @property
    def is_fully_declared(self) -> bool:
        """Whether the regulated fields a fully-regulated shipment needs are set.

        Deliberately not enforced: plenty of hazmat ships under limited or
        excepted quantity with far less paperwork. It exists so an adapter can
        warn instead of guessing.
        """
        return bool(self.un_number and self.hazard_class and self.packing_group)


@dataclass(frozen=True)
class PackagingTemplate:
    """Carrier-supplied packaging, such as a USPS Flat Rate box.

    A template *replaces* dimensions rather than supplementing them. Shippo
    enforces this in its schema with two mutually exclusive request bodies: with
    a template, the dimension fields must be empty. So a `Parcel` carrying a
    template and no dimensions is correct, not incomplete, and adapters must not
    demand dimensions for it.

    `code` is the provider's own token and is not portable between providers:

    * Shippo    `USPS_FlatRateEnvelope`, `USPS_MediumFlatRateBox1`, …
    * ShipEngine `flat_rate_envelope`, `medium_flat_rate_box`, …
    * Easyship   a box `slug`

    Weight is still required: flat rate has a ceiling, 70 lb on USPS templates.
    """

    code: str
    #: Which provider's vocabulary `code` belongs to, so a mismatch can be
    #: reported rather than silently sent to the wrong API.
    provider: str | None = None


@dataclass(frozen=True)
class TrackingLeg:
    """One tracking number. A shipment can have several, for four reasons.

    1. **Legs.** An international shipment handed from one courier to another
       starts a new leg with its own number (Easyship `trackings[].leg_number`).
    2. **Pieces.** A multi-piece shipment has a master number plus one per piece
       (ShipEngine `packages[].tracking_number`; UPS allows up to 20).
    3. **Network handoff.** Ground Saver and similar keep one number across two
       networks.
    4. **Aliases.** DHL eCommerce adds `local` and `alternate` numbers for the
       same package.

    Collapsing these to a single string, which shipzil did, loses every leg after
    the first.
    """

    tracking_number: str
    #: 1-based. Increments when the shipment passes to a new courier.
    leg_number: int = 1
    #: The carrier actually moving the parcel on this leg.
    handler: str | None = None
    #: Position within a multi-piece shipment, when applicable.
    piece: int | None = None
    #: Carrier-internal aliases for the same movement.
    local_tracking_number: str | None = None
    alternate_tracking_number: str | None = None


@dataclass(frozen=True)
class CustomsLine:
    """One line of a customs declaration, carrying **both** bases.

    Providers disagree about whether a customs line's value and weight mean the
    per-unit figure or the line total, and the disagreement is not guessable —
    it splits two against two among the four providers whose documentation says
    either way. So this holds both and each adapter takes the one its provider
    documents, declared as `Adapter.customs_value_basis`.

    | Provider | Basis | Documented as |
    |---|---|---|
    | Shippo | line total | "Total value of this item, i.e. quantity * value per item" |
    | ShipStation v1 | line total | "The value (in USD) of the line item" |
    | ShipEngine / v2 | **per unit** | "The declared value of *each* item" |
    | Easyship | **per unit** | "this value refers to the unit rather than the total" |

    Getting it backwards is not a formatting error. Sending a line total where a
    unit is expected multiplies the declared customs value by the quantity, which
    inflates duty and misstates the shipment to the destination authority.
    """

    description: str
    quantity: int
    #: Total for the line: unit value x quantity.
    line_value: Decimal
    #: Total for the line: unit weight x quantity.
    line_weight: Weight
    #: The per-unit figures, for providers documented as wanting them.
    unit_value: Decimal
    unit_weight: Weight
    currency: str = "USD"
    hs_code: str | None = None
    origin_country: str = "US"
    sku: str | None = None


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
    #: ShipStation v1 accepts `street3` and Easyship accepts `line_3`.
    street3: str | None = None
    #: What kind of place this is. Drives residential surcharges, which are
    #: real money — roughly $6/parcel on UPS. Defaults to UNKNOWN, which is sent
    #: as "unknown" where the provider has that value and omitted otherwise.
    #: It is never silently downgraded to "commercial".
    address_class: AddressClass = AddressClass.UNKNOWN

    @property
    def residential(self) -> bool | None:
        """Tri-state view of `address_class`, for providers that take a boolean.

        None means unknown, and adapters must omit the field rather than send
        False. Sending False asserts "commercial" on no evidence.
        """
        if self.address_class is AddressClass.RESIDENTIAL:
            return True
        if self.address_class is AddressClass.COMMERCIAL:
            return False
        return None

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
    #: **Per unit**, not the line total. Whether a provider wants this figure or
    #: `line_weight` is provider-specific and documented per adapter as
    #: `customs_value_basis`; see `CustomsLine`. Shippo is explicit that its
    #: `net_weight` is "quantity * weight per item", ShipEngine is equally
    #: explicit that its `products[].value` is "the declared value of *each*
    #: item", and those are opposites.
    weight: Weight | None = None
    dimensions: Dimensions | None = None
    #: **Per unit**, not the line total. See the note on `weight`.
    value: Decimal | None = None
    currency: str = "USD"
    sku: str | None = None
    hs_code: str | None = None
    category: str | None = None
    origin_country: str | None = None

    @property
    def line_value(self) -> Decimal | None:
        """Total declared value for this line: value x quantity."""
        return None if self.value is None else self.value * self.quantity

    @property
    def line_weight(self) -> Weight | None:
        """Total weight for this line: weight x quantity."""
        if self.weight is None:
            return None
        return Weight(value=self.weight.value * self.quantity, unit=self.weight.unit)

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
    #: Carrier-supplied packaging. Replaces `dimensions`; see PackagingTemplate.
    packaging: PackagingTemplate | None = None
    #: Hazmat declaration for this parcel's contents.
    dangerous_goods: DangerousGoods | None = None
    #: Value to insure. Distinct from `Item.value`, which is the customs declared
    #: value, and from any COD amount. Collapsing the three misprices claims.
    insured_value: Decimal | None = None

    @property
    def has_dimensions(self) -> bool:
        """Whether the parcel's size is determined.

        True when dimensions are given **or** a carrier template supplies them.
        Adapters must use this rather than checking `dimensions` directly, or
        they will refuse flat-rate shipments the provider would happily quote.
        """
        return self.dimensions is not None or self.packaging is not None

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
    #: Who pays import duty and tax. UNSPECIFIED sends nothing, letting the
    #: provider default. shipzil does not choose a liability model for you.
    duties_paid_by: DutiesPaidBy = DutiesPaidBy.UNSPECIFIED
    #: Future-date the label. Manifests are keyed to ship date on ShipEngine.
    ship_date: str | None = None
    #: EEI / PFC exemption or citation for a US export, e.g. "NOEEI_30_37_a"
    #: or "AES_ITN". Left None, shipzil derives the under-$2,500 exemption from
    #: the declared value, and refuses rather than guessing above it — see
    #: `derived_eei_exemption`.
    eei_exemption: str | None = None

    @property
    def declared_value(self) -> Decimal:
        """Total customs value across every item in every parcel."""
        return sum(
            ((i.value or Decimal(0)) * i.quantity for p in self.parcels for i in p.items),
            Decimal(0),
        )

    @property
    def derived_eei_exemption(self) -> str | None:
        """The exemption shipzil is willing to assert, or None.

        `NOEEI_30_37_a` is the Foreign Trade Regulations exemption for shipments
        valued at $2,500 or less per Schedule B number. shipzil already knows the
        declared value, so applying it below the threshold is a derivation from
        the caller's own data rather than an invention.

        Above the threshold it returns None: that case genuinely needs an AES
        filing and an ITN, which shipzil cannot produce. An explicit
        `eei_exemption` always wins.
        """
        if self.eei_exemption:
            return self.eei_exemption
        if not self.parcels or not any(p.items for p in self.parcels):
            return None
        return "NOEEI_30_37_a" if self.declared_value <= Decimal(2500) else None

    @property
    def dangerous_goods(self) -> tuple[DangerousGoods, ...]:
        """Every hazmat declaration across the parcels."""
        return tuple(p.dangerous_goods for p in self.parcels if p.dangerous_goods)

    @property
    def is_hazmat(self) -> bool:
        return bool(self.dangerous_goods)

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
    CUSTOMS_DECLARATION_REQUIRED = "customs_declaration_required"
    DUTIES_UNSUPPORTED = "duties_unsupported"
    HAZMAT_DETAIL_UNSUPPORTED = "hazmat_detail_unsupported"
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
    #: Base carriage before surcharges, when the provider separates it. Easyship
    #: returns 25 cost components and shipzil previously kept only the total, so
    #: a 13% gap between carriage and total was invisible.
    base_amount: Decimal | None = None
    #: Named surcharge components in the provider's own spelling, e.g.
    #: ("fuel_surcharge", 3.03). Their sum need not equal amount - base_amount;
    #: no provider guarantees that, so shipzil does not assert it.
    surcharges: tuple[tuple[str, Decimal], ...] = ()
    #: Stable gateway address, `{provider}-{carrier}-{service}`. `None` when the
    #: provider gave nothing identifiable to address. See `shipzil.service_id`.
    service_id: ServiceId | None = None
    raw: Any = None

    @property
    def surcharge_total(self) -> Decimal:
        return sum((v for _, v in self.surcharges), Decimal(0))

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
    #: Every tracking number this purchase produced. `tracking_number` is the
    #: first leg; this is the whole set, including later legs after a courier
    #: handoff and per-piece numbers in a multi-piece shipment.
    tracking_legs: tuple[TrackingLeg, ...] = ()
    #: One entry per parcel when a single purchase produced several labels.
    #: EasyPost's `POST /orders/{id}/buy` is the case that needs it: it returns
    #: a `shipments` array, each with its own postage label and tracking code.
    parcel_labels: tuple[Label, ...] = ()
    raw: Any = None
