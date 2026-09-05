# Agent guide

This repository is the Python shipping Gateway, not the separate Shipzil status
page repository.

## Current scope

Supported provider adapters:

- `shippo`
- `easyship`
- `shipstation_v1`
- `shipstation_v2`

`Gateway` sits above the existing single-provider `Client`:

- no provider preference means query all configured sources
- an explicit `fallback=(...)` is caller-authored policy
- provider, carrier and exact `ServiceKey` filters intersect
- source failures remain in `GatewayQuote.sources`
- every Gateway rate carries its configured `source`
- buying and voiding use that exact source

Do not add health-aware routing, cheapest-rate selection, service equivalence,
manifest generation or automatic purchase fallback. Those are not Gateway MVP
features.

## Code map

| Area | File | Role |
|---|---|---|
| internal source client | `shipzil/_client.py` | rate, buy, void for one adapter |
| Gateway | `shipzil/gateway.py` | multi-source aggregation and explicit filters |
| service map | `shipzil/services.py` | `ServiceKey`, `ServiceInfo`, `ServiceMap` |
| provider contract | `shipzil/providers/base.py` | adapter capabilities and fidelity gaps |
| provider adapters | `shipzil/providers/` | provider-specific requests/parsers |
| provider errors | `shipzil/normalize.py` | prose/code to `ExclusionCode` |
| live-shaped fixtures | `tests/fixtures/` | scrubbed provider responses |
| provider source guide | `docs-site/content/docs/provider-sources.md` | authoritative documentation URLs and verification rules |

## Invariants

- `Item.value` and `Item.weight` are per unit.
- Provider machine keys go into `ServiceKey.service`; display names stay on
  `Rate.service`.
- `ServiceKey` is provider-namespaced. Do not infer that two providers' keys are
  equivalent.
- `Rate.source` is the configured account/source that produced the rate. Never
  purchase through another source.
- `Quote.excluded` retains provider-reported and local exclusions. It cannot explain
  services the provider silently omitted.
- Purchases are never retried automatically.
- A fan-out rate is an estimate, not one provider-native multi-parcel quote, and
  cannot be bought as one label.
- Do not add a provider capability to `hazmat_fields` unless the adapter emits
  that field on the wire.

## Verification

```bash
make check
make check-compat
make docs-build
make test-live
```

`make test-live` loads the ignored `.env`. Live calls can buy test labels or
consume sandbox quota; inspect the marker and provider before running them.

When changing a provider parser, use a sanitized provider response plus a
payload-level test. A constructed fixture is appropriate for an edge case absent
from the captured response.

## Provider documentation

Read `docs-site/content/docs/provider-sources.md` before changing provider
behavior. Verify claims against the provider's current schema or documentation and
then add a captured-response or payload-level test. Never commit `.env`, raw
credentials or unsanitized provider payloads.

## Style

Keep the public API small and Pythonic. Prefer a dataclass, tuple, mapping or
keyword argument over a hierarchy of policy/configuration classes. Use
`dataclasses.replace` when copying a model so new fields cannot silently vanish.
