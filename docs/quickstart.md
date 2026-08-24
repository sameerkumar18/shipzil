# Quickstart

Every snippet here is real code that has been run against a provider sandbox.

## 1. Pick an adapter

One adapter is one provider account. You supply the credential you already have.

```python
import shipzil as z
from shipzil.providers import (
    EasyPostAdapter,
    EasyshipAdapter,
    ShippoAdapter,
    ShipStationV1Adapter,
    ShipStationV2Adapter,
)

adapter = EasyPostAdapter("EZTK...")           # test or production key
client = z.Client(adapter)
```

Two optional guardrails, both worth setting early:

```python
client = z.Client(
    adapter,
    max_spend="25.00",  # refuses to buy a rate above this, before any network call
    dry_run=False,      # True returns a fake label and never spends money
)
```

## 2. Describe the shipment

Addresses, then parcels. `Weight` and `Dimensions` convert at the boundary, so
you give whatever unit you have and shipzil sends whatever the provider wants.

```python
sender = z.Address(
    street1="215 Clayton St", city="San Francisco", state="CA",
    postal_code="94117", country="US",
    name="Sameer Kumar", phone="4151234567", email="s@example.com",
)
recipient = z.Address(
    street1="1600 Pennsylvania Ave NW", city="Washington", state="DC",
    postal_code="20500", country="US",
    name="Recipient", phone="2024561111", email="r@example.com",
)

parcel = z.Parcel(
    weight=z.Weight.of(16, "oz"),
    dimensions=z.Dimensions.of(10, 8, 4, "in"),
)

shipment = z.Shipment(sender, recipient, (parcel,))
```

!!! tip "Parcels is always a sequence"
    Even for one box. That is what makes multi-parcel a non-event rather than a
    different code path.

## 3. Rate

```python
quote = client.get_rates(shipment)

print(f"{len(quote.rates)} rates via {quote.via} ({quote.strategy.value})")
for rate in quote.rates:
    print(f"  {rate.carrier:10s} {rate.service:28s} {rate.amount} {rate.currency}")
```

`Quote` carries more than a list of rates:

| Field | What it tells you |
|---|---|
| `rates` | what you can buy |
| `excluded` | what dropped out, and **why** — see [Errors](errors.md) |
| `via` | which provider surface answered, e.g. `easypost:orders` |
| `strategy` | `NATIVE`, `ORDER` or `FANOUT` — see [Concepts](concepts.md) |
| `messages` | provider prose that did not map to a structured code |

## 4. Buy

```python
cheapest = min(quote.rates, key=lambda r: r.amount)
label = client.buy(shipment, cheapest)

print(label.tracking_number)   # e.g. "LM000024452US"
print(label.label_url)         # PDF/PNG/ZPL, provider-hosted
print(label.is_test)           # True on a test key, where knowable
```

!!! danger "Purchases are never retried automatically"
    A repeated purchase can buy postage twice, and only one of the four providers
    publishes an idempotency key. shipzil sends purchases with retries disabled.
    If a purchase fails ambiguously you get
    [`AmbiguousPurchaseError`](errors.md#ambiguouspurchaseerror) — treat it as
    "may have succeeded", never as "failed".

## 5. Void, if you need to

```python
client.void(label)   # True when the provider confirms the refund
```

## Multi-parcel

Add parcels. Nothing else changes.

```python
shipment = z.Shipment(sender, recipient, (parcel, parcel, parcel))
quote = client.get_rates(shipment)
print(quote.strategy, quote.via)
```

What happens underneath depends on the provider, and shipzil tells you which
happened rather than pretending they are the same:

- **EasyPost** switches to its `/orders` resource automatically — `strategy=ORDER`
- **ShipStation v2** sends native `packages[]` — `strategy=NATIVE`
- **Shippo, ShipStation v1, Easyship** cannot, so shipzil rates each parcel and
  combines — `strategy=FANOUT`, and `via` reads e.g. `shippo:fanoutx3`

A combined rate is only offered when a carrier covered **every** parcel. If a
carrier covered two of three, it is excluded with a reason instead of quietly
quoting a partial shipment.

## Shipping across a border

Customs needs item detail, and shipzil refuses to quote a cross-border shipment
it cannot declare rather than handing you a rate that fails at purchase.

```python
from decimal import Decimal

item = z.Item(
    "cotton t-shirt",
    quantity=2,
    weight=z.Weight.of(6, "oz"),     # per unit
    value=Decimal("15"),             # per unit
    hs_code="610910",
    origin_country="US",
)

toronto = z.Address(
    street1="220 Yonge St", city="Toronto", state="ON",
    postal_code="M5B 2H1", country="CA",
    name="Recipient", phone="4165550199", email="r@example.com",
)

shipment = z.Shipment(
    sender, toronto,
    (z.Parcel(weight=z.Weight.of(16, "oz"),
              dimensions=z.Dimensions.of(10, 8, 4, "in"),
              items=(item,)),),
    duties_paid_by=z.DutiesPaidBy.SENDER,   # DDP: you pay the duty
)
```

`weight` and `value` on an `Item` are **per unit**. shipzil converts to whatever
basis each provider documents. Read
[International shipping](international.md) before shipping commercially — the
per-unit-versus-total distinction is the single easiest way to misdeclare a
shipment.

## Next

- [Concepts](concepts.md) — the object model, and what `strategy` means
- [Providers](providers.md) — what each provider can and cannot do
- [Errors and exclusions](errors.md) — how failures are reported
