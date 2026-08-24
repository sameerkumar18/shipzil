# International shipping

Cross-border is where the providers disagree most, and where a silent mistake has
a legal consequence rather than a cosmetic one. This page covers what you have to
supply, what shipzil derives, and what it refuses to guess.

## The short version

```python
from decimal import Decimal
import shipzil as z

item = z.Item(
    "cotton t-shirt",
    quantity=2,
    weight=z.Weight.of(6, "oz"),   # PER UNIT
    value=Decimal("15"),           # PER UNIT
    hs_code="610910",
    origin_country="US",
)

shipment = z.Shipment(
    sender, recipient,
    (z.Parcel(weight=z.Weight.of(16, "oz"),
              dimensions=z.Dimensions.of(10, 8, 4, "in"),
              items=(item,)),),
    duties_paid_by=z.DutiesPaidBy.SENDER,
)
```

That is enough for shipzil to build a customs declaration on any of the five
providers.

## Rating refuses before purchase fails

Providers will happily rate an international shipment with no customs data and
then reject the purchase. Measured on Shippo:

```
US → CA, fully declared item, no customs sent
  rating   4 rates  (USPS Priority Express Intl, Priority Intl, First Class Intl, DHL)
  purchase LabelPurchaseError: USPS - Customs declaration is required for
           international shipments via the USPS
```

Four perfectly good rates, and you find out at the till. So shipzil raises the
problem at **rating** time as an exclusion:

```python
quote = client.get_rates(shipment_with_no_items)
for e in quote.excluded:
    print(e.code, e.message)
# CUSTOMS_DECLARATION_REQUIRED  easypost needs a customs declaration for a
# cross-border shipment. Give every Item a weight and a value, and an hs_code
# where you have one. Rating would succeed without them and the purchase would
# then fail.
```

A rate that can never be bought is worse than no rate.

## Per unit, not per line

`Item.weight` and `Item.value` are **per unit**. `quantity` multiplies them.

Providers disagree about which figure belongs on a customs line, 3–2:

| Provider | Wants | Their words |
|---|---|---|
| EasyPost | line total | *"Total value (unit value \* quantity)"* |
| Shippo | line total | *"Total value of this item, i.e. quantity \* value per item"* |
| ShipStation v1 | line total | *"The value (in USD) of the line item"* |
| ShipStation v2 | **per unit** | *"The declared value of \*each\* item"* |
| Easyship | **per unit** | *"this value refers to the unit rather than the total"* |

shipzil handles the conversion — each adapter declares its
`customs_value_basis` and takes the matching figure. You always supply per unit.

!!! danger "Why this is on its own page"
    Sending a line total where a provider expects a unit multiplies the declared
    customs value by the quantity. Two shirts at \$15 become \$30 each, \$60 for
    the line. That inflates duty and misstates the shipment to the destination
    authority. shipzil had this wrong on ShipStation v2 until the specifications
    were read directly, so it is documented loudly rather than buried.

## Duty liability

```python
z.Shipment(..., duties_paid_by=z.DutiesPaidBy.SENDER)      # DDP — you pay
z.Shipment(..., duties_paid_by=z.DutiesPaidBy.RECIPIENT)   # DDU — they pay
z.Shipment(...)                                            # UNSPECIFIED (default)
```

`UNSPECIFIED` sends **nothing**, so your account default applies. That is
deliberate: an earlier version hardcoded DDU, which silently made the recipient
liable for duty on every international shipment.

Two things to know:

**Not every provider can express it.** ShipStation v1 has no field, so the choice
comes back as an exclusion rather than being silently dropped:

```python
quote = client.get_rates(shipment)   # v1 adapter, duties_paid_by=SENDER
[e.code for e in quote.excluded]
# [<ExclusionCode.DUTIES_UNSUPPORTED: 'duties_unsupported'>]
```

**DDP filters carriers.** It is not a price modifier. Measured on EasyPost, same
shipment, only `duties_paid_by` differing:

```
SENDER      (DDP)   14 rates
RECIPIENT   (DDU)   18 rates
unspecified         18 rates
```

Four of eighteen services will not carry DDP at all. Expect a shorter list, not
a more expensive one.

## EEI exemptions, and where shipzil stops

US exports need an EEI citation. Below \$2,500 per Schedule B number,
`NOEEI 30.37(a)` applies, and shipzil derives it from the declared value you
already gave it — a derivation from your own data, not a guess.

**Above \$2,500 it refuses.** That case needs an AES filing and an ITN, which
shipzil cannot produce:

```python
quote = client.get_rates(high_value_shipment)
# CUSTOMS_DECLARATION_REQUIRED: declared value 5000 exceeds the $2,500
# NOEEI 30.37(a) threshold, so this export needs an AES filing and an ITN that
# shipzil cannot produce. Set Shipment(eei_exemption=...) with your ITN or the
# correct citation.
```

Override explicitly when you have the filing:

```python
z.Shipment(..., eei_exemption="AES_ITN")
```

The same citation is spelled differently per provider — `NOEEI 30.37(a)` on
EasyPost, `NOEEI_30_37_a` on Shippo — and shipzil renders per provider. You hold
the token form.

## What shipzil does not model yet

Honest list, because these will block real commercial traffic:

| Missing | Impact |
|---|---|
| `tax_identifiers` (VAT, EORI, IOSS) | effectively mandatory for commercial EU traffic |
| USPS six-digit HS code enforcement | required on **every item** for all international commercial shipments since 1 Sept 2025; shipzil treats `hs_code` as optional |
| Third-party duty billing | EasyPost `options.duty_payment`, ShipStation `canada_delivered_duty` |
| Wider incoterms | `FCA`, `DAP`, `eDAP` on Shippo; only DDP/DDU are exposed |
| `eccn` / export control | Shippo `eccn_ear99`, EasyPost `eccn` |
| Commercial invoice controls | `suppress_etd`, `declaration`, `invoice_number` |

The USPS HS-code one is the most likely to bite: shipzil will build a declaration
without an `hs_code` and USPS will reject it at purchase. Supply `hs_code` on
every item for USPS international traffic until shipzil enforces it.

See [Gaps](GAPS.md) for the full per-provider list, and the
[roadmap](roadmap.md) for what is planned.

## Checklist before you ship commercially

- [ ] every `Item` has `weight`, `value`, `hs_code` and `origin_country`
- [ ] `hs_code` is at least six digits if USPS is in the carrier mix
- [ ] `duties_paid_by` is set deliberately, not left to the account default
- [ ] you read `quote.excluded` and not only `quote.rates`
- [ ] declared value above \$2,500 has a real ITN, not the derived exemption
- [ ] if using ShipStation v1, the dashboard customs setting is
      "Leave blank (Enter Manually)" — otherwise your items are overwritten
