# Errors and exclusions

shipzil distinguishes two very different things:

- an **exception** means the operation did not happen
- an **exclusion** means the operation happened, and something you might have
  expected is missing — with the reason attached

The second one is the unusual part, and it is most of the value.

## Exclusions: why your rate list is short

A short rate list is never silently short. Every carrier or service that dropped
out is reported.

```python
quote = client.get_rates(shipment)

print(f"{len(quote.rates)} rates via {quote.via}")
for e in quote.excluded:
    print(f"  excluded {e.carrier or 'all'}: {e.code.value} — {e.message}")
```

Real output, from a three-parcel shipment on ShipStation v2:

```
7 rate(s) via shipstation_v2:rates (native)
  excluded usps: multipackage_not_supported — carrier 30718 does not support
  multipackage. unable to rate the shipment
```

### Exclusion fields

| Field | Meaning |
|---|---|
| `code` | structured `ExclusionCode` — switch on this |
| `message` | human text, provider prose or shipzil's own |
| `carrier` | which carrier, when the provider says |
| `service` | which service, when the provider says |
| `source` | `"shipzil"` if shipzil raised it, otherwise the provider |

`source` matters: it tells you whether the provider refused, or shipzil refused on
your behalf before asking.

### Every exclusion code

| Code | Cause | What to do |
|---|---|---|
| `MULTIPACKAGE_NOT_SUPPORTED` | carrier cannot rate this many parcels | use a different carrier, or split |
| `SERVICE_UNAVAILABLE` | no service for this lane or parcel | check the lane, weight and dimensions |
| `CARRIER_ACCOUNT_MISCONFIGURED` | account not set up for this | fix it in the provider dashboard |
| `DIMENSIONS_REQUIRED` | provider cannot derive a box | supply `Parcel.dimensions` |
| `ITEM_CLASSIFICATION_REQUIRED` | provider needs item detail even domestically | supply `Item` with `hs_code` or category (Easyship) |
| `CUSTOMS_DECLARATION_REQUIRED` | cross-border with nothing declarable, or above the EEI threshold | see [International](international.md) |
| `DUTIES_UNSUPPORTED` | `duties_paid_by` set, provider has no field | use another provider, or set the account default |
| `HAZMAT_DETAIL_UNSUPPORTED` | declared hazmat detail this provider will drop | use another provider, or file outside shipzil |
| `ADDRESS_UNSUPPORTED` | address class not supported | check residential/PO box handling |
| `RATE_LIMITED` | throttled | back off and retry |
| `UNKNOWN` | provider prose that did not map | read `message`; please open an issue |

!!! note "`UNKNOWN` is honest, not lazy"
    Provider failures arrive as prose, and shipzil maps what it recognises.
    Anything it does not recognise becomes `UNKNOWN` with the original text
    preserved, rather than being forced into a category that might be wrong.

## Exceptions

All inherit `ShipzilError`, so one `except` clause catches everything from the
library.

```python
import shipzil as z

try:
    label = client.buy(shipment, rate)
except z.AmbiguousPurchaseError:
    raise                       # never retry — see below
except z.SpendLimitExceeded as e:
    ...                         # your guardrail fired; nothing was bought
except z.LabelPurchaseError as e:
    ...                         # purchase failed, nothing was bought
except z.ShipzilError as e:
    ...                         # anything else
```

| Exception | Meaning |
|---|---|
| `ShipzilError` | base for everything |
| `ConfigurationError` | missing credentials, contradictory options |
| `AuthenticationError` | credentials rejected |
| `ValidationError` | request malformed or incomplete, per the provider |
| `CapabilityError` | provider cannot do this and emulation was impossible |
| `RateLimitError` | throttled |
| `ProviderError` | provider error not modelled more specifically |
| `LabelPurchaseError` | a buy failed |
| `AmbiguousPurchaseError` | a buy **may** have succeeded — read below |
| `SpendLimitExceeded` | `max_spend` stopped a purchase before it happened |

### `AmbiguousPurchaseError`

The one that needs care.

A purchase can fail in a way that leaves you unable to tell whether postage was
bought — a timeout after the request was accepted, for instance. shipzil raises
`AmbiguousPurchaseError` rather than pretending it knows.

!!! danger "Treat it as 'may have succeeded'"
    Do **not** retry. Only one of the four providers publishes an idempotency key,
    so a retry can buy postage twice with no way to link the two attempts.
    Reconcile against the provider — list recent shipments or labels — before
    attempting anything again.

This is also why shipzil sends purchases with retries disabled, while rate
requests are retried normally. Rating is safe to repeat; buying is not.

## Rate limiting is not always a 429

Two provider behaviours worth knowing, both handled:

**Shippo reports throttling as a message on a `201`.** The request succeeds, the
rate list is empty, and the reason is prose:

```
HTTP 201, status: SUCCESS, rates: []
messages: ["UPS - Hard: Too Many Requests"]
```

shipzil parses that into a `RATE_LIMITED` exclusion. If you only checked the
status code you would see a successful call with no rates and no explanation.

**Easyship reports quota exhaustion as `403 Forbidden`**, with *"API usage limit
exceeded"*. That reads like a rejected credential and would send you off to rotate
a key that was never the problem. shipzil matches on the message and raises
`RateLimitError`, not `AuthenticationError`.

## Retry behaviour

| Operation | Retried | Why |
|---|---|---|
| rating | yes, with backoff | safe to repeat |
| tracking, reads | yes | safe to repeat |
| **purchase** | **no** | a repeat may buy postage twice |
| void | no | not idempotent across providers |

`Retry-After` is honoured when the provider sets it, capped at 30 seconds;
otherwise exponential backoff.

## A pattern that works

```python
quote = client.get_rates(shipment)

if not quote.rates:
    # Never just report "no rates". The reason is already in hand.
    for e in quote.excluded:
        log.warning("no rate: %s (%s) — %s", e.code.value, e.source, e.message)
    for m in quote.messages:
        log.warning("provider said: %s", m)
    raise NoShippingOption(quote)

# Blocking problems that are not per-carrier are worth surfacing even when
# rates did come back.
blocking = {
    z.ExclusionCode.DUTIES_UNSUPPORTED,
    z.ExclusionCode.HAZMAT_DETAIL_UNSUPPORTED,
}
for e in quote.excluded:
    if e.code in blocking:
        log.error("declared detail will not reach the carrier: %s", e.message)
```
