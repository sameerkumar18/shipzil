---
title: International shipping
description: Customs values, duties, EEI handling and current adapter limits.
---

Cross-border requests need item data that domestic requests may not require.
shipzil blocks rating before the network call when any cross-border item lacks
weight or value.

## Item data

```python
from decimal import Decimal

item = z.Item(
    description="cotton t-shirt",
    quantity=2,
    weight=z.Weight.of(6, "oz"),    # per unit
    value=Decimal("15.00"),          # per unit
    currency="USD",
    hs_code="610910",
    origin_country="US",
)

parcel = z.Parcel(
    weight=z.Weight.of(16, "oz"),
    dimensions=z.Dimensions.of(10, 8, 4, "in"),
    items=(item,),
)
```

`value` and `weight` are per-unit values. `quantity` is applied by the adapter when
the provider expects a line total.

Every cross-border item needs `weight` and `value`. HS code and origin requirements
vary by provider and carrier. Supply both when known; shipzil does not validate HS
code length or tariff eligibility.

Easyship also requires at least one item on domestic requests. Each Easyship item
needs an explicit value and either `category`, `hs_code` or an adapter
`default_category`. shipzil does not create placeholder items or default values.

## Per-unit and line-total conversion

Provider schemas disagree about the meaning of a customs line value:

| Adapter | Value sent | Provider wording retained in local evidence |
|---|---|---|
| Shippo | line total | "Total value of this item, i.e. quantity * value per item" |
| ShipStation v1 | line total, USD | "The value (in USD) of the line item" |
| ShipStation v2 | per unit | "The declared value of each item" |
| Easyship | per unit | "this value refers to the unit rather than the total" |

The adapter performs this conversion. Callers always provide per-unit values.

ShipStation v1 has no customs currency field. The adapter sends numeric values to
an API whose model documents them as USD. Do not use non-USD item values with that
adapter.

## Duties

```python
z.Shipment(..., duties_paid_by=z.DutiesPaidBy.SENDER)      # DDP
z.Shipment(..., duties_paid_by=z.DutiesPaidBy.RECIPIENT)   # DDU/DAP
z.Shipment(...)                                            # UNSPECIFIED
```

`UNSPECIFIED` omits the field. The resulting behavior is provider-specific:

- Easyship documents omitted/null incoterms as DDU.
- Other providers may use account or carrier defaults.
- ShipStation v1 has no duty-liability field. Setting `duties_paid_by` returns a
  `DUTIES_UNSUPPORTED` exclusion while leaving rates available.

Some providers remove carriers or services when DDP is requested. Inspect
`quote.excluded` even when rates were returned.

## EEI and US exports

EEI handling is currently limited:

- Only the Shippo adapter transmits `eei_exemption`.
- For a US-origin shipment, shipzil derives `NOEEI_30_37_a` only when the total
  declared shipment value is at most USD 2,500.
- The regulation applies per Schedule B number. Using the total is conservative:
  it can block a shipment whose separate Schedule B groups are each below the
  threshold.
- For non-US origins, shipzil does not derive a US exemption.
- An explicit EEI value on another adapter produces an exclusion because that
  adapter does not transmit it.

The current model does not hold a complete AES filing or ITN. Do not treat the
string `"AES_ITN"` by itself as proof that a filing exists. If a shipment requires
AES, complete the filing outside shipzil and verify the provider-specific payload
before purchase.

## Provider notes

### Shippo

- Receives shipment-level customs lines and `eel_pfc`.
- Receives line-total values and weights.
- Some international services require provider-specific HS and origin detail that
  shipzil does not validate.

### Easyship

- Requires an item category or HS code on every item.
- Requires explicit item values even on domestic requests.
- Sends values and weights per unit.
- Omitted incoterms default to DDU according to the retained schema.

### ShipStation v1

- Customs data is sent during label creation, not the rating call.
- Customs value is documented as a USD line-item value.
- The API model has no incoterm, DDP/DDU or EEI field.

### ShipStation v2

- Product data is attached per package.
- Values and weights are sent per unit.
- Duty terms are sent through the shipment customs block.
- EEI is not currently transmitted by this adapter.

## Pre-purchase checks

Before buying an international label:

- confirm every item has weight, value, currency, description and origin;
- validate HS/Schedule B codes outside shipzil;
- inspect `quote.excluded` for duty, customs and dangerous-goods gaps;
- confirm the provider received the intended incoterm and EEI data;
- avoid ShipStation v1 for non-USD customs values.
