"""Multi-parcel emulation.

Only one of the four supported provider surfaces can rate more than one parcel in
a single call (ShipStation v2's `packages[]`). The other three cannot, and each
refuses differently — silently in some cases.

Reporting "unsupported" on three of four would make the abstraction useless, so
where a provider cannot do it natively, shipzil rates each parcel separately and
combines the results into something shaped like a single multi-parcel quote.

The honesty rules, which matter more than the mechanism:

* A combined rate is only offered when **every** parcel got a quote for that
  same carrier and service. A service that can carry two of three boxes is not
  a way to ship three boxes.
* Combined amounts are marked `Strategy.FANOUT`, and `Rate.is_synthesized` is
  True. A carrier may price one consignment differently from the sum of its
  parts, and the caller is told which number they are looking at.
* Per-parcel quotes are kept in `raw` so the sum can be audited.
* Anything that dropped out — a carrier that covered some parcels but not all —
  becomes an `Exclusion`, not a silence.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .models import Exclusion, ExclusionCode, Quote, Rate, Strategy

__all__ = ["combine_parcel_quotes"]


def _service_key(rate: Rate) -> tuple[str, str]:
    """Identity of a service for combination purposes.

    Prefer the stable gateway key. The display fallback keeps custom third-party
    adapters compatible while they migrate to `Rate.service_key`.
    """
    if rate.service_key is not None:
        return (rate.service_key.provider, rate.service_key.slug)
    return (rate.carrier.strip().lower(), rate.service.strip().lower())


def combine_parcel_quotes(
    per_parcel: list[Quote],
    *,
    provider: str,
    via: str,
) -> Quote:
    """Fold per-parcel quotes into one multi-parcel quote.

    `per_parcel` must be in parcel order, one quote per parcel.
    """
    parcel_count = len(per_parcel)
    if parcel_count == 0:
        return Quote(via=via, strategy=Strategy.FANOUT)
    if parcel_count == 1:
        only = per_parcel[0]
        return Quote(
            rates=only.rates,
            excluded=only.excluded,
            via=via,
            strategy=only.strategy,
            messages=only.messages,
        )

    # Group each parcel's rates by service, so we can find services present for all.
    by_service: dict[tuple[str, str], list[Rate | None]] = defaultdict(
        lambda: [None] * parcel_count
    )
    for index, quote in enumerate(per_parcel):
        for rate in quote.rates:
            slot = by_service[_service_key(rate)]
            # Where a provider returns several rates for one service on one
            # parcel, keep the cheapest — the caller asked to ship, not to audit
            # a provider's duplicate rate rows.
            existing = slot[index]
            if existing is None or rate.amount < existing.amount:
                slot[index] = rate

    combined: list[Rate] = []
    partial: list[Exclusion] = []

    # The key is lowercased for matching, so display names come off the rate
    # itself rather than the key. Both parts of the key are unused here.
    for (_carrier, _service), slots in sorted(by_service.items()):
        present = [r for r in slots if r is not None]
        if len(present) == parcel_count:
            combined.append(_sum_rates(present, provider=provider, parcel_count=parcel_count))
        else:
            missing = parcel_count - len(present)
            example = present[0]
            partial.append(
                Exclusion(
                    code=ExclusionCode.SERVICE_UNAVAILABLE,
                    message=(
                        f"{example.carrier} {example.service} could not cover "
                        f"{missing} of {parcel_count} parcels, so it cannot carry this shipment"
                    ),
                    carrier=example.carrier,
                    service=example.service,
                    source="shipzil",
                )
            )

    # Exclusions the provider itself reported, de-duplicated across parcels.
    seen: set[tuple[ExclusionCode, str | None, str]] = set()
    passthrough: list[Exclusion] = []
    for quote in per_parcel:
        for exc in quote.excluded:
            key = (exc.code, exc.carrier, exc.message)
            if key not in seen:
                seen.add(key)
                passthrough.append(exc)

    messages: list[str] = []
    for quote in per_parcel:
        for msg in quote.messages:
            if msg not in messages:
                messages.append(msg)

    combined.sort(key=lambda r: r.amount)
    return Quote(
        rates=tuple(combined),
        excluded=tuple(passthrough + partial),
        via=via,
        strategy=Strategy.FANOUT,
        messages=tuple(messages),
    )


def _sum_rates(rates: list[Rate], *, provider: str, parcel_count: int) -> Rate:
    """Add per-parcel rates for one service into a single synthesized rate."""
    total = sum((r.amount for r in rates), Decimal(0))

    # Currency must agree. Mixed currencies would make the sum meaningless, and
    # some providers omit it entirely (ShipStation v1), so absent is tolerated
    # while conflicting is not.
    currencies = {r.currency for r in rates if r.currency}
    if len(currencies) > 1:
        raise ValueError(
            f"cannot combine rates in different currencies: {sorted(currencies)}"
        )
    currency = currencies.pop() if currencies else None

    # Slowest parcel governs when the whole shipment has arrived.
    days = [r.delivery_days for r in rates if r.delivery_days is not None]
    delivery_days = max(days) if len(days) == len(rates) else None

    # Guaranteed only if every leg is guaranteed.
    flags = [r.guaranteed for r in rates]
    guaranteed = all(flags) if all(f is not None for f in flags) else None

    first = rates[0]
    return Rate(
        carrier=first.carrier,
        service=first.service,
        amount=total,
        currency=currency,
        delivery_days=delivery_days,
        guaranteed=guaranteed,
        provider=provider or first.provider,
        service_code=first.service_code,
        strategy=Strategy.FANOUT,
        parcel_count=parcel_count,
        raw={"per_parcel": [r.raw for r in rates], "amounts": [str(r.amount) for r in rates]},
        service_key=first.service_key,
        source=first.source,
    )
