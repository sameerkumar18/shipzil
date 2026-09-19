---
title: Evidence
description: What has been verified live, captured from provider responses or read from specifications.
---

Provider behavior changes. This page distinguishes three evidence levels:

- **Live**: exercised against a provider during the current test run.
- **Captured**: parser and payload tests use a sanitized provider response retained
  in `tests/fixtures`.
- **Specification**: implemented from the provider's current schema or model page.

## Current evidence

| Adapter | Rating | Purchase | Cancel/refund | Customs basis |
|---|---|---|---|---|
| Shippo | live and captured | live test purchase | live refund request | specification: line total |
| Easyship | **live sandbox** and captured | captured sandbox label response | payload/response tests | specification: per unit |
| ShipStation v1 | **live** and captured | captured `testLabel` response | payload/response tests | specification: USD line total |
| ShipStation v2 | live and captured | implementation and captured schema; not run live | implementation and captured schema; not run live | specification: per unit |

Two-source rating has been run live with Shippo and ShipStation v2. The test checks
that both sources contribute rates and that each rate retains matching source and
provider provenance. It does not assert a rate count because provider results vary
between runs.

## Specification sources

- [Shippo OpenAPI](https://docs.goshippo.com/spec/shippoapi/public-api.yaml)
- [ShipStation v2 / ShipEngine documentation](https://docs.shipstation.com/apis/shipengine/)
- [ShipEngine OpenAPI repository](https://github.com/ShipEngine/shipengine-openapi)
- [ShipStation v1 Customs Item model](https://www.shipstation.com/docs/api/models/customs-item/)
- [ShipStation v1 International Options model](https://www.shipstation.com/docs/api/models/international-options/)
- [Easyship documentation index](https://developers.easyship.com/llms.txt)

The local source cache is gitignored and is not part of the published evidence.
Public claims in these docs link to provider-owned pages; tests retain sanitized
response structure without credentials or contact details.

## Known evidence gaps

- ShipStation v2 purchase and void have not been run live in this repository.
- Easyship and ShipStation v1 purchase paths have no live test. Their rating paths
  are now exercised live; purchases remain captured and specification based.
- Shippo's test environment is inconsistent about refunds. It has returned both an
  accepted refund and HTTP 201 carrying `status: "ERROR"` with no reason, so the
  live test accepts either and requires the refusal to be attributable.
- ShipStation v1 `testLabel` no-charge behavior was observed for one
  Stamps.com/USPS label. It is not generalized to every connected carrier.
- Provider omissions cannot be distinguished from unsupported services unless the
  provider reports an error or exclusion.
