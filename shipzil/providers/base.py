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

from ..models import (
    Exclusion,
    ExclusionCode,
    Label,
    LithiumBatteryPacking,
    Parcel,
    Quote,
    Rate,
    Shipment,
)

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
        """A copy of `shipment` carrying exactly one parcel, for fan-out."""
        return Shipment(
            from_address=shipment.from_address,
            to_address=shipment.to_address,
            parcels=(parcel,),
            reference=shipment.reference,
        )
