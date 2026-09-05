"""Adapter contract.

An adapter translates between one provider and the shipzil model. Two rules apply:

* Never return an empty rate list without populating `Quote.excluded`. An
  unexplained absence of rates is the bug this library exists to fix.
* Never invent dimensions or weights. If a provider needs them and the caller
  gave items only, say so with `ExclusionCode.DIMENSIONS_REQUIRED`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Any

from ..http import Transport, request
from ..models import (
    CustomsLine,
    DutiesPaidBy,
    Exclusion,
    ExclusionCode,
    Label,
    LithiumBatteryPacking,
    Parcel,
    Quote,
    Rate,
    Shipment,
)
from ..units import Weight

_EEI_PROSE = {
    "NOEEI_30_37_a": "NOEEI 30.37(a)",
    "NOEEI_30_37_f": "NOEEI 30.37(f)",
    "NOEEI_30_37_h": "NOEEI 30.37(h)",
    "NOEEI_30_36": "NOEEI 30.36",
    "AES_ITN": "AES ITN",
}

__all__ = ["Adapter", "Capabilities"]


@dataclass(frozen=True)
class Capabilities:
    """What a provider surface can actually do.

    Deliberately small: a flag nothing reads is documentation wearing a type.
    Dimension and classification requirements are enforced in each adapter's own
    pre-flight check, which is the single source of truth for them.
    """

    #: Rates several parcels in one call.
    native_multi_parcel: bool = False
    #: Populates Rate.currency.
    returns_currency: bool = True
    #: Populates Rate.delivery_days.
    returns_delivery_estimate: bool = True

    @property
    def emulates_multi_parcel(self) -> bool:
        """True when multi-parcel has to be faked by fanning out."""
        return not self.native_multi_parcel


class Adapter(ABC):
    """One provider surface."""

    #: Stable identifier, e.g. "shippo", "shipstation_v2".
    name: str = ""
    capabilities: Capabilities = Capabilities()
    #: Byte-level HTTP. None uses the shared default. Set it to add logging,
    #: tracing, a proxy, connection pooling, or to replay recorded traffic.
    transport: Transport | None = None

    def http(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> tuple[int, Any]:
        """Make a request through this adapter's transport.

        Every adapter goes through here rather than calling `http.request` directly,
        so a caller-supplied transport cannot be forgotten at one call site — which
        would silently bypass their logging, proxy or recorded cassette.
        """
        kwargs.setdefault("provider", self.name)
        return request(method, url, transport=self.transport, **kwargs)


    @abstractmethod
    def rate_single(self, shipment: Shipment) -> Quote:
        """Rate a shipment containing exactly one parcel.

        Every adapter must implement this; it is the unit the fan-out strategy
        composes when a provider cannot do multi-parcel.
        """

    def rate_native_multi(self, shipment: Shipment) -> Quote:
        """Rate a multi-parcel shipment in one provider call.

        Only implemented where `capabilities` says it is possible.
        """
        raise NotImplementedError(
            f"{self.name} cannot rate multiple parcels natively; the client will fan out"
        )

    #: What hazmat detail this provider can actually carry. Anything a caller
    #: declares that is not in this set gets reported, because a hazmat parcel
    #: that ships under-declared leaves the liability with the shipper while
    #: looking like a success.
    hazmat_fields: frozenset[str] = frozenset()

    #: How this provider spells the EEI exemption. The same regulation is
    #: written differently per provider: some want prose, others want the enum
    #: token "NOEEI_30_37_a".
    #: shipzil holds the token form and each adapter renders it.
    eei_style: str | None = None

    #: How this provider spells DDP/DDU, or None when shipzil does not express
    #: duty liability here at all. Same reasoning as `eei_style`: one concept,
    #: several spellings, so the mapping lives once and adapters declare a style.
    #: `"upper"` -> `DDP` / `DDU` (Shippo, Easyship).
    #: `"lower"` -> `ddp` / `ddu`. ShipEngine's `terms_of_trade_code` enum is
    #: documented lowercase (`exw fca cpt cip dpu dap ddp fas fob cfr cif ddu
    #: daf deq des`), so lowercase is what shipzil sends. Note their own example
    #: request uses `"DDP"`, so the field may well be case-insensitive; shipzil
    #: has no ShipStation credentials and has never tested that.
    #: `None` -> the caller's `duties_paid_by` cannot be honoured, and
    #: `duties_gap` reports that rather than dropping it silently.
    incoterm_style: str | None = "upper"

    #: Whether this provider's customs lines mean the per-unit figure or the
    #: line total. Not guessable and not uniform: it splits evenly among the
    #: providers that document it, and every supported adapter sets it from that
    #: provider's own documentation. Either `"line_total"` or `"per_unit"`.
    customs_value_basis: str = "line_total"

    @staticmethod
    def is_cross_border(shipment: Shipment) -> bool:
        return (shipment.from_address.country or "US") != (
            shipment.to_address.country or "US"
        )

    @staticmethod
    def customs_lines(shipment: Shipment) -> list[CustomsLine]:
        """Declarable lines, flattened across parcels in order.

        Each line carries **both** the per-unit and the line-total figures. An
        adapter must take the one matching its own `customs_value_basis`.

        Flattening loses which parcel a line belongs to. That is unavoidable for
        Shippo, whose customs item list is shipment-level with no parcel
        reference. Adapters that can do better — ShipEngine, via
        `packages[].products[]` — should walk `shipment.parcels` themselves
        rather than use this.
        """
        lines: list[CustomsLine] = []
        for parcel in shipment.parcels:
            for item in parcel.items:
                # Narrowed on the per-unit fields, since the line totals are
                # derived from them: if either unit figure is missing the line
                # cannot be declared on any provider, whichever basis it uses.
                if item.weight is None or item.value is None:
                    continue
                lines.append(
                    CustomsLine(
                        description=item.description,
                        quantity=item.quantity,
                        line_value=item.value * item.quantity,
                        line_weight=Weight(
                            value=item.weight.value * item.quantity,
                            unit=item.weight.unit,
                        ),
                        unit_value=item.value,
                        unit_weight=item.weight,
                        currency=item.currency,
                        hs_code=item.hs_code,
                        origin_country=item.origin_country
                        or shipment.from_address.country
                        or "US",
                        sku=item.sku,
                    )
                )
        return lines

    def render_eei(self, shipment: Shipment) -> str | None:
        """The exemption in this provider's spelling, or None to refuse."""
        if self.eei_style is None:
            return None
        token = shipment.derived_eei_exemption
        if not token:
            return None
        if self.eei_style == "prose":
            return _EEI_PROSE.get(token, token)
        return token

    def render_incoterm(self, shipment: Shipment) -> str | None:
        """DDP/DDU in this provider's spelling, or None to send nothing.

        None covers two different situations on purpose: the caller expressed no
        preference, and the provider has no field for it. Callers who need to tell
        those apart should read `duties_gap`, which fires only for the second.
        """
        if self.incoterm_style is None:
            return None
        if shipment.duties_paid_by is DutiesPaidBy.SENDER:
            return "ddp" if self.incoterm_style == "lower" else "DDP"
        if shipment.duties_paid_by is DutiesPaidBy.RECIPIENT:
            return "ddu" if self.incoterm_style == "lower" else "DDU"
        # UNSPECIFIED sends nothing, so the provider's account default applies.
        return None

    def duties_gap(self, shipment: Shipment) -> Exclusion | None:
        """Report a duty-liability choice this provider will not carry.

        Same shape as `hazmat_fidelity_gap`, and for the same reason: the caller
        made a commercial decision and shipzil is about to discard it. Measured
        on the wire, ShipStation v1 has no duty field, so `duties_paid_by` cannot
        be expressed there.

        Deliberately worded as a shipzil limitation, not a provider one.
        ShipStation v1's `internationalOptions` shows no such field.
        """
        if shipment.duties_paid_by is DutiesPaidBy.UNSPECIFIED:
            return None
        if self.incoterm_style is not None:
            return None
        return Exclusion(
            code=ExclusionCode.DUTIES_UNSUPPORTED,
            message=(
                f"shipzil does not express duty liability on {self.name}, so "
                f"duties_paid_by={shipment.duties_paid_by.name} will not reach the "
                "carrier and the account default applies. If that default is DDU "
                "the recipient is billed import duty on arrival. Use a provider "
                "where shipzil expresses it, or set the default on your account."
            ),
            source="shipzil",
        )

    def eei_gap(self, shipment: Shipment) -> Exclusion | None:
        """Report an explicit EEI citation this adapter does not transmit."""
        if not shipment.eei_exemption or self.eei_style is not None:
            return None
        return Exclusion(
            code=ExclusionCode.CUSTOMS_DECLARATION_REQUIRED,
            message=(
                f"shipzil does not transmit eei_exemption through {self.name}; "
                "use Shippo or handle the filing outside shipzil"
            ),
            source="shipzil",
        )

    def customs_gap(self, shipment: Shipment) -> Exclusion | None:
        """Refuse a cross-border shipment shipzil cannot declare.

        Deliberately raised at *rating* time. Providers happily rate an
        international shipment with no customs data and then fail the purchase —
        Shippo answered a fully rated US-to-Canada shipment with "USPS - Customs
        declaration is required for international shipments via the USPS". A rate
        that can never be bought is worse than no rate.
        """
        if not self.is_cross_border(shipment):
            return None
        items = [item for parcel in shipment.parcels for item in parcel.items]
        if not items:
            return Exclusion(
                code=ExclusionCode.CUSTOMS_DECLARATION_REQUIRED,
                message=(
                    f"{self.name} needs item data for a cross-border shipment. "
                    "Add at least one Item with weight and value."
                ),
                source="shipzil",
            )
        incomplete = [
            item.description for item in items if item.weight is None or item.value is None
        ]
        if incomplete:
            shown = ", ".join(repr(name) for name in incomplete[:3])
            more = f" and {len(incomplete) - 3} more" if len(incomplete) > 3 else ""
            return Exclusion(
                code=ExclusionCode.CUSTOMS_DECLARATION_REQUIRED,
                message=(
                    "every cross-border Item needs both weight and value; missing for "
                    f"{shown}{more}"
                ),
                source="shipzil",
            )
        if not self.customs_lines(shipment):
            return Exclusion(
                code=ExclusionCode.CUSTOMS_DECLARATION_REQUIRED,
                message=(
                    f"{self.name} needs a customs declaration for a cross-border "
                    "shipment. Give every Item a weight and a value, and an hs_code "
                    "where you have one. Rating would succeed without them and the "
                    "purchase would then fail."
                ),
                source="shipzil",
            )
        if (
            shipment.from_address.country == "US"
            and self.eei_style is not None
            and self.render_eei(shipment) is None
        ):
            return Exclusion(
                code=ExclusionCode.CUSTOMS_DECLARATION_REQUIRED,
                message=(
                    f"declared value {shipment.declared_value} exceeds the $2,500 "
                    "NOEEI 30.37(a) threshold, so this export needs an AES filing and "
                    "an ITN that shipzil cannot produce. Set "
                    "Shipment(eei_exemption=...) with your ITN or the correct citation."
                ),
                source="shipzil",
            )
        return None

    def hazmat_fidelity_gap(self, shipment: Shipment) -> Exclusion | None:
        """Report hazmat detail this provider will drop.

        Not a refusal. Plenty of hazmat ships lawfully under limited or excepted
        quantity with far less paperwork than a fully regulated consignment, and
        deciding which applies is the shipper's call, not shipzil's. But a
        declaration silently discarded is exactly the failure this library
        exists to surface, so it comes back as an `Exclusion` on the quote.
        """
        declared: set[str] = set()
        for dg in shipment.dangerous_goods:
            if dg.lithium_batteries is not LithiumBatteryPacking.NONE:
                declared.add("lithium_batteries")
            if dg.biological_material:
                declared.add("biological_material")
            if dg.contains_liquids:
                declared.add("contains_liquids")
            if dg.contains_alcohol:
                declared.add("contains_alcohol")
            if dg.dry_ice is not None:
                declared.add("dry_ice")
            if any(
                (
                    dg.un_number,
                    dg.shipping_name,
                    dg.technical_name,
                    dg.hazard_class,
                    dg.packing_group,
                    dg.packing_instruction,
                    dg.regulation_level,
                    dg.regulation_authority,
                    dg.transport_mode,
                    dg.reportable_quantity,
                    dg.emergency_contact_name,
                    dg.emergency_contact_phone,
                )
            ):
                declared.add("regulated_detail")
            if dg.radioactive:
                declared.add("radioactive")
        dropped = sorted(declared - self.hazmat_fields)
        if not dropped:
            return None
        return Exclusion(
            code=ExclusionCode.HAZMAT_DETAIL_UNSUPPORTED,
            message=(
                f"{self.name} cannot carry these declared hazmat details: "
                f"{', '.join(dropped)}. They will not reach the carrier, so the "
                "shipment may be under-declared. Use a provider that supports "
                "them, or file the declaration outside shipzil."
            ),
            source="shipzil",
        )

    def is_test_mode(self) -> bool | None:
        """True/False where determinable, None where the provider gives no hint."""
        return None

    @abstractmethod
    def buy(self, shipment: Shipment, rate: Rate) -> Label:
        """Purchase postage. Never retried: every implementation sends retries=0.

        shipzil takes no idempotency key, because no supported provider documents a
        caller-supplied one on the purchase path. Offering the parameter would
        promise a guarantee shipzil cannot keep. Deduplicate at your own layer,
        keyed on something you own such as an order id.
        """

    def void(self, label: Label) -> bool:
        """Refund/cancel an unused label."""
        raise NotImplementedError(f"{self.name} does not support voiding via shipzil yet")

    def single_parcel_shipment(self, shipment: Shipment, parcel: Parcel) -> Shipment:
        """A copy of `shipment` carrying exactly one parcel, for fan-out.

        Uses `dataclasses.replace` rather than naming fields so a field added to
        `Shipment` later cannot silently vanish on the fan-out path. Dropping
        `duties_paid_by` or `eei_exemption` here would let rating succeed and the
        purchase fail at the carrier.

        """
        return replace(shipment, parcels=(parcel,))
