---
title: Provider documentation
description: Official sources used when maintaining provider adapters.
---

This page is for adapter maintainers. Public verification status is summarized on
[Evidence](./research.md).

## Shippo

Primary source: [Shippo OpenAPI](https://docs.goshippo.com/spec/shippoapi/public-api.yaml)

Enum values are often composed through separate schemas. Read the full composed
schema rather than only the base field.

## ShipStation v2 / ShipEngine

Primary sources:

- [ShipStation API documentation](https://docs.shipstation.com/apis/shipengine/)
- [ShipEngine OpenAPI repository](https://github.com/ShipEngine/shipengine-openapi)

The repository schema can lag the hosted documentation. Check both before changing
an enum, customs field or purchase request.

## ShipStation v1

Primary sources:

- [Customs Item](https://www.shipstation.com/docs/api/models/customs-item/)
- [International Options](https://www.shipstation.com/docs/api/models/international-options/)
- [Create Label](https://www.shipstation.com/docs/api/shipments/create-label/)
- [Get Rates](https://www.shipstation.com/docs/api/shipments/get-rates/)

Endpoint pages link to separate model pages. Field-level claims must be checked
against the model page, not the endpoint summary.

## Easyship

Primary source: [Easyship documentation index](https://developers.easyship.com/llms.txt)

Reference pages include inline schemas. Check the schema for field nesting,
required values and defaults. Easyship documents omitted/null incoterms as DDU.

## Verification rules

For an adapter change:

1. Confirm the field or behavior in a current provider-owned schema or model page.
2. Add a payload test proving the value reaches the outgoing request.
3. Add a parser test using a sanitized provider response when one is available.
4. Run a live read-only or sandbox call when credentials and provider safety allow.

An HTTP 200 response or a non-empty downloaded file does not prove the requested
documentation was returned. Check page title, endpoint name and expected fields.
