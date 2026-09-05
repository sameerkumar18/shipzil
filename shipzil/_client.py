"""The per-source leaf client. Internal.

`Gateway` is the public entry point; this is the single-provider layer it calls
once per configured source. It is private because a caller choosing a provider
by hand is choosing the one thing the gateway exists to abstract.

Responsibilities:

* Pick the right provider resource for the parcel count, or emulate multi-parcel
  by fanning out when the provider cannot do it.
* Enforce guardrails before spending money.
* Never retry a purchase blindly.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal

from .errors import (
    AmbiguousPurchaseError,
    CapabilityError,
    ConfigurationError,
    ProviderError,
    SpendLimitExceeded,
)
from .models import Label, Quote, Rate, Shipment
from .multiparcel import combine_parcel_quotes
from .providers.base import Adapter

__all__ = ["Client"]


class Client:
    """A shipping client bound to one provider. Internal; use `Gateway`."""

    def __init__(
        self,
        adapter: Adapter,
        *,
        max_spend: Decimal | float | str | None = None,
        dry_run: bool = False,
        max_workers: int | None = None,
    ):
        self.adapter = adapter
        self.dry_run = dry_run
        self.max_spend = Decimal(str(max_spend)) if max_spend is not None else None
        self.max_workers = max_workers

    # ── rating ──────────────────────────────────────────────────────

    def get_rates(self, shipment: Shipment) -> Quote:
        """Rate a shipment of any parcel count.

        The caller gets the same shape regardless of whether the provider
        supports multi-parcel; `Quote.strategy` says which path was taken.
        """
        customs_gap = self.adapter.customs_gap(shipment)
        if customs_gap is not None:
            return Quote(excluded=(customs_gap,), via=f"{self.adapter.name}:preflight")

        quote = self._rate(shipment)
        # Attached here rather than in each adapter so none can forget them.
        extra = [
            g
            for g in (
                self.adapter.eei_gap(shipment),
                self.adapter.duties_gap(shipment),
                self.adapter.hazmat_fidelity_gap(shipment),
            )
            if g is not None
        ]
        if extra:
            quote = replace(quote, excluded=(*quote.excluded, *extra))
        return quote

    def _rate(self, shipment: Shipment) -> Quote:
        if not shipment.is_multi_parcel:
            return self.adapter.rate_single(shipment)

        caps = self.adapter.capabilities
        if caps.native_multi_parcel:
            return self.adapter.rate_native_multi(shipment)

        # Emulate: rate each parcel alone, then combine. The per-parcel calls are
        # independent, so they run concurrently — a five-parcel shipment would
        # otherwise pay five round trips end to end. Results are read back in parcel
        # order because `combine_parcel_quotes` matches quotes to parcels by
        # position, and a rate belonging to the wrong parcel would be silently
        # mispriced rather than obviously broken.
        parcels = shipment.parcels
        legs = [self.adapter.single_parcel_shipment(shipment, parcel) for parcel in parcels]
        if len(legs) == 1:  # pragma: no cover - is_multi_parcel guarantees > 1
            per_parcel = [self.adapter.rate_single(legs[0])]
        else:
            workers = self.max_workers or len(legs)
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="shipzil-parcel"
            ) as pool:
                per_parcel = list(pool.map(self.adapter.rate_single, legs))

        return combine_parcel_quotes(
            per_parcel,
            provider=self.adapter.name,
            via=f"{self.adapter.name}:fanoutx{len(parcels)}",
        )

    # ── buying ──────────────────────────────────────────────────────

    def buy(
        self,
        shipment: Shipment,
        rate: Rate,
    ) -> Label:
        """Purchase postage for `rate`.

        Guardrails run before any network call, so a dry run or a spend-limit
        breach costs nothing.
        """
        if self.max_spend is not None and rate.currency is None:
            raise ConfigurationError(
                "max_spend cannot be enforced because this rate has no currency",
                provider=self.adapter.name,
            )
        if self.max_spend is not None and rate.amount > self.max_spend:
            raise SpendLimitExceeded(
                f"rate {rate.amount} exceeds max_spend {self.max_spend}",
                limit=self.max_spend,
                attempted=rate.amount,
            )

        if rate.is_synthesized:
            raise CapabilityError(
                f"this rate was synthesized by summing {rate.parcel_count} per-parcel quotes "
                f"({self.adapter.name} cannot rate multiple parcels in one call), so it cannot be "
                "bought as a single label. Buy each parcel individually, or use a provider with "
                "native multi-parcel support.",
                provider=self.adapter.name,
            )

        if self.dry_run:
            return Label(
                tracking_number="DRYRUN",
                label_url="",
                carrier=rate.carrier,
                service=rate.service,
                amount=rate.amount,
                currency=rate.currency,
                provider=self.adapter.name,
                # Not a guess: a dry run never reached the network.
                is_test=True,
                raw={"dry_run": True, "rate": rate.raw},
            )

        try:
            return self.adapter.buy(shipment, rate)
        except ProviderError as error:
            raise AmbiguousPurchaseError(
                "the purchase request failed after dispatch and may have succeeded; "
                "reconcile with the provider before trying again",
                provider=self.adapter.name,
                messages=error.messages,
            ) from error

    def void(self, label: Label) -> bool:
        if self.dry_run:
            return True
        return self.adapter.void(label)
