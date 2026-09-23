---
title: Migrating
description: Moving rate, buy and void calls from the Shippo, ShipEngine and ShipStation SDKs to shipzil.
---

shipzil is not a drop-in replacement for a vendor SDK. It replaces one path -
rate, buy, void - and makes that path identical across four providers. The vendor
SDKs cover a single provider far more deeply than shipzil covers any of them.

Read this page as "which calls move" rather than "which package to uninstall".
The common outcome is that both stay installed: shipzil for the rating and label
path, the vendor SDK for the provider features shipzil has no opinion about.

## The packages this page compares

Surfaces below were measured by installing each package and introspecting it, not
read from marketing pages.

| Package | Version | Providers reached | Callable surface | Packages installed | Typing |
|---|---|---|---|---|---|
| `shippo` | 3.9.0 | Shippo | 72 methods across 21 resource groups | 15 | typed request and response models |
| `shipengine` | 2.1.1 | ShipStation v2, ShipEngine | 10 methods | 23 | `dict` in, `dict` out |
| `shipstation` | 0.1.3, unofficial | ShipStation v1 | 8 methods, order centric | 6 | untyped |
| `shipzil` | 0.1.0 | Shippo, Easyship, ShipStation v1, ShipStation v2 | 3 `Gateway` methods | 1 | typed dataclasses |

Two facts from that table drive most of this page.

The first is breadth against depth. `shippo` exposes 72 methods because it maps
the whole Shippo API: manifests, batches, pickups, webhooks, carrier account
registration, parcel templates. shipzil exposes 3. Moving to shipzil for rating
does not give you the other 69.

The second is that `shipstation` 0.1.3 is unofficial and order centric. Its eight
methods are `add_order`, `fetch_orders`, `get_orders`, `submit_orders`, `get`,
`post`, `as_dict`, `to_camel_case`. It has no rating or label call, so for
ShipStation v1 there is no rating code to port - shipzil adds a path that
package never had.

### These SDKs conflict with each other

Installing all three into one environment does not produce three working SDKs:

```
pip install shippo shipengine shipstation
→ shippo resolves to 2.1.2, not 3.9.0

pip install "shippo==3.9.0"
→ ERROR: shipengine 2.1.1 requires dataclasses-json<0.6.0,>=0.5.3,
  but you have dataclasses-json 0.6.7 which is incompatible
```

A multi-provider application built on vendor SDKs inherits every vendor's
dependency pins. shipzil has no runtime dependencies, so adding it cannot cause
this class of conflict, and it cannot pin `requests` or `urllib3` versions out
from under you.

## Shippo SDK to shipzil

### Rating

Before, with `shippo` 3.9.0:

```python
import shippo
from shippo.models import components

client = shippo.Shippo(api_key_header="ShippoToken SHIPPO_TEST_KEY")

created = client.shipments.create(
    components.ShipmentCreateRequest(
        address_from=components.AddressCreateRequest(
            name="Warehouse",
            street1="215 Clayton St",
            city="San Francisco",
            state="CA",
            zip="94117",
            country="US",
        ),
        address_to=components.AddressCreateRequest(
            name="Buyer",
            street1="1 Rocket Rd",
            city="Hawthorne",
            state="CA",
            zip="90250",
            country="US",
        ),
        parcels=[
            components.ParcelCreateRequest(
                length="10",
                width="8",
                height="4",
                distance_unit=components.DistanceUnitEnum.IN,
                weight="2",
                mass_unit=components.WeightUnitEnum.LB,
            )
        ],
        async_=False,
    )
)

for rate in created.rates:
    print(rate.provider, rate.servicelevel.name, rate.amount, rate.currency)
```

After, with shipzil:

```python
from shipzil import Address, Gateway, Parcel, Shipment
from shipzil.units import Dimensions, Weight

gateway = Gateway(shippo="SHIPPO_TEST_KEY")

shipment = Shipment(
    from_address=Address(
        name="Warehouse",
        street1="215 Clayton St",
        city="San Francisco",
        state="CA",
        postal_code="94117",
        country="US",
    ),
    to_address=Address(
        name="Buyer",
        street1="1 Rocket Rd",
        city="Hawthorne",
        state="CA",
        postal_code="90250",
        country="US",
    ),
    parcels=(
        Parcel(
            weight=Weight(2, "lb"),
            dimensions=Dimensions(10, 8, 4, "in"),
        ),
    ),
)

quote = gateway.get_rates(shipment)
for rate in quote:
    print(rate.source, rate.carrier, rate.service, rate.amount, rate.currency)
```

Four differences matter more than the shorter code.

`zip` becomes `postal_code`, because the field is not US specific. Units become
explicit `Weight` and `Dimensions` objects instead of a string paired with a
separate enum, so a weight cannot be passed without its unit.

There is no shipment object to create first. Shippo's API models rating as
"create a shipment, read `.rates` off it"; shipzil treats `Shipment` as a local
value and `get_rates` as the call. Nothing is persisted provider side by building
a `Shipment`.

