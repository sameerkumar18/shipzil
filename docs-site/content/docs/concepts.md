---
title: Concepts
description: Sources, filters, service identity, multi-parcel rating and purchase routing.
---

## Provider, adapter and source

- A **provider** is Shippo, Easyship, ShipStation v1 or ShipStation v2.
- An **adapter** translates between shipzil models and one provider API.
- A **source** is one configured provider account. Source names are chosen by the
  caller.

Two sources can use the same adapter:

```python
gateway = z.Gateway({
    "shippo-us": ShippoAdapter(us_token),
    "shippo-eu": ShippoAdapter(eu_token),
})
```

Rates and labels retain `source`, so a purchase can return to the account that
produced the rate.

## Source selection

With no `fallback`, all eligible sources are called concurrently:

```python
gateway = z.Gateway(shippo=shippo_token, shipstation_v2=shipstation_key)
```

Results are assembled in configured-source order. Completion timing does not
change the order.

An explicit fallback is sequential:

```python
gateway = z.Gateway(
    {"primary": primary_adapter, "backup": backup_adapter},
    fallback=("primary", "backup"),
)
```

The first source with a matching rate wins. Lower-ranked sources are not called
after that. This is caller-defined policy; shipzil does not evaluate provider
health.

`providers=` currently matches either a source name or an adapter name. A separate
`sources=` filter is planned because the current behavior is ambiguous when those
names differ.

## Filters

`providers`, `carriers` and `services` use AND semantics:

```python
quote = gateway.get_rates(
    shipment,
    providers={"shippo"},
    carriers={"usps"},
    services={"shippo-usps-usps_priority"},
)
```

Rates removed by shipzil filters appear in `quote.excluded` with
`FILTERED_BY_REQUEST`. An unaddressable rate appears as
`SERVICE_NOT_ADDRESSABLE`. Provider exclusions are retained when the provider
reports them.

## Service identity

`Rate.service_key` identifies one provider's service:

```text
shippo-usps-usps_ground_advantage
shipstation_v1-usps-usps_ground_advantage-thick_envelope
```

The key contains:

```python
key.provider
key.carrier
key.service
key.packaging
```

Carrier names are normalized for filtering. Provider service tokens are preserved.
Matching carrier and service text across two providers does not prove equivalent
delivery behavior, packaging rules, duties support or price guarantees.

`Rate.carrier` and `Rate.service` remain the provider's display text. Use
`service_key` for machine identity.

## Multi-parcel rating

`Shipment.parcels` is always a tuple.

`Rate.strategy` records how a multi-parcel amount was produced:

| Strategy | Meaning |
|---|---|
| `NATIVE` | one provider request rated the full shipment |
| `FANOUT` | shipzil rated each parcel separately and summed matching services |

ShipStation v2 uses native `packages[]` rating. The current Shippo, Easyship and
ShipStation v1 adapters use FANOUT. Shippo itself supports native multi-piece
rating for some carrier and account combinations; this adapter does not use that
path yet.

A FANOUT rate is returned only when the same `ServiceKey` covered every parcel.
It cannot be bought as one label because the amount was assembled from separate
provider quotes.

## GatewayQuote

`GatewayQuote` implements `bool`, `len`, iteration and indexing over `rates`.

```python
if quote:
    first = quote[0]

for rate in quote:
    ...
```

Other fields:

| Field | Meaning |
|---|---|
| `sources` | one `SourceResult` per source attempted |
| `errors` | source-level exceptions |
| `excluded` | local and provider exclusions |
| `messages` | provider warning messages |
| `services` | service keys observed in returned rates |
| `cheapest` | lowest amount when all rates share one known currency |
| `fastest` | lowest reported `delivery_days` |

## Purchase routing

`Gateway.buy(shipment, rate)` uses `rate.source`. It rejects a rate with no source
or with a provider that does not match the configured source.

Purchases are not retried or redirected. A transport or provider error after
dispatch raises `AmbiguousPurchaseError` because the request may have succeeded.

## Transport

Adapters use a synchronous `Transport` protocol. The default is stdlib
`UrllibTransport`. A custom transport can add logging, proxies, connection pooling
or recorded replay. Its `send()` method may be called concurrently and should be
thread-safe. Transport failures must be raised as `OSError` for shipzil's retry and
error mapping to apply.

See [Reference](./reference.md#transport) for the protocol.
