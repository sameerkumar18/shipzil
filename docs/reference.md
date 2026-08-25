# Reference

Everything importable from `shipzil`. All dataclasses are frozen.

```python
import shipzil as z
from shipzil.providers import EasyPostAdapter   # adapters live in a submodule
```

## Client

```python
z.Client(adapter, *, max_spend=None, dry_run=False)
```

| Parameter | Type | Meaning |
|---|---|---|
| `adapter` | `Adapter` | one provider account |
| `max_spend` | `Decimal \| float \| str \| None` | refuse to buy above this, checked before any network call |
| `dry_run` | `bool` | return a synthetic label, never contact the purchase endpoint |

### Methods

| Method | Returns | Notes |
|---|---|---|
| `get_rates(shipment)` | `Quote` | retried with backoff; safe to repeat |
| `buy(shipment, rate)` | `Label` | **never retried** — see [Errors](errors.md#ambiguouspurchaseerror) |
| `void(label)` | `bool` | `True` when the provider confirms the refund |

Guardrails run before any network call, so a `dry_run` or a `max_spend` breach
costs nothing.

## Request objects

### `Address`

| Field | Required | Notes |
|---|---|---|
| `street1` | ✔ | |
| `city` | ✔ | |
| `postal_code` | ✔ | |
| `country` | | ISO 3166-1 alpha-2, defaults to `"US"` |
| `state` | | required by several carriers for US/CA/MX/AU |
| `street2`, `street3` | | `street3` is dropped by providers that lack it |
| `name`, `company` | | at least one is needed by most carriers |
| `phone`, `email` | | required for most international lanes |
| `address_class` | | `AddressClass`, see below |

### `Parcel`

| Field | Required | Notes |
|---|---|---|
| `weight` | | `Weight`. Omit only if items carry weights and the provider can derive |
| `dimensions` | | `Dimensions`. Some providers refuse without it |
| `items` | | `tuple[Item, ...]` — the customs lines for this box |
| `packaging` | | `PackagingTemplate` for carrier-supplied packaging |
| `dangerous_goods` | | `DangerousGoods` |
| `insured_value` | | `Decimal` |
| `reference` | | your own string |

### `Item`

A customs line. **`weight` and `value` are per unit**; `quantity` multiplies them.

| Field | Required | Notes |
|---|---|---|
| `description` | ✔ | avoid generic text — the EU rejects "Clothes" |
| `quantity` | | defaults to `1` |
| `weight` | | **per unit** |
| `value` | | **per unit**, `Decimal` |
| `currency` | | defaults to `"USD"`. ShipStation v1 has no currency field |
| `hs_code` | | six digits minimum for USPS international since 2025-09-01 |
| `category` | | Easyship needs this **or** `hs_code`, even domestically |
| `origin_country` | | falls back to the sender's country |
| `sku` | | |
| `dimensions` | | used by Easyship's box packing |

### `Shipment`

| Field | Required | Notes |
|---|---|---|
| `from_address` | ✔ | |
| `to_address` | ✔ | |
| `parcels` | ✔ | always a tuple, even at one |
| `duties_paid_by` | | `DutiesPaidBy`, defaults to `UNSPECIFIED` (sends nothing) |
| `eei_exemption` | | override the derived EEI citation |
| `ship_date` | | |
| `reference` | | |

## Response objects

### `Quote`

| Field | Notes |
|---|---|
| `rates` | `tuple[Rate, ...]` |
| `excluded` | `tuple[Exclusion, ...]` — **read this**, see [Errors](errors.md) |
| `via` | which surface answered, e.g. `easypost:orders`, `shippo:fanoutx3` |
| `strategy` | `Strategy` |
| `messages` | provider prose that did not map to a code |

### `Rate`

| Field | Notes |
|---|---|
| `service_id` | `ServiceId \| None` — the stable address, `{provider}-{carrier}-{service}` |
| `carrier`, `service` | the **provider's** spelling, unnormalised |
| `amount`, `currency` | `currency` may be `None` on ShipStation v1 |
| `base_amount`, `surcharges` | where the provider itemises |
| `delivery_days`, `guaranteed` | `None` where not offered |
| `provider` | who quoted it, and who must sell it |
| `service_code` | provider machine code |
| `strategy`, `parcel_count` | how it was produced, and what it covers |
| `raw` | the provider's own payload |

### `Label`

| Field | Notes |
|---|---|
| `tracking_number` | primary |
| `tracking_legs` | `tuple[TrackingLeg, ...]` where a provider exposes several |
| `parcel_labels` | one per parcel on a multi-parcel purchase |
| `label_url` | provider-hosted |
| `is_test` | `True` / `False`, or `None` on ShipStation v2 which gives no hint |
| `amount`, `currency`, `carrier`, `service`, `provider`, `shipment_id`, `raw` | |

### `Exclusion`

| Field | Notes |
|---|---|
| `code` | `ExclusionCode` — switch on this |
| `message` | human text |
| `carrier`, `service` | where the provider says |
| `source` | `"shipzil"` if raised locally, else the provider |

### `ServiceId`

The stable, addressable identity for one provider's one service.

| Field / property | Notes |
|---|---|
| `provider` | `easypost`, `shippo`, `shipstation_v1`, … |
| `carrier` | **normalised**: `UPSDAP` → `ups`, `stamps_com` → `usps` |
| `service` | the provider's own spelling, slugified — **not** normalised |
| `packaging` | set only where the provider prices packaging separately |
| `.slug` | `"easypost-usps-groundadvantage"` — display and wire form |
| `.unqualified` | `"usps-groundadvantage"` — grouping only, **not** an equivalence claim |

Provider-namespaced on purpose: two providers offering what looks like the same
service get two addresses, because asserting they substitute for one another is a
claim with a much higher correctness bar. Store the parts, not the slug — a later
layer needs `carrier` and `service` without parsing a string apart.

```python
from shipzil.service_id import normalize_carrier, carrier_from_service

normalize_carrier("UPSDAP")                        # "ups"
normalize_carrier("stamps_com")                    # "usps"
carrier_from_service("USPS Ground Advantage - Package")   # "usps"
```

## Units

```python
z.Weight.of(16, "oz")               # oz, lb, g, kg
z.Dimensions.of(10, 8, 4, "in")     # in, cm
```

Both hold `Decimal`, and convert at the adapter boundary. Money is `Decimal`
throughout — never `float`.

## Enums

| Enum | Members |
|---|---|
| `DutiesPaidBy` | `UNSPECIFIED`, `SENDER`, `RECIPIENT` |
| `Strategy` | `NATIVE`, `ORDER`, `FANOUT` |
| `AddressClass` | `UNKNOWN`, `RESIDENTIAL`, `COMMERCIAL`, `PO_BOX`, `MILITARY` |
| `LithiumBatteryPacking` | `NONE`, `PACKED_WITH_EQUIPMENT`, `CONTAINED_IN_EQUIPMENT` |
| `RegulationLevel` | `LIMITED_QUANTITIES`, `EXCEPTED_QUANTITY`, `LIGHTLY_REGULATED`, `FULLY_REGULATED` |
| `ExclusionCode` | see [Errors](errors.md#every-exclusion-code) |

## Hazmat

```python
z.DangerousGoods(
    lithium_batteries=z.LithiumBatteryPacking.PACKED_WITH_EQUIPMENT,
    dry_ice=z.DryIce(contains=True, weight=z.Weight.of(2, "lb")),
    contains_alcohol=False,
    contains_liquids=False,
    biological_material=False,
    radioactive=False,
    un_number="UN3481",
    hazard_class="9",
    packing_group="ii",
    regulation_level=z.RegulationLevel.LIMITED_QUANTITIES,
)
```

Attach to a `Parcel`. Anything the chosen provider will not carry is reported as a
`HAZMAT_DETAIL_UNSUPPORTED` exclusion, never dropped silently. See
[Providers](providers.md#hazmat) for what each one accepts.

## Exceptions

All inherit `ShipzilError`. See [Errors](errors.md#exceptions) for the full table
and handling guidance.

```
ShipzilError
├── ConfigurationError
├── AuthenticationError
├── ValidationError
├── CapabilityError
├── RateLimitError
├── ProviderError
├── SpendLimitExceeded
└── LabelPurchaseError
    └── AmbiguousPurchaseError      # may have succeeded — never retry
```

## Adapters

```python
from shipzil.providers import EasyPostAdapter, ShippoAdapter, EasyshipAdapter
from shipzil.providers import ShipStationV1Adapter, ShipStationV2Adapter

EasyPostAdapter(api_key, *, timeout=60.0)
ShippoAdapter(api_token, *, timeout=90.0)
ShipStationV2Adapter(api_key, *, carrier_ids=None, timeout=60.0)

ShipStationV1Adapter(
    api_key, api_secret, *,
    carriers=None,          # restrict the fan-out; None rates every carrier
    test_labels=True,       # see the warning below
    confirmation="none",
    timeout=60.0,
)

EasyshipAdapter(
    api_key, *,
    sandbox=None,           # None = infer from the key; sandbox is a separate host
    label_timeout=60.0,     # purchase is async; this bounds the poll
    poll_interval=2.0,
    default_category=None,  # fallback when an Item has neither hs_code nor category
    timeout=90.0,
)
```

!!! warning "`ShipStationV1Adapter` defaults to `test_labels=True`"
    v1 has no separate test credential, so the adapter defaults to sending
    `testLabel: true`, which returns a real label response **without buying
    postage**. That is a deliberate safe default. You must pass
    `test_labels=False` explicitly to spend money, and `Label.is_test` will tell
    you which you got.

Each declares its own behaviour as class attributes, which is how per-provider
differences stay visible rather than buried in a branch:

| Attribute | Meaning |
|---|---|
| `customs_value_basis` | `"line_total"` or `"per_unit"` |
| `incoterm_style` | `"upper"`, `"lower"`, `"ddp_only"`, or `None` |
| `eei_style` | `"token"` or `"prose"` |
| `hazmat_fields` | what this adapter **sends**, not what the provider supports |
| `capabilities` | native multi-parcel, order resource, and so on |

See [Providers](providers.md) for the filled-in matrix.
