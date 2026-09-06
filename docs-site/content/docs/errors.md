---
title: Errors and exclusions
description: Partial rating failures, validation errors and purchase safety.
---

shipzil reports three kinds of failure:

1. A source-level `ShipzilError` in `quote.errors`.
2. An `Exclusion` for a carrier, service or local preflight rule.
3. A synchronous exception from configuration, model construction or purchase.

## Partial rating failures

`Gateway.get_rates()` catches `ShipzilError` from each source. Successful sources
still contribute rates:

```python
quote = gateway.get_rates(shipment)

for error in quote.errors:
    log.warning("rating source failed: %s", error)

if not quote:
    raise NoShippingOption(quote.explain())
```

Errors that are not `ShipzilError` propagate. They indicate a programming error in
shipzil or a custom adapter and are not converted into provider downtime.

## Exclusions

```python
for exclusion in quote.excluded:
    print(exclusion.code, exclusion.source, exclusion.message)
```

| Field | Meaning |
|---|---|
| `code` | normalized `ExclusionCode` |
| `message` | provider text or shipzil preflight explanation |
| `carrier` | affected carrier when known |
| `service` | affected service when known |
| `source` | `provider` when the failure came from provider output; `shipzil` for local validation or filtering |

The code may be inferred from provider prose. `source` records where the failure
originated, not whether the normalized code was structured or inferred.

shipzil reports rates it removes through local carrier or service filters, and
retains exclusions supplied by providers.

It also reports a rate list shortened by a transient carrier failure. A provider
can return some rates while one carrier is rate limited, which would otherwise look
like a normal result. That case arrives as `RATE_LIMITED` alongside the rates that
did come back:

```python
quote = gateway.get_rates(shipment)

if any(e.code is z.ExclusionCode.RATE_LIMITED for e in quote.excluded):
    # Fewer services than usual. Retrying later may return more.
    log.info("rate list may be incomplete: %s", quote.explain())
```

Permanent account and lane messages are not promoted to exclusions, because they
are true on every call for that account and would bury the transient case. They
remain available in `quote.messages`.

shipzil cannot report a service a provider omits with no message at all. Detecting
that needs a baseline of what the account usually returns.

### Exclusion codes

| Code | Meaning |
|---|---|
| `MULTIPACKAGE_NOT_SUPPORTED` | carrier cannot rate the full parcel set |
| `SERVICE_UNAVAILABLE` | no service was available for the request |
| `CARRIER_ACCOUNT_MISCONFIGURED` | provider account or carrier connection needs configuration |
| `DIMENSIONS_REQUIRED` | provider needs dimensions that were not supplied |
| `ITEM_CLASSIFICATION_REQUIRED` | provider needs item category or HS classification |
| `ADDRESS_UNSUPPORTED` | address or lane is unsupported |
| `CUSTOMS_DECLARATION_REQUIRED` | cross-border item data, EEI data or provider support is missing |
| `DUTIES_UNSUPPORTED` | selected duty liability is not transmitted by this adapter |
| `HAZMAT_DETAIL_UNSUPPORTED` | declared dangerous-goods fields would be dropped |
| `RATE_LIMITED` | provider throttled the request |
| `FILTERED_BY_REQUEST` | caller filter removed the rate |
| `SERVICE_NOT_ADDRESSABLE` | provider rate could not be assigned a `ServiceKey` |
| `UNKNOWN` | provider text did not match a known code |

`UNKNOWN` retains the original message. Branch on the code for known cases and log
the message for diagnosis.

## Exceptions

Provider, Gateway and purchase failures inherit `ShipzilError`:

| Exception | Meaning |
|---|---|
| `ConfigurationError` | missing credentials, unknown source or conflicting options |
| `AuthenticationError` | provider rejected credentials |
| `ValidationError` | provider rejected the request data |
| `CapabilityError` | requested operation cannot be performed or emulated |
| `RateLimitError` | provider throttled or exhausted quota |
| `ProviderError` | network/provider failure not classified more specifically |
| `LabelPurchaseError` | provider returned a known purchase failure |
| `AmbiguousPurchaseError` | purchase request may have succeeded |
| `SpendLimitExceeded` | local `max_spend` check stopped the purchase |

Invalid local model values, units and service-key strings raise `ValueError`. They
do not inherit `ShipzilError`.

## Purchase safety

Purchases are sent once. They are not automatically retried or redirected to a
different source.

A `ProviderError` raised after `Gateway.buy()` dispatches the request is converted
to `AmbiguousPurchaseError`. Easyship also raises it when a confirmed shipment does
not settle before the polling deadline.

```python
try:
    label = gateway.buy(shipment, rate)
except z.AmbiguousPurchaseError as error:
    log.error("purchase outcome unknown: %s", error)
    # Reconcile recent labels/shipments with rate.source before another attempt.
    raise
except z.LabelPurchaseError as error:
    log.error("provider reported purchase failure: %s", error)
```

No supported purchase path documents a caller-supplied idempotency key. A second
request can buy postage twice.

## Rate limits

Provider status codes are not uniform:

- Shippo can return HTTP 201 with no rates and a message containing "Too Many
  Requests". shipzil turns that message into a `RATE_LIMITED` exclusion.
- Easyship can return HTTP 403 with "API usage limit exceeded". shipzil raises
  `RateLimitError`, not `AuthenticationError`.

Safe rating and read requests retry 429, 502, 503 and 504 responses with backoff.
`Retry-After` is honored and capped at 30 seconds. Purchase, cancel and refund
requests pass `retries=0`.

## Empty result example

```python
quote = gateway.get_rates(shipment)

if not quote:
    for error in quote.errors:
        log.warning("source failed: %s", error)
    for exclusion in quote.excluded:
        log.warning(
            "excluded %s: %s",
            exclusion.code.value,
            exclusion.message,
        )
    for message in quote.messages:
        log.warning("provider warning: %s", message)
    raise NoShippingOption(quote.explain())
```
