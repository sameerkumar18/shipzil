---
title: shipzil
description: Documentation for the MIT-licensed multi-provider shipping library.
---

**OpenRouter for Shipping.** shipzil is a fully open-source, MIT-licensed Python
library for using Shippo, ShipStation and Easyship through one request and response
model. It runs inside your application and calls providers with your credentials.
There is no hosted shipzil service or per-label fee.

The phrase describes the product category; shipzil is not affiliated with
OpenRouter.

!!! warning "Alpha, and not yet tagged"
    No release tag or PyPI package exists yet, so pin a commit rather than a
    version. The interface may change before the first release.

## Start here

The [Quickstart](./quickstart.md) covers configuration, rating and purchase.

```python
import shipzil as z

gateway = z.Gateway(
    shipstation_v2="...",
    shippo="shippo_test_...",
)

quote = gateway.get_rates(shipment)

rate = quote.cheapest
if rate is None:
    raise NoShippingOption(quote.explain())

label = gateway.buy(shipment, rate)
```

`cheapest` is available only when every returned rate has the same known
currency. Otherwise select a rate using your application's currency and service
policy.

## What the Gateway does

- Calls eligible rating sources concurrently.
- Returns source failures in `quote.errors` without discarding successful rates.
- Records the configured source and provider on every rate.
- Applies provider, carrier and service filters with AND semantics.
- Lists rates removed by local filters in `quote.excluded`.
- Buys through the same configured source that produced the selected rate.

Provider-reported exclusions are retained when the provider supplies them.
shipzil cannot explain a service the provider omits without reporting it.

## Current limits

- `fallback=(...)` is a caller-defined order. shipzil does not score provider
  health or choose a provider.
- Provider-scoped service keys do not establish equivalent service behavior
  across providers.
- Purchases are not retried or redirected. See [Purchase safety](./errors.md).
- `cheapest` does not compare mixed or unknown currencies.
- Cross-border rating requires weight and value on every item.
- EEI data is currently sent only by the Shippo adapter.
- ShipStation v2 purchase and cancellation are implemented but have not been run
  live in this repository.

## Development checkout

From this checkout:

```bash
uv sync
make check
make docs
```

Until the first tag exists, pin an exact commit:

```bash
uv add "shipzil @ git+https://github.com/sameerkumar18/shipzil@<commit-sha>"
```

## Documentation

| Task | Page |
|---|---|
| Configure providers and request a label | [Quickstart](./quickstart.md) |
| Understand sources, filters and FANOUT rates | [Concepts](./concepts.md) |
| Check provider-specific support | [Providers](./providers.md) |
| Build cross-border shipments | [International shipping](./international.md) |
| Handle partial failures and uncertain purchases | [Errors and exclusions](./errors.md) |
| Look up fields and methods | [Reference](./reference.md) |
| Review evidence status | [Evidence](./research.md) |
| See unreleased work and remaining gaps | [Roadmap](./roadmap.md) |

shipzil is licensed under MIT. Commercial use, modification and distribution are
allowed under the terms in the repository's `LICENSE` file.