`rate.source` has no Shippo equivalent. It names which configured credential
produced the rate, which is what makes the same loop work when a second provider
is added.

### Buying a label

Before:

```python
transaction = client.transactions.create(
    components.TransactionCreateRequest(
        rate=chosen_rate.object_id,
        label_file_type=components.LabelFileTypeEnum.PDF,
        async_=False,
    )
)

if transaction.status != "SUCCESS":
    raise RuntimeError(transaction.messages)

print(transaction.tracking_number, transaction.label_url)
```

After:

```python
label = gateway.buy(shipment, quote.cheapest)
print(label.tracking_number, label.label_url)
```

shipzil raises on failure rather than returning an object whose `status` you must
remember to check. A Shippo transaction can come back `HTTP 201` with
`"status": "ERROR"`; the `transaction.status != "SUCCESS"` branch above is load
bearing, and omitting it is a common source of shipments that were never really
bought.

`gateway.buy` also routes the purchase back through the credential that produced
the rate. With one provider configured that is invisible. With several it is the
difference between buying the label you priced and buying a different one.

### Voiding

Before:

```python
refund = client.refunds.create(transaction=transaction.object_id)
print(refund.status)
```

After:

```python
gateway.void(label)
```

`void` returns `True` when the provider confirms, and raises when it refuses. It
does not return a status string for you to interpret.

### Calls with no shipzil equivalent

These have no mapping and should stay on the Shippo SDK:

`addresses.validate`, `tracking_status`, `manifests`, `batches`, `pickups`,
`orders`, `webhooks`, `carrier_accounts`, `user_parcel_templates`,
`carrier_parcel_templates`, `service_groups`, `rates_at_checkout`,
`shippo_accounts`, `customs_declarations` and `customs_items` as standalone
objects.

## ShipEngine SDK to shipzil

`shipengine` 2.1.1 is `dict` in, `dict` out. Nothing is typed, so field names are
only checked at runtime by the API.

Before:

```python
from shipengine import ShipEngine

client = ShipEngine("SHIPSTATION_V2_KEY")

result = client.get_rates_from_shipment(
    {
        "shipment": {
            "ship_from": {
                "name": "Warehouse",
                "address_line1": "215 Clayton St",
                "city_locality": "San Francisco",
                "state_province": "CA",
                "postal_code": "94117",
                "country_code": "US",
                "phone": "5551234567",
            },
            "ship_to": {
                "name": "Buyer",
                "address_line1": "1 Rocket Rd",
                "city_locality": "Hawthorne",
                "state_province": "CA",
                "postal_code": "90250",
                "country_code": "US",
            },
            "packages": [{"weight": {"value": 2, "unit": "pound"}}],
        },
        "rate_options": {"carrier_ids": ["se-123456"]},
    }
)

for rate in result["rate_response"]["rates"]:
    print(rate["carrier_friendly_name"], rate["shipping_amount"]["amount"])
```

After:

```python
gateway = Gateway(shipstation_v2="SHIPSTATION_V2_KEY")
quote = gateway.get_rates(shipment)

for rate in quote:
    print(rate.carrier, rate.service, rate.amount, rate.currency)
```

The `carrier_ids` lookup disappears. `shipengine` requires you to call
`list_carriers` and pass carrier IDs into `rate_options`; the shipzil adapter
resolves carriers for the account itself. To narrow the result, filter on carrier
names rather than opaque IDs:

```python
quote = gateway.get_rates(shipment, carriers=["ups", "usps"])
```

Label and void map directly:

| `shipengine` | shipzil |
|---|---|
| `create_label_from_rate_id(rate_id, {})` | `gateway.buy(shipment, rate)` |
| `create_label_from_shipment({...})` | `gateway.buy(shipment, rate)` after `get_rates` |
| `void_label_by_label_id(label_id, config)` | `gateway.void(label)` |
| `get_rates_from_shipment({...})` | `gateway.get_rates(shipment)` |
| `validate_addresses([{...}])` | no equivalent, keep `shipengine` |
| `track_package_by_label_id(...)` | no equivalent, keep `shipengine` |
| `track_package_by_carrier_code_and_tracking_number(...)` | no equivalent, keep `shipengine` |
| `list_labels_by_tracking_number(...)` | no equivalent, keep `shipengine` |
| `list_carriers()` | not needed for rating, no equivalent |
| `get_rate_estimate({...})` | no equivalent, `get_rates` needs a full shipment |

Half that table is "no equivalent". `shipengine`'s 10 methods are weighted toward
tracking and validation, and shipzil covers neither.

## ShipStation v1 to shipzil

The unofficial `shipstation` package manages orders. It cannot rate or buy, so
there is no before and after pair - only new capability:

```python
gateway = Gateway(
    shipstation_v1=("SHIPSTATION_V1_KEY", "SHIPSTATION_V1_SECRET"),
)
quote = gateway.get_rates(shipment)
```

v1 takes a key and secret pair, passed as a two item tuple.

