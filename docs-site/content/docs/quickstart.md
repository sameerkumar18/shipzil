---
title: Quickstart
description: Configure sources, request rates and buy a label.
---

```bash
uv add git+https://github.com/sameerkumar18/shipzil.git
```

Or with pip:

```bash
pip install git+https://github.com/sameerkumar18/shipzil.git
```

## Configure sources

```python
import shipzil as z

gateway = z.Gateway(
    shippo="shippo_test_...",
    shipstation_v2="...",
)
```

The keyword is both the adapter name and the default source name. ShipStation v1
needs a key and secret:

```python
gateway = z.Gateway(shipstation_v1=("key", "secret"))
```

Use explicit adapters when you need custom source names, two accounts from one
provider, a shorter timeout or a custom transport:

```python
from shipzil.providers import ShippoAdapter

gateway = z.Gateway({
    "shippo-us": ShippoAdapter(us_token, timeout=30),
    "shippo-eu": ShippoAdapter(eu_token, timeout=30),
})
```

## Describe the shipment

```python
sender = z.Address(
    street1="215 Clayton St",
    city="San Francisco",
    state="CA",
    postal_code="94117",
)

recipient = z.Address(
    street1="1600 Pennsylvania Ave NW",
    city="Washington",
    state="DC",
    postal_code="20500",
)

parcel = z.Parcel(
    weight=z.Weight.of(16, "oz"),
    dimensions=z.Dimensions.of(10, 8, 4, "in"),
)

shipment = z.Shipment(sender, recipient, (parcel,))
```

`Shipment.parcels` is always a tuple, including single-parcel shipments.

## Request rates

```python
quote = gateway.get_rates(shipment, carriers={"usps"})

for rate in quote:
    print(rate.source, rate.service, rate.amount, rate.currency)
```

Eligible sources are called concurrently. Rates are grouped by configured source,
then left in the order returned by that provider.

Inspect diagnostics even when rates were returned:

```python
for error in quote.errors:
    log.warning("source failed: %s", error)

for exclusion in quote.excluded:
    log.info("rate excluded: %s", exclusion.message)
```

`quote.excluded` includes rates removed by shipzil's filters and exclusions reported
by providers when available. It cannot describe a service that a provider silently
omitted.

## Select a rate

```python
rate = quote.cheapest
if rate is None:
    raise NoShippingOption(quote.explain())
```

`cheapest` compares amounts only when every returned rate has the same known
currency. It returns `None` for mixed currencies and for ShipStation v1 rates,
whose API does not return a rate currency. In those cases, filter or select using
your application's currency policy.

`fastest` considers only rates with a reported `delivery_days` value.

## Buy

```python
label = gateway.buy(shipment, rate)

print(label.tracking_number)
print(label.label_url)   # URL providers
print(label.label_data)  # base64 providers, currently ShipStation v1
```

The purchase goes to `rate.source`. It is not redirected to another provider.

Purchases are not retried. A provider or network failure after dispatch raises
`AmbiguousPurchaseError`; reconcile with the provider before trying again. See
[Errors and exclusions](./errors.md).

## Cancel or request a refund

```python
accepted = gateway.void(label)
```

`True` means the provider accepted or confirmed the request. Some providers settle
refunds asynchronously, so it does not always mean the money has already returned.

## Next

- [Concepts](./concepts.md): source selection, filters, service keys and FANOUT
- [Providers](./providers.md): support and verification status by adapter
- [International shipping](./international.md): customs, duties and EEI limits
- [Reference](./reference.md): complete field and method lookup
