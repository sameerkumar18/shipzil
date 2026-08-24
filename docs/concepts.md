# Concepts

shipzil has a small object model. Everything below is a frozen dataclass, so a
`Shipment` you built cannot be mutated by the library underneath you.

## The shape of a request

```
Shipment
├── from_address : Address
├── to_address   : Address
├── parcels      : tuple[Parcel, ...]        # always a tuple, even at one
│   └── Parcel
│       ├── weight          : Weight | None
│       ├── dimensions      : Dimensions | None
│       ├── items           : tuple[Item, ...]      # customs lines
│       ├── packaging       : PackagingTemplate | None
│       └── dangerous_goods : DangerousGoods | None
├── duties_paid_by : DutiesPaidBy            # SENDER / RECIPIENT / UNSPECIFIED
├── eei_exemption  : str | None              # override the derived citation
├── ship_date      : str | None
└── reference      : str | None
```

## Units convert once, at the boundary

`Weight` and `Dimensions` carry their unit. You supply whatever you have; each
adapter converts to what its provider wants — ounces for EasyPost, kilograms for
Easyship, and so on.

```python
z.Weight.of(16, "oz")
z.Weight.of(1, "lb")            # same thing
z.Dimensions.of(10, 8, 4, "in")
```

This is deliberately not a float. Weights and money are `Decimal`, because a
rounding error in a declared customs value is a compliance problem.

## Per unit versus line total

`Item.weight` and `Item.value` are **per unit**. `quantity` multiplies them.

```python
z.Item("t-shirt", quantity=2, weight=z.Weight.of(6, "oz"), value=Decimal("15"))
# per unit:   6 oz, $15
# line total: 12 oz, $30
```

Which of those a provider wants on a customs declaration is not uniform, so
shipzil carries both and each adapter takes the one its provider documents. This
matters enough to have its own page: [International shipping](international.md).

## The shape of a response

```
Quote
├── rates    : tuple[Rate, ...]
├── excluded : tuple[Exclusion, ...]     # why something is missing
├── via      : str                       # "easypost:orders", "shippo:fanoutx3"
├── strategy : Strategy
└── messages : tuple[str, ...]           # unmapped provider prose
```

A short rate list is never silently short. If a carrier dropped out there is an
`Exclusion` saying so — see [Errors and exclusions](errors.md).

### Strategy

`Strategy` tells you how a quote was produced. It exists because these three are
genuinely not the same thing, and treating them as interchangeable is how
multi-parcel bugs happen.

| Value | Meaning | Trust level |
|---|---|---|
| `NATIVE` | the provider rated the whole shipment in one call | highest |
| `ORDER` | the provider has a separate multi-parcel resource (EasyPost `/orders`) | high |
| `FANOUT` | shipzil rated each parcel separately and summed | **read the caveat** |

!!! warning "What a FANOUT rate is and is not"
    A combined rate is the sum of per-parcel rates. It is a good estimate and it
    is what the provider would charge for those parcels bought separately. It is
    **not** a multi-parcel contract rate, and it will miss any discount a carrier
    applies to a consolidated shipment. `strategy` is on the `Rate` as well as
    the `Quote` so this cannot be lost when you pass rates around.

    A combined rate is only offered when one carrier covered **every** parcel.
    Partial coverage becomes an exclusion, not a partial quote.

### Rate

```python
rate.carrier        # "USPS"           — provider's spelling, not canonical yet
rate.service        # "GroundAdvantage" — likewise
rate.amount         # Decimal
rate.provider       # "easypost"       — who quoted this, and who must sell it
rate.service_code   # provider's machine code, where it has one
rate.strategy       # NATIVE / ORDER / FANOUT
rate.parcel_count   # how many parcels this covers
rate.surcharges     # itemised, where the provider breaks them out
rate.raw            # the provider's own payload, as an escape hatch
```

!!! note "`carrier` and `service` are not canonical across providers"
    The same USPS service is `GroundAdvantage` on EasyPost, `Ground Advantage` on
    Shippo, `USPS Ground Advantage` on ShipStation v2 and
    `usps_ground_advantage` on v1 — which also returns two rates sharing that
    code, differing only by packaging. Canonical service identity is on the
    [roadmap](roadmap.md#a1-canonical-carrier-and-service-identity); until then,
    do not compare `rate.service` strings across providers.

### Label

```python
label.tracking_number   # primary tracking number
label.tracking_legs     # every leg, for providers that expose more than one
label.parcel_labels     # one entry per parcel on a multi-parcel purchase
label.label_url         # provider-hosted PDF/PNG/ZPL
label.is_test           # True/False where knowable, None where the provider
                        # gives no hint at all
```

## Guardrails

Two, both enforced **before** any network call, so hitting one costs nothing.

```python
client = z.Client(adapter, max_spend="25.00", dry_run=True)
```

- `max_spend` raises `SpendLimitExceeded` rather than buying.
- `dry_run` returns a synthetic label and never contacts the provider's purchase
  endpoint.

## What shipzil will not do

It refuses rather than guessing. The clearest case: a US export above the
$2,500 EEI threshold needs an AES filing and an ITN, which shipzil cannot
produce, so it declines to quote instead of filing a false exemption. You can
override with an explicit citation:

```python
z.Shipment(..., eei_exemption="AES_ITN")
```

The same principle applies to hazmat classification and to anything else where
the correct value is a regulatory decision rather than a data transformation.
