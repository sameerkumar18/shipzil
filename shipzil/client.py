"""The client callers actually use.

Responsibilities:

* Pick the right provider resource for the parcel count, or emulate multi-parcel
  by fanning out when the provider cannot do it.
* Enforce guardrails before spending money.
* Never retry a purchase blindly.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from .errors import CapabilityError, ConfigurationError, SpendLimitExceeded
from .models import Label, Quote, Rate, Shipment
from .multiparcel import combine_parcel_quotes
from .providers.base import Adapter

__all__ = ["Client"]


class Client:
    """A shipping client bound to one provider.

    Multi-provider failover builds on this and is deliberately a later layer;
    getting one provider honest is the harder half.
    """

    def __init__(
        self,
        adapter: Adapter,
        *,
        max_spend: Decimal | float | str | None = None,
        dry_run: bool = False,
    ):
        self.adapter = adapter
        self.dry_run = dry_run
        self.max_spend = Decimal(str(max_spend)) if max_spend is not None else None

    # ── rating ──────────────────────────────────────────────────────

    def get_rates(self, shipment: Shipment) -> Quote:
        """Rate a shipment of any parcel count.

        The caller gets the same shape regardless of whether the provider
        supports multi-parcel; `Quote.strategy` says which path was taken.
        """
        if not shipment.is_multi_parcel:
            return self.adapter.rate_single(shipment)

        caps = self.adapter.capabilities
        if caps.native_multi_parcel or caps.order_resource:
            return self.adapter.rate_native_multi(shipment)

        # Emulate: rate each parcel alone, then combine.
        per_parcel = [
            self.adapter.rate_single(self.adapter._single_parcel_shipment(shipment, parcel))
            for parcel in shipment.parcels
        ]
        return combine_parcel_quotes(
            per_parcel,
            provider=self.adapter.name,
            via=f"{self.adapter.name}:fanout×{len(shipment.parcels)}",
        )

    # ── buying ──────────────────────────────────────────────────────

    def buy(
        self,
        shipment: Shipment,
        rate: Rate,
        *,
        idempotency_key: str | None = None,
    ) -> Label:
        """Purchase postage for `rate`.

        Guardrails run before any network call, so a dry run or a spend-limit
        breach costs nothing.
        """
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
                raw={"dry_run": True, "rate": rate.raw},
            )

        key = self._resolve_idempotency_key(idempotency_key)
        return self.adapter.buy(shipment, rate, idempotency_key=key)

    def _resolve_idempotency_key(self, requested: str | None) -> str | None:
        """Decide what key, if any, reaches the adapter.

        Only EasyPost can honour one. Passing a key to the others and dropping
        it would hand the caller a guarantee that does not exist, so an explicit
        key against an unsupporting provider is refused up front instead.

        Purchases are never retried on any provider, which is a real protection
        against the duplicate-charge case that matters most. It just isn't the
        same as provider-side deduplication, and shipzil will not conflate them.
        """
        if self.adapter.supports_idempotency_key:
            return requested or f"shipzil-{uuid.uuid4()}"

        if requested is not None:
            raise CapabilityError(
                f"{self.adapter.name} cannot honour an idempotency key: it publishes no such "
                "header, so shipzil would have to discard the one you passed and let you believe "
                "a repeat purchase is safe. shipzil never retries a purchase, which covers the "
                "common case. If you accept that weaker guarantee, omit idempotency_key. "
                "Check adapter.supports_idempotency_key to branch on this.",
                provider=self.adapter.name,
            )
        return None

    def void(self, label: Label) -> bool:
        if self.dry_run:
            return True
        return self.adapter.void(label)


def _unused(*_: object) -> None:  # pragma: no cover
    raise ConfigurationError("unreachable")
