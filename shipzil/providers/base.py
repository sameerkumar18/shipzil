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
