---
title: Roadmap
description: Remaining work before and after the first public release.
---

The current code is an unreleased working tree. This page lists known gaps; the
[changelog](https://github.com/sameerkumar18/shipzil/blob/main/CHANGELOG.md) lists
implemented changes.

## Before the first public tag

- Publish the repository and create a tag that matches `shipzil.__version__`.
- Replace the temporary development install instructions with a real pinned tag.

- Run ShipStation v2 purchase and void with an account approved for safe testing.
- Add a live ShipStation v1 `testLabel` purchase test. Rating is now covered live
  for all four adapters; only v1 purchase and v2 purchase remain unexercised.

- Populate per-package label output once a ShipStation v2 purchase has been run
  live. `Label.parcel_labels` was removed rather than shipped always empty.

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
- publishing to PyPI so other published Python packages can depend on shipzil.

## Outside this project

shipzil does not provide provider health scoring, automatic service equivalence,
cost optimization or automatic purchase failover. Those decisions need separate
policy and evidence. The Gateway keeps provider-specific code out of an application;
it does not remove the operational need for multiple funded provider accounts.
