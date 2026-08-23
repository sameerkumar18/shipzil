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

from ..models import Label, Parcel, Quote, Rate, Shipment

__all__ = ["Adapter", "Capabilities"]


class Capabilities:
    """What a provider surface can actually do.

    Defaults are the conservative case. Values here are asserted by
    `scripts/probe_capabilities.py` against live sandboxes rather than taken from
    documentation, which was wrong about multi-parcel for two providers.
    """

    #: Rates several parcels in one call.
    native_multi_parcel: bool = False
    #: Has a distinct resource for multi-parcel (EasyPost /orders).
    order_resource: bool = False
    #: Needs dimensions somewhere — on the box, or on every item.
    requires_explicit_dimensions: bool = True
    #: Can compute the box itself from per-item dimensions or stored SKUs.
    #: Only Easyship. Everyone else needs a box on the parcel.
    can_derive_box_from_items: bool = False
    #: Requires customs classification even domestically (Easyship).
    requires_item_classification: bool = False
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

    #: Whether the provider accepts a client-supplied idempotency key on
    #: purchase and will collapse a repeat into the original label.
    #:
    #: **Only EasyPost does.** Verified against provider documentation, not
    #: assumed: Shippo documents no such header on `/transactions` (its
    #: "idempotency key" is internal billing reconciliation), and ShipStation
    #: v2 documents exactly two headers, `API-Key` and `Content-Type`.
    #: Easyship has no key either, but is structurally protected — a second
    #: label request for the same shipment id is refused with "labels already
    #: requested". See docs/API-REALITY.md.
    #:
    #: shipzil refuses an explicit key rather than accepting one it cannot
    #: honour. Silently dropping it would be the exact failure this library
    #: exists to surface.
    supports_idempotency_key: bool = False

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

    def is_test_mode(self) -> bool | None:
        """Whether this adapter is operating against test credentials.

        Returns None when the provider gives no way to tell. Reporting False in
        that case would assert "these are production credentials" on no evidence,
        so the uncertainty is passed through to `Label.is_test` instead.
        """
        return None

    @abstractmethod
    def buy(self, shipment: Shipment, rate: Rate, *, idempotency_key: str | None) -> Label:
        """Purchase postage.

        `idempotency_key` is only ever non-None when
        `supports_idempotency_key` is True; the client enforces that. Adapters
        that cannot honour a key receive None so the parameter can never be
        accepted and quietly discarded.

        No adapter may retry a purchase. Every implementation passes
        `retries=0`, because a repeat without provider-side deduplication risks
        buying postage twice.
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
