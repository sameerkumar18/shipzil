---
title: Roadmap
description: Known gaps and planned work after 0.1.0.
---

0.1.0 is published. This page lists known gaps; the
[changelog](https://github.com/sameerkumar18/shipzil/blob/main/CHANGELOG.md) lists
implemented changes.

## Next

- Run ShipStation v2 purchase and void against an account approved for safe
  testing. Rating is covered live; those two paths are the largest untested money
  paths in the library.
- Add a live ShipStation v1 `testLabel` purchase test. The retained no-charge
  evidence covers one Stamps.com/USPS label and is not generalised to other
  carriers.
- Populate per-package label output. `Label.parcel_labels` was removed rather than
  shipped permanently empty, and implementing it needs a captured v2 purchase
  response.
- Use Shippo's native multi-piece rating where the carrier and account support it,
  instead of always summing per-parcel quotes.

## Adapter work

### Sendcloud

Sendcloud is the next candidate because it adds another aggregator shape. Before an
adapter is added, verify:

- service and carrier identifiers;
- multi-parcel request and purchase behavior;
- customs value basis and duty fields;
- test credentials and purchase safety;
- label cancellation/refund semantics;
- manifest requirements.

### Direct carriers

Direct USPS, UPS or FedEx adapters would test whether the adapter contract assumes
an aggregator account model. They require separate work on authentication, account
configuration and manifests.

## Later

- tracking normalization across providers;
- manifest and close-out operations;
- address validation;
- connection-pooled optional transports;
- recorded HTTP replay tooling for adapter development;

## Outside this project

shipzil does not provide provider health scoring, automatic service equivalence,
cost optimization or automatic purchase failover. Those decisions need separate
policy and evidence. The Gateway keeps provider-specific code out of an application;
it does not remove the operational need for multiple funded provider accounts.
