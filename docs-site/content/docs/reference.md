---
title: Reference
description: Public Gateway, model, service and transport interfaces.
---

This page covers the caller-facing API. The [generated Python
reference](./api/shipzil/index.md) is produced from source docstrings and also
includes adapter-author interfaces.

```python
import shipzil as z
from shipzil.providers import Adapter, Capabilities, Quote
```

Public dataclasses are frozen. `Rate` and `Label` use keyword-only constructors.

## Gateway

```python
z.Gateway(
    sources=None,
    *,
    fallback=None,
    max_spend=None,
    dry_run=False,
    max_workers=None,
    **credentials,
)
```

| Parameter | Meaning |
|---|---|
| `sources` | `Mapping[str, Adapter]`; caller-defined source name to configured adapter |
| `**credentials` | short form: provider name to credential; mutually exclusive with `sources` |
| `fallback` | sequential source order; `None` calls all eligible sources concurrently |
| `max_spend` | numeric purchase limit in the selected rate's currency; no conversion |
| `dry_run` | return a synthetic label instead of calling a purchase endpoint |
| `max_workers` | worker count per source or parcel executor, not a global request cap |

Credential examples:

```python
z.Gateway(shippo="shippo_test_...", easyship="sand_...")
z.Gateway(shipstation_v1=("key", "secret"))
```

### Methods

```python
gateway.get_rates(
    shipment,
    *,
    providers=None,
    carriers=None,
    services=None,
) -> GatewayQuote

gateway.buy(shipment, rate) -> Label
gateway.void(label) -> bool
```

`providers=` currently matches either source names or adapter names. The three
filters intersect.

`buy()` requires a rate returned by this Gateway and uses `rate.source`.
`void()` uses `label.source`.

## GatewayQuote

```python
quote.rates       # tuple[Rate, ...]
quote.sources     # tuple[SourceResult, ...]
quote.excluded    # tuple[Exclusion, ...]
quote.errors      # tuple[ShipzilError, ...]
quote.messages    # tuple[str, ...]
quote.services    # ServiceMap
quote.cheapest    # Rate | None
quote.fastest     # Rate | None
quote.explain()   # str
```

`bool`, `len`, iteration and integer indexing operate on `rates`.

`cheapest` returns `None` unless every rate has the same non-null currency.
`fastest` ignores rates with no `delivery_days`.

## SourceResult

| Field | Type | Meaning |
|---|---|---|
| `source` | `str` | configured account name |
| `provider` | `str` | adapter name |
| `rates` | `tuple[Rate, ...]` | rates contributed after filtering |
| `excluded` | `tuple[Exclusion, ...]` | exclusions from this source |
| `messages` | `tuple[str, ...]` | provider warnings |
| `via` | `str` | provider operation used for rating |
| `error` | `ShipzilError \| None` | source failure |
| `ok` | `bool` | true when no source error was raised |

## Request models

### Address

Required: `street1`, `city`, `postal_code`.

Optional: `country="US"`, `state`, `street2`, `street3`, `name`, `company`,
`phone`, `email`, `address_class`.

`address_class` is `UNKNOWN`, `RESIDENTIAL`, `COMMERCIAL`, `PO_BOX` or
`MILITARY`. Providers that accept only a residential boolean receive no value
when the class is unknown.

### Item

| Field | Meaning |
|---|---|
| `description` | item description |
| `quantity` | integer, at least 1 |
| `weight` | per-unit `Weight` |
| `dimensions` | per-unit `Dimensions`; currently used by Easyship |
| `value` | per-unit `Decimal` customs value |
| `currency` | customs currency, default `USD` |
| `sku` | provider SKU lookup key |
| `hs_code` | caller-supplied HS/Schedule B code; shipzil does not validate it |
| `category` | provider category; currently used by Easyship |
| `origin_country` | caller-supplied ISO alpha-2 origin |

Every cross-border item needs weight and value. Easyship requires at least one
item on domestic requests and requires an explicit value plus category or HS code.

### Parcel

Required: a `weight` or items from which weight can be derived.

| Field | Meaning |
|---|---|
| `weight` | package weight |
| `dimensions` | package dimensions |
| `items` | `tuple[Item, ...]` |
| `packaging` | provider packaging token; `provider` metadata is not currently enforced |
| `dangerous_goods` | `DangerousGoods \| None` |
| `insured_value` | numeric amount; currently sent by Shippo and ShipStation v2 as USD |
| `reference` | retained locally; current adapters do not transmit it |

