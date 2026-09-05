---
title: Providers
description: Supported operations, adapter behavior and verification status.
---

The unreleased working tree contains four provider adapters.

## Support matrix

| Adapter | Rating | Multi-parcel path | Purchase | Cancel/refund | Verification retained in this repository |
|---|---|---|---|---|---|
| Shippo | implemented | FANOUT | implemented | refund request | live rating, test purchase and refund; captured responses |
| Easyship | implemented | FANOUT | implemented | cancellation | captured sandbox rating and label responses; payload tests |
| ShipStation v1 | implemented | FANOUT | implemented; base64 label | void | captured rating; one Stamps.com/USPS `testLabel` response |
| ShipStation v2 | implemented | native `packages[]` | implemented | void | live rating; captured responses; purchase not run live |

"Implemented" describes the adapter code. The final column states what has been
run against a provider. It is not a guarantee that every carrier connected to an
account supports the operation.

Shippo supports native multi-piece rating for some carrier and account
combinations. The current adapter does not use that path; it rates parcels
separately and returns FANOUT rates.

## Configure adapters

The short form accepts credentials:

```python
gateway = z.Gateway(
    shippo="shippo_test_...",
    easyship="sand_...",
    shipstation_v1=("key", "secret"),
    shipstation_v2="key",
)
```

Construct adapters directly for provider-specific options:

```python
from shipzil.providers import EasyshipAdapter, ShipStationV1Adapter

gateway = z.Gateway({
    "easyship-sandbox": EasyshipAdapter(
        "sand_...",
        sandbox=True,
        default_category="fashion",
    ),
    "shipstation-legacy": ShipStationV1Adapter(
        key,
        secret,
        carriers=("stamps_com",),
        test_labels=True,
    ),
})
```

## Provider notes

### Shippo

- Test tokens start with `shippo_test_`; live purchase tests reject other tokens.
- The adapter uses the shipment endpoint for rating and the transaction endpoint
  for purchase.
- Provider messages are preserved in `quote.messages` and normalized to exclusions
  when no rates are returned.
- Customs values are sent as line totals.
- EEI/PFC data is currently transmitted only through this adapter.

### Easyship

- Sandbox keys use the `sand_` prefix and the sandbox host.
- Every parcel needs at least one `Item`.
- Every item needs an explicit `value` and either `category`, `hs_code` or an
  adapter `default_category`.
- Parcel dimensions may be supplied on the parcel or derived from item dimensions
  or stored SKU data.
- Customs values are sent per unit.
- Label purchase requires `company` on both addresses, although rating does not.

### ShipStation v1

- Authentication needs a key and secret.
- Rating requires one request per connected carrier because `carrierCode` is
  mandatory.
- Rate responses do not include currency or delivery estimates.
- Labels are returned as base64 in `Label.label_data`, not as a URL.
- `test_labels=True` sends `testLabel: true`. The retained live evidence covers
  one Stamps.com/USPS test label; other carriers are not verified as no-charge.
- The API has no duty-liability or EEI field in its international options model.
- Customs values are USD line totals.

### ShipStation v2

- Rating accepts native `packages[]` and returns structured per-carrier errors.
- Rating has been run live with production credentials; it is a read-only call.
- Purchase and void are implemented but have not been run live in this repository.
- Customs values are sent per unit.
- `ship_date` is currently transmitted by this adapter only.

## Adding an adapter

Adapter authors import the extension contract from `shipzil.providers`:

```python
from shipzil.providers import Adapter, Capabilities, Quote
```

Implement `rate_single()` and `buy()`, assign a stable `name`, and populate
`Rate.provider` plus `Rate.service_key`. Add `rate_native_multi()` and `void()` only
when supported. See [CONTRIBUTING.md](https://github.com/sameerkumar18/shipzil/blob/main/CONTRIBUTING.md)
for the test and evidence requirements.
