"""Adapter contract.

An adapter's job is to make one provider look like the shipzil model, and to be
honest about what it cannot do. Two rules matter:

* Never return an empty rate list without populating `Quote.excluded`. An
  unexplained absence of rates is the bug this library exists to fix.
* Never invent dimensions or weights. If a provider needs them and the caller
  gave items only, say so with `ExclusionCode.DIMENSIONS_REQUIRED`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace

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

#: EasyPost writes the same citations in prose form.
_EEI_PROSE = {
    "NOEEI_30_37_a": "NOEEI 30.37(a)",
    "NOEEI_30_37_f": "NOEEI 30.37(f)",
    "NOEEI_30_37_h": "NOEEI 30.37(h)",
    "NOEEI_30_36": "NOEEI 30.36",
    "AES_ITN": "AES ITN",
}

__all__ = ["Adapter", "Capabilities"]


class Capabilities:
    """What a provider surface can actually do.

    Deliberately small: a flag nothing reads is documentation wearing a type.
    Dimension and classification requirements are enforced in each adapter's
    own pre-flight check, which is the single source of truth for them, and
    described in docs/API-REALITY.md.
    """

    #: Rates several parcels in one call.
    native_multi_parcel: bool = False
    #: Has a distinct resource for multi-parcel (EasyPost /orders).
    order_resource: bool = False
    #: Populates Rate.currency.
    returns_currency: bool = True
    #: Populates Rate.delivery_days.
    returns_delivery_estimate: bool = True

    @property
    def emulates_multi_parcel(self) -> bool:
        """True when multi-parcel has to be faked by fanning out."""
        return not (self.native_multi_parcel or self.order_resource)


class Adapter(ABC):
    """One provider surface."""

    #: Stable identifier, e.g. "easypost", "shipstation_v2".
    name: str = ""
    capabilities: Capabilities = Capabilities()


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
    #: written differently per provider: EasyPost wants "NOEEI 30.37(a)" with
    #: spaces and parentheses, Shippo wants the enum token "NOEEI_30_37_a".
    #: shipzil holds the token form and each adapter renders it.
    eei_style: str = "token"

    #: How this provider spells DDP/DDU, or None when shipzil does not express
    #: duty liability here at all. Same reasoning as `eei_style`: one concept,
    #: several spellings, so the mapping lives once and adapters declare a style.
    #: `"upper"` -> `DDP` / `DDU` (Shippo, Easyship).
    #: `"lower"` -> `ddp` / `ddu`. ShipEngine's `terms_of_trade_code` enum is
    #: documented lowercase (`exw fca cpt cip dpu dap ddp fas fob cfr cif ddu
    #: daf deq des`), so lowercase is what shipzil sends. Note their own example
    #: request uses `"DDP"`, so the field may well be case-insensitive; shipzil
    #: has no ShipStation credentials and has never tested that.
    #: `"ddp_only"` -> `DDP` or nothing. EasyPost's `options.incoterm` enum has
    #: no DDU at all (`CFR CIF CIP CPT DAT DAP DDP EXW FAS FCA FOB`), and its
    #: docs say "anything other than 'DDP' will pass the cost and responsibility
    #: of duties on to the recipient". So DDP is the only value worth sending and
    #: recipient-pays is expressed by omitting the field, which is also every
    #: carrier's default. Sending an arbitrary non-DDP term instead would change
    #: the delivery terms as a side effect of a duty choice.
    #: `None` -> the caller's `duties_paid_by` cannot be honoured, and
    #: `duties_gap` reports that rather than dropping it silently.
    incoterm_style: str | None = "upper"

    #: Whether this provider's customs lines mean the per-unit figure or the
    #: line total. Not guessable and not uniform: it splits two against two
    #: among the providers that document it. `"unverified"` means shipzil has no
    #: authoritative source and the current choice is a carried-over default,
    #: not a decision — see docs/GAPS.md.
    customs_value_basis: str = "line_total"

    @staticmethod
    def is_cross_border(shipment: Shipment) -> bool:
        return (shipment.from_address.country or "US") != (
            shipment.to_address.country or "US"
        )

    @staticmethod
    def customs_lines(shipment: Shipment) -> list[CustomsLine]:
        """Declarable lines, flattened across parcels in order.

        Each line carries **both** the per-unit and the line-total figures,
        because providers disagree about which one a customs line means and the
        disagreement is real rather than incidental. Shippo documents
        `net_weight` as "quantity * weight per item"; ShipEngine documents
        `products[].value` as "The declared value of *each* item"; Easyship says
        outright "this value refers to the unit rather than the total". An
        adapter must take the figure matching its own `customs_value_basis`.

        Flattening loses which parcel a line belongs to. That is unavoidable for
        Shippo and EasyPost, whose customs item lists are shipment-level with no
        parcel reference. Adapters that can do better — ShipEngine, via
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
            if self.incoterm_style == "ddp_only":
                # No DDU token exists; omission is recipient-pays. Reported by
                # `duties_expressed_by_omission` so callers can tell the
                # difference between "not asked" and "asked, sent nothing".
                return None
            return "ddu" if self.incoterm_style == "lower" else "DDU"
        # UNSPECIFIED sends nothing, so the account default applies. shipzil used
        # to hardcode DDU here, silently making the recipient liable for duty.
        return None

    def duties_gap(self, shipment: Shipment) -> Exclusion | None:
        """Report a duty-liability choice this provider will not carry.

        Same shape as `hazmat_fidelity_gap`, and for the same reason: the caller
        made a commercial decision and shipzil is about to discard it. Measured
        on the wire, DDP and DDU produced byte-identical payloads on EasyPost and
        ShipStation v1, so `duties_paid_by` was doing nothing on two of five
        providers with no indication to the caller.

        Deliberately worded as a shipzil limitation, not a provider one. EasyPost
        may well support duty billing; its documentation is off-limits here, so
        the honest claim is that *shipzil* does not express it, not that EasyPost
        cannot. ShipStation v1's `internationalOptions` shows no such field in the
        scraped HTML, which is absence of evidence rather than proof of absence.
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
        if self.render_eei(shipment) is None:
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
            if dg.un_number or dg.hazard_class or dg.packing_group:
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

        shipzil takes no idempotency key. Only EasyPost publishes one, and a key
        generated per call deduplicates nothing, so offering the parameter would
        promise more than it delivers. Deduplicate at your own layer, keyed on
        something you own like an order id. See docs/API-REALITY.md.
        """

    def void(self, label: Label) -> bool:
        """Refund/cancel an unused label."""
        raise NotImplementedError(f"{self.name} does not support voiding via shipzil yet")

    def _single_parcel_shipment(self, shipment: Shipment, parcel: Parcel) -> Shipment:
        """A copy of `shipment` carrying exactly one parcel, for fan-out.

        Uses `replace` rather than naming fields, because the hand-written version
        silently dropped every field added to `Shipment` after it. It listed four
        of seven, so `duties_paid_by`, `eei_exemption` and `ship_date` all vanished
        on the fan-out path — which is the path four of six provider surfaces take
        for any multi-parcel shipment.

        The consequences were not cosmetic. A two-parcel DDP shipment reached
        Shippo with `incoterm` unset, silently reverting duty liability to the
        recipient. A declared value above the EEI threshold with an explicit
        `eei_exemption="AES_ITN"` lost the override, so each leg built no customs
        declaration at all while `customs_gap` still passed on the original
        shipment: rating succeeded and the purchase would have failed at the
        carrier, the precise failure this library exists to prevent.

        `replace` cannot drift. A new field is carried without anyone remembering
        to add it here, and `test_fan_out_preserves_every_shipment_field` fails if
        this is ever written out by hand again.
        """
        return replace(shipment, parcels=(parcel,))
