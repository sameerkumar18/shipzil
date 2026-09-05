# shipzil build plan

The unreleased working tree is a Python Gateway. Provider health scoring and
automatic routing are outside its current scope.

## Current product

Supported provider surfaces:

- Shippo
- Easyship
- ShipStation v1
- ShipStation v2

The Gateway will query configured sources, resolve explicit provider/carrier/
service filters, return partial results with diagnostics, and preserve the source
needed to buy a selected rate.

No health scoring, cheapest selection, service equivalence or automatic purchase
fallback belongs in this release.

## Build order

1. `ServiceKey`, `ServiceMap` and deterministic selection resolution
2. `Gateway` with all-sources default and explicit fallback configuration
3. Source provenance on rates and labels
4. Multi-parcel service-key propagation
5. Human-facing Gateway documentation
6. Sendcloud adapter research and implementation
7. Direct carrier adapter research, starting with USPS or FedEx

## Selection rules

- No provider preference: query all configured sources.
- Explicit fallback order: try sources in that order and stop at the first source
  returning a matching rate.
- Provider, carrier and exact service filters intersect.
- Contradictory configuration fails before network calls.
- One provider failure does not erase rates returned by another provider.
- Buying always uses the exact source that returned the rate.

## Scope fence

This release does not build an intelligence layer. It does not choose the cheapest
rate, infer service equivalence, consume carrier health, generate manifests, or
automatically retry an ambiguous purchase through another source.

The Gateway is valuable on its own: it removes provider-specific code and lets a
merchant add a second provider without rewriting their shipping integration.
