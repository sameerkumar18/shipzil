# Providers

One adapter is one provider account. All five implement the same `Adapter`
interface, so switching is a constructor change — but they are not equivalent,
and this page is the honest version of where they differ.

```python
from shipzil.providers import (
    EasyPostAdapter,        # ("EZTK...")
    ShippoAdapter,          # ("shippo_test_...")
    ShipStationV1Adapter,   # (api_key, api_secret) — test_labels=True by default
    ShipStationV2Adapter,   # (api_key)
    EasyshipAdapter,        # (api_key, sandbox=None) — separate host for sandbox
)
```

Full constructor options are in the [reference](reference.md#adapters). One worth
knowing up front: **ShipStation v1 defaults to `test_labels=True`**, because v1 has
no separate test credential. You have to opt in to spending money.

## Support at a glance

| Provider | Rating | Multi-parcel | Buy | Void | Notable |
|---|---|---|---|---|---|
| EasyPost | yes | native, via `/orders` | yes | yes | fullest support |
| ShipStation v2 | yes | native, `packages[]` | yes | yes | best exclusion reporting |
| Shippo | yes | emulated | yes | yes | reports failures as `HTTP 201` |
| Easyship | yes | emulated | yes | yes | only surface that packs items into a box |
| ShipStation v1 | yes | emulated | yes | yes | no currency, no delivery estimate, one carrier per call |

## "Implemented" is not "proven"

This distinction is kept because a library handling label purchases should not
blur it. Rating is live-verified on all five. Purchasing is not.

| Provider | Rating | Purchase |
|---|---|---|
| EasyPost | live | shipment: **live**. order: verified against recorded traffic |
| Shippo | live | **live** (test token, buy and void) |
| Easyship | live | **live** (sandbox) |
| ShipStation v1 | live | **live** via `testLabel: true`, no postage charged |
| ShipStation v2 | live | route verified, **purchase never run** — production keys only |

International purchase specifically has been run on EasyPost and Shippo only.
Neither ShipStation adapter has ever bought an international label, so their
customs handling is asserted against the request body shipzil would send, not
against a carrier's acceptance of it.

## Multi-parcel: only two of six surfaces do it natively

This is the finding the library exists for. Three of the four that cannot
**fail without raising an error**.

| Surface | 1 parcel | N parcels | Mechanism |
|---|---|---|---|
| EasyPost `/shipments` | 19 rates | **0 rates, HTTP 201** | `parcels[]` silently ignored |
| EasyPost `/orders` | — | 17 order-level rates | different resource ✅ |
| Shippo `/shipments` | 11 rates | **0 rates, HTTP 201, `status: SUCCESS`** | array accepted, rated as nothing |
| ShipStation v2 `/v2/rates` | 27 rates | 7 rates | native `packages[]` ✅ |
| ShipStation v1 `/shipments/getrates` | 19 rates | **HTTP 400** | unsupported by design |
| Easyship `/2024-09/rates` | 5 rates | **HTTP 422** | *"No shipping solutions available"* |

shipzil routes EasyPost to `/orders` automatically, sends native `packages[]` on
v2, and emulates the other three by rating each parcel and combining. Every quote
is labelled with which happened — see [`Strategy`](concepts.md#strategy).

## Where the providers genuinely disagree

These are not stylistic differences. Each one has caused a real defect.

### Customs value basis

Whether a customs line means the per-unit figure or the line total, split 3–2:

| Provider | Basis | Documented as |
|---|---|---|
| EasyPost | line total | *"Total value (unit value \* quantity)"* |
| Shippo | line total | *"Total value of this item, i.e. quantity \* value per item"* |
| ShipStation v1 | line total | *"The value (in USD) of the line item"* |
| ShipStation v2 | **per unit** | *"The declared value of \*each\* item"* |
| Easyship | **per unit** | *"this value refers to the unit rather than the total"* |

You do not have to care — `Item.value` is always per unit and each adapter
converts. It is documented because getting it wrong multiplies the declared
customs value by the quantity, and shipzil got it wrong on ShipStation v2 until
the specifications were read directly.

### Duty liability

One concept, four spellings and one absence:

| Provider | Field | Values |
|---|---|---|
| Shippo | `customs_declaration.incoterm` | `DDP DDU FCA DAP eDAP` |
| Easyship | `incoterms` | `DDU DDP null` |
| ShipStation v2 | `customs.terms_of_trade_code` | lowercase `ddp` / `ddu` |
| EasyPost | `options.incoterm` | `DDP` only — **no DDU exists** |
| ShipStation v1 | — | **no field at all** |

`Shipment(duties_paid_by=...)` handles all of it. On ShipStation v1 the choice
cannot be expressed, so you get a `DUTIES_UNSUPPORTED` exclusion on the quote
rather than a silently ignored setting.

### Hazmat

`hazmat_fields` is a claim about **what shipzil sends**, not about what the
provider supports.

| Provider | shipzil sends |
|---|---|
| Shippo | lithium batteries, biological material, dry ice, alcohol |
| Easyship | lithium batteries, liquids |
| EasyPost | dry ice, alcohol |
| ShipStation v2 | dry ice, alcohol |
| ShipStation v1 | nothing |

Anything you declare that the chosen provider will not carry comes back as a
`HAZMAT_DETAIL_UNSUPPORTED` exclusion. It is never dropped silently.

EasyPost and ShipStation v2 both support far more than shipzil sends — EasyPost's
`options.hazmat` enum alone covers lithium classes, ORMD, limited quantity and
the DOT divisions. Mapping shipzil's PI965/966/967 model onto those is a
regulatory classification rather than a rename, so it is deliberately left
unclaimed and reported as dropped.

## Provider-specific things worth knowing

=== "EasyPost"

    - Multi-parcel silently returns zero rates on `/shipments`; shipzil uses
      `/orders` instead and reports `strategy=ORDER`.
    - Requesting DDP **reduces the rate list**, measured 18 → 14, because four
      services will not carry it. A customs flag filters carriers, it does not
      just change the price.
    - The only provider that enforces an idempotency key on purchase.
    - UPS caps customs items at 100. shipzil does not check this.

=== "Shippo"

    - **Reports failures as `HTTP 201`.** A rate request can come back
      successful, with `status: SUCCESS`, and an empty rate list plus a prose
      message such as `"UPS - Hard: Too Many Requests"`. shipzil parses those
      messages into structured exclusions, which is why the message inspection
      exists at all.
    - Requires an EEI citation (`eel_pfc`) on every international declaration.

=== "ShipStation v2"

    - Best structured exclusions of the five — it says *which* carrier dropped
      out and why.
    - `customs_items` is deprecated in favour of `packages[].products[]`, and the
      two are mutually exclusive. shipzil sends only `products`.
    - `is_test` is unknowable: this is the one provider that gives shipzil no way
      to tell a test label from a real one, so `Label.is_test` is `None`.

=== "ShipStation v1"

    - Rates **one carrier per call**, so shipzil fans out across carriers and
      merges.
    - Returns no currency and no delivery estimate. shipzil reports absence
      rather than assuming USD.
    - Customs items have **no weight field and no EEI field**, so a US export
      above the $2,500 threshold cannot be declared through v1 at all.
    - Customs value is USD-only — there is no currency field, so a non-USD `Item`
      is passed through as if it were dollars.
    - **Out-of-band gotcha:** ShipStation overwrites supplied `customsItems`
      unless *International Settings → Customs Declarations* is set to
      "Leave blank (Enter Manually)" in the dashboard. Nothing in the API reports
      that setting, so shipzil cannot detect it.

=== "Easyship"

    - The only surface that packs items into a box for you.
    - Requires `category` or `hs_code` on **every** item, even domestically, so
      shipzil refuses a domestic parcel with no item detail rather than inventing
      a category.
    - Quota exhaustion arrives as **`403 Forbidden`** with *"API usage limit
      exceeded"*, which reads like a bad credential. shipzil classifies it as
      `RateLimitError`, not `AuthenticationError`.
    - Sandbox is a **separate host**, chosen at construction.
    - DDP returns zero rates on the free sandbox while DDU returns four. The
      payload matches the published schema, so this is most likely a plan gate or
      a lane no connected courier serves — unproven either way.

## Adding a provider

The `Adapter` interface is the whole contract. See
[CONTRIBUTING](https://github.com/sameerkumar18/shipzil/blob/main/CONTRIBUTING.md)
and the [API reality](API-REALITY.md) document, which records the questions each
new surface has to answer before it can be trusted.