One v1 specific behaviour to plan for: **v1 rates carry no currency**. The API
returns bare numbers, and shipzil surfaces `rate.currency` as `None` rather than
guessing USD. Two consequences follow, and the first is stronger than it looks:

- **`quote.cheapest` is always `None` on a v1 quote**, not only on mixed ones.
  Measured against a live v1 account: 27 rates returned, `cheapest` was `None`.
  Comparing amounts with no currency would be arithmetic on unlabelled numbers, so
  shipzil declines rather than assuming. Adding a currency-bearing provider does
  not fix it either - a mixed quote of 30 rates also returned `None`.
- `max_spend` refuses a v1 rate rather than assuming its currency. Set
  `max_spend_currency` if you need a ceiling enforced, and expect v1 rates to be
  excluded by it.

So on v1 you select a rate yourself instead of reaching for `cheapest`:

```python
quote = gateway.get_rates(shipment)
rate = min(quote, key=lambda r: r.amount)  # you are asserting the currency
label = gateway.buy(shipment, rate)
```

Writing that `min` is the point at which you take responsibility for the
assumption shipzil refused to make. That is deliberate, not an oversight to work
around silently.

## Running shipzil beside a vendor SDK

The usual arrangement. shipzil owns the money path; the vendor SDK owns what it
uniquely provides:

```python
import shippo
from shippo.models import components
from shipzil import Gateway

gateway = Gateway(shippo=TOKEN, easyship=EASYSHIP_KEY)
vendor = shippo.Shippo(api_key_header=f"ShippoToken {TOKEN}")

# Compare across providers and buy the cheaper one.
quote = gateway.get_rates(shipment)
label = gateway.buy(shipment, quote.cheapest)

# Track through the vendor SDK, which shipzil does not cover.
status = vendor.tracking_status.get(
    carrier=label.carrier.lower(),
    tracking_number=label.tracking_number,
)
```

The same credential can back both. shipzil does not hold provider state, so
nothing is desynchronised by using both against one account.

## Behaviour differences to plan for

Things that are deliberate in shipzil and will read as surprising if you arrive
from a single-provider SDK.

**Rating queries every configured source.** `Gateway(shippo=..., easyship=...)`
means `get_rates` calls both, concurrently, and returns one merged list. Scope it
with `sources=` for configured credentials or `providers=` for adapter types.

**A failure does not empty the result.** If one source fails and another
succeeds, you get the successful rates plus an entry in `quote.errors`. Check
`bool(quote)` for rates and `quote.errors` for what did not answer.

**Filtered rates are accounted for, not dropped.** Narrowing by `carriers=` or
`services=` puts every removed rate in `quote.excluded` with a reason code, so a
filter that matches nothing is distinguishable from a provider that returned
nothing.

**Purchases are bound to the quoting source.** `buy` uses the credential that
produced the rate. There is no way to pass a rate from one source to another.

**An ambiguous purchase is its own error.** If a purchase request fails in a way
that leaves it unclear whether a label was created, shipzil raises
`AmbiguousPurchaseError` instead of a generic provider error. Treat it as "check
the provider dashboard before retrying", because shipzil will not retry a
purchase for you.

**No idempotency keys.** No supported provider documents a caller-supplied
idempotency key for label purchase, so shipzil does not pretend to offer one. A
purchase that times out needs reconciliation, not a blind retry.

**Cross-border rating is validated locally.** If a parcel crosses a border and
any item lacks a weight or a value, shipzil raises before the network call rather
than letting the provider invent a customs value.

**Synchronous only.** There is no async client. Concurrency across sources is
handled internally with threads, bounded by `max_workers`. Note that `shippo`
3.9.0 is also synchronous - introspection shows zero async variants across its 72
methods - so this is not a regression when migrating from it.

## What shipzil does not do

Current gaps, so you can decide what has to stay on a vendor SDK. This is the
same list that drives the [roadmap](./roadmap.md).

| Capability | Status | Where to get it today |
|---|---|---|
| Tracking | not implemented | `shippo.tracking_status`, `shipengine` tracking methods |
| Address validation | not implemented | `shippo.addresses.validate`, `shipengine.validate_addresses` |
| Manifests and close-out | not implemented | `shippo.manifests` |
| Batch label creation | not implemented | `shippo.batches` |
| Pickup scheduling | not implemented | `shippo.pickups` |
| Webhook management | not implemented | `shippo.webhooks` |
| Carrier account onboarding | not implemented | `shippo.carrier_accounts` |
| Order management | not implemented | `shippo.orders`, `shipstation` |
| Parcel template CRUD | read-only model | `shippo.user_parcel_templates` |
| Rate estimate without a full shipment | not implemented | `shipengine.get_rate_estimate` |
| Returns and reverse labels | not implemented | provider APIs directly |
| Async client | not implemented | none, `shippo` is synchronous too |
| Per-package labels on multi-piece | removed rather than empty | provider APIs directly |

Rating, buying and voiding are the covered surface. Everything else in that table
is a reason to keep a vendor SDK installed, which is why this page does not
describe removing one.