### Shipment

| Field | Meaning |
|---|---|
| `from_address` | origin `Address` |
| `to_address` | destination `Address` |
| `parcels` | non-empty `tuple[Parcel, ...]` |
| `duties_paid_by` | `UNSPECIFIED`, `SENDER` or `RECIPIENT` |
| `ship_date` | transmitted by ShipStation v2 only |
| `eei_exemption` | transmitted by Shippo only |
| `reference` | retained locally; current adapters do not transmit it |

## Response models

### Rate

| Field | Meaning |
|---|---|
| `carrier`, `service` | provider display text |
| `provider` | adapter name |
| `source` | configured account name, added by Gateway |
| `service_code` | provider purchase token when separate from display text |
| `service_key` | provider-scoped `ServiceKey`; may be `None` if unaddressable |
| `amount`, `currency` | provider quote; currency is `None` on ShipStation v1 |
| `base_amount`, `surcharges` | provider components when available |
| `delivery_days`, `guaranteed` | optional service estimates |
| `strategy` | `NATIVE` or `FANOUT` |
| `parcel_count` | number of parcels represented |
| `raw` | provider response fragment |

### Label

| Field | Meaning |
|---|---|
| `tracking_number` | first available tracking number |
| `tracking_legs` | tracking legs returned by providers that expose them |
| `label_url` | provider-hosted label URL, or empty string |
| `label_data` | base64 label content, currently ShipStation v1 |
| `carrier`, `service`, `amount`, `currency` | purchase result |
| `provider`, `source` | adapter and configured account |
| `shipment_id` | provider shipment/transaction id |
| `is_test` | `True`, `False` or `None` when undetectable |
| `parcel_labels` | reserved; current adapters do not populate it |
| `raw` | provider response |

### Exclusion

| Field | Meaning |
|---|---|
| `code` | `ExclusionCode` |
| `message` | provider or local explanation |
| `carrier`, `service` | affected identity when known |
| `source` | `provider` for provider output; `shipzil` for local validation/filtering |

See [Errors and exclusions](./errors.md) for all codes.

## ServiceKey

```python
key.provider
key.carrier
key.service
key.packaging
key.slug
key.unqualified
```

`slug` renders `{provider}-{carrier}-{service}[-packaging]`.
`unqualified` removes the provider and is a grouping key only. It does not imply
that services are interchangeable.

`ServiceMap.resolve()` intersects provider, carrier and exact service filters over
services observed in returned rates.

## Units

```python
z.Weight.of(16, "oz")
z.Dimensions.of(10, 8, 4, "in")
```

Supported weight units: `mg`, `g`, `kg`, `oz`, `lb`.

Supported dimension units: `mm`, `cm`, `m`, `in`, `ft`.

Values use `Decimal` internally. `ValueError` is raised for unsupported units or
non-positive dimensions/weights.

## Dangerous goods

`DangerousGoods` stores declarations supplied by the caller. shipzil does not
determine whether a shipment is legally compliant.

`has_core_regulated_fields` checks only for UN number, hazard class and packing
group. It is not a compliance result.

If an adapter cannot transmit populated dangerous-goods fields, the quote includes
`HAZMAT_DETAIL_UNSUPPORTED`.

## Transport

```python
class Transport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse: ...
```

The transport returns `HttpResponse` for every HTTP status. It raises `OSError` for
DNS, connection, reset and timeout failures so shipzil can apply retry and error
mapping. `send()` can be called concurrently and should be thread-safe.

The default `UrllibTransport` is stateless and does not pool connections.

## Adapter constructors

```python
ShippoAdapter(api_token, *, timeout=90, transport=None)

EasyshipAdapter(
    api_key,
    *,
    sandbox=None,
    label_timeout=60,
    poll_interval=2,
    default_category=None,
    timeout=60,
    transport=None,
)

ShipStationV1Adapter(
    api_key,
    api_secret,
    *,
    carriers=None,
    test_labels=True,
    confirmation="none",
    timeout=60,
    transport=None,
)

ShipStationV2Adapter(
    api_key,
    *,
    carrier_ids=None,
    timeout=60,
    transport=None,
)
```

See [Providers](./providers.md) before relying on a provider-specific operation.
