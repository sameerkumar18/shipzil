# API reality — verified, not assumed

Live probes, 2026-08-23. Every claim was produced by an actual call. Raw
responses in `.probe/` (gitignored). Re-run `scripts/probe_capabilities.py`
before trusting this again — it is only as good as its date.

ShipStation calls were **read-only** (carrier listings, rate quotes). The only
ShipStation credentials available are production, so nothing mutating was ever
sent. Easyship was researched from docs only — no key yet.

## The headline

One shipment containing N parcels is the user's mental model. All five provider
surfaces disagree about whether that exists, and **three of them fail without
raising an error.**

| Surface | 1 parcel | N parcels | Multi-parcel mechanism |
|---|---|---|---|
| EasyPost `/shipments` | 19 rates | **0 rates, HTTP 201** | `parcels[]` silently ignored |
| EasyPost `/orders` | — | **17 order-level rates** | different resource ✅ |
| Shippo `/shipments` | 11 rates | **0 rates, HTTP 201, `status: SUCCESS`** | array accepted, rated as nothing |
| ShipStation **v2** `/v2/rates` | 27 rates | **7 rates** | native `packages[]` ✅ |
| ShipStation **v1** `/shipments/getrates` | 19 rates | **HTTP 400** | unsupported by design |
| Easyship `/2024-09/rates` | 5 rates | **HTTP 422** | *"No shipping solutions available"* |

**Only 2 of 6 surfaces natively rate multiple parcels.** Four cannot, and each
fails differently. That ratio is why the library emulates multi-parcel by
fan-out rather than reporting it as unsupported — refusing on four of six
surfaces would make the abstraction useless.

## How each surface reports "I can't do that"

This is the axis that matters, because the library's job is to turn all of it
into one honest answer.

**ShipStation v2 — the best, and the normalization target.** Returns the rates
that worked *and* a structured exclusion per carrier that didn't:

```json
{"error_source":"shipengine","error_type":"validation",
 "error_code":"multipackage_not_supported",
 "message":"carrier 30718 does not support multipackage. unable to rate the shipment"}
```

Partial success, machine-readable, attributed to a specific carrier. USPS
(se-30718) cannot multipackage; UPS (se-30719) can, at $25.27 UPS Ground.

**EasyPost — semi-structured.** 201 with `messages[].type = "rate_error"` and
prose: *"A to_address, from_address and parcel are required for rating."*
Parseable, but you must know to look.

**Shippo — prose only.** 201, `status: SUCCESS`, empty rates, and the reason
buried in `messages[]`: *"Carrier account shippo_usps_master doesn't support one
or more shipment options."* No code, no per-carrier structure.
Also observed: **`UPS - Hard: Too Many Requests` arriving as a message on a 201.**
Rate limiting hidden inside a success.

**ShipStation v1 — honest but blunt.** HTTP 400 with
`ModelState: {"request.weight": ["The weight field is required."]}`.

**Never discard provider messages.** On three of these surfaces they are the only
place the truth appears.

## Rate shape varies enormously

| Surface | Fields available |
|---|---|
| EasyPost | `carrier`, `service`, `rate`, `currency`, `delivery_days`, `est_delivery_days`, `delivery_date_guaranteed`, `list_rate`, `retail_rate`, `billing_type`, `carrier_account_id`, `mode` |
| ShipStation v2 | `carrier_friendly_name`, `service_type`, `shipping_amount{amount,currency}`, `carrier_delivery_days`, `delivery_days`, `estimated_delivery_date`, `guaranteed_service`, `negotiated_rate`, `insurance_amount`, `confirmation_amount`, `error_messages` |
| Shippo | `provider`, `servicelevel.name`, `amount`, `currency`, estimated days |
| **ShipStation v1** | **`serviceCode`, `serviceName`, `shipmentCost`, `otherCost` — that is all** |

ShipStation v1 returns **no currency and no delivery estimate**. So the unified
`Rate` cannot require those fields, and code that assumes them will break on v1.
Note also that v1 splits cost into `shipmentCost + otherCost`, which must be
summed to compare against the others' single amount.

Cross-check on identical input (SF 94117 → NY 10020, 16oz, 10×8×4): EasyPost
USPS Priority **$11.41**, ShipStation v1 USPS Priority **$11.41**, ShipStation v2
USPS Priority Mail **$9.62**, Shippo cheapest UPS 2nd Day **$15.17**. The first
two agree exactly — both are Stamps.com underneath.

## Easyship needs dimensions, just not necessarily on the box

Easyship is the only surface that will compute a box for you, but "no
dimensions required" is wrong — it needs dimensions *somewhere*. An item-only
parcel whose items carry no dimensions is rejected:

```
parcels[0].items[0].dimensions can't be blank
parcels[0].items[0].dimensions.length can't be blank
```

So there are three valid ways to describe a parcel to Easyship, and shipzil
checks for all three before making a call (`EasyshipAdapter._input_gap`):

1. dimensions on the parcel (a box), or
2. dimensions on **every** item — Easyship packs them, or
3. a `sku` per item that Easyship can look up.

This is why the capability is modelled as `requires_explicit_dimensions = True`
*plus* `can_derive_box_from_items = True`, rather than the tempting-but-false
`requires_explicit_dimensions = False`.

## There is no generic Easyship category, so shipzil will not invent one

`GET /item_categories` returns exactly 20 slugs, and every one is specific:

```
mobile_phones, tablets, computers_laptops, cameras, accessory_no_battery,
accessory_with_battery, health_beauty, fashion, watches, home_appliances,
home_decor, toys, sport_leisure, bags_luggages, audio_video, documents,
jewelry, dry_food_supplements, books_collectibles, pet_accessory
```

There is no `other`, `general`, `misc`, or `merchandise` bucket. Since Easyship
requires `category` or `hs_code` on every item **even domestically**, and a
category is a customs declaration, there is nothing honest to default to.
shipzil refuses locally with `ITEM_CLASSIFICATION_REQUIRED` and names the fix
(`Item(category=...)` or `EasyshipAdapter(default_category=...)`) rather than
guessing. This also applies to a bare box with no items at all, because shipzil
synthesizes a placeholder item for it — and that placeholder would need a
declaration it has no basis to make.

## Easyship is a different model, not just a different schema

From the docs (unprobed): Easyship's shipment carries `parcels[]`, and each
parcel has a **`box`** plus **`items[]`**. Dimensions and weight can be supplied
three ways:

1. `total_actual_weight` + `box`
2. `actual_weight` + `dimensions` per item → Easyship computes the box
3. `sku` per item → Easyship looks up stored product dimensions

So Easyship is **item-centric and can pack for you**, where the other four are
box-centric and expect you to have packed already. It is also duties/taxes-first,
being international by default.

This is the single biggest constraint on the data model: a `Parcel` that only
holds `weight/length/width/height` cannot express Easyship at all.

## Authentication

| Surface | Scheme |
|---|---|
| EasyPost | HTTP Basic, key as username, empty password. `EZTK…` test / `EZAK…` live |
| Shippo | `Authorization: ShippoToken <token>`. `shippo_test_…` test |
| ShipStation v1 | HTTP Basic, `key:secret` |
| ShipStation v2 | `API-Key: <key>` header |
| Easyship | Bearer token (docs) |

## Accounts observed

- ShipStation v1 carriers: `stamps_com`, `ups_walleted`
- ShipStation v2 carriers: `usps` (se-30718), `ups` (se-30719)
- Shippo test: USPS/UPS/DHL/Canada Post/Sendle/LSO/CouriersPlease master accounts
- Rate counts depend entirely on which carrier accounts a given tenant has, so
  **rate-count comparisons across providers are not capability comparisons.**

## Rate limits seen

- ShipStation v1: documented 40 req/min — probe gently.
- Shippo: surfaced `UPS - Hard: Too Many Requests` as a message during the
  multi-parcel probe, on a 201.
- Easyship: two distinct limits, and **neither one returns 429**.
  - Per-second burst, hit by ordinary back-to-back rating:
    `You have exceeded the maximum number of requests per second`.
  - Plan allowance, which is exhaustible on the sandbox and does not reset
    quickly: `API usage limit exceeded. Please upgrade your plan or wait for
    your usage period to reset.`

### 403 is Easyship's "you are out of quota"

Both of the above arrive as **`403 Forbidden`**, not `429`. This is the most
expensive lie in this document to debug, because the honest reading of a 403 is
"your credentials are wrong" — so the first instinct is to rotate a perfectly
good API key. The key is fine; the account is simply out of requests.

shipzil therefore classifies on the message, not the status code
(`http._is_quota`): a 403 whose body mentions a usage limit, quota, or plan
upgrade raises `RateLimitError`, and only a 403 *without* that language raises
`AuthenticationError`. Getting this backwards was a real bug, found because the
sandbox quota ran out mid-development. Regression tests live in
`tests/test_http.py::TestQuotaMisclassification`.

One consequence: **the Easyship sandbox quota is a finite development
resource.** Live Easyship tests can start failing for reasons that have nothing
to do with the code.

## Easyship specifics (sandbox, probed)

**Base URL differs by environment** — this cost time:

| Env | Base |
|---|---|
| production | `https://public-api.easyship.com` |
| **sandbox** | `https://public-api-sandbox.easyship.com` |

Using the sandbox key against the production host returns `401 invalid_token`,
and `GET /2024-09/account` on production returns a **500**, not a 401 — so the
first error you see does not point at the real problem.

**Cloudflare blocks default HTTP client user-agents.** urllib with no
`User-Agent` gets `403 error_code: 1010 browser_signature_banned` from
Cloudflare, not from Easyship. Always send a real UA.

**Every item requires `category` or `hs_code`, even for a domestic US shipment.**
Omitting both is a `422 invalid_content`:
`"parcels[0].items[0].category can't be blank if hs_code is blank"`. None of the
other four providers require customs classification for domestic. Valid category
slugs come from `GET /2024-09/item_categories` (`mobile_phones`, `fashion`,
`toys`, …).

**Rate shape is international-first.** Fields include `estimated_import_duty`,
`estimated_import_tax`, `ddp_handling_fee`, `fuel_surcharge`, `insurance_fee`,
`is_above_duty_threshold`, `incoterms`, `cost_rank`, `delivery_time_rank`,
`easyship_rating`. Courier identity is nested under `courier_service`, **not** a
top-level `courier_name` — that field exists and is `null`.

**Units are declared per request** via
`shipping_settings.units = {weight: "kg", dimensions: "cm"}`, unlike the others
which take units inline per field or fix them per endpoint.

## ShipStation v1 rates one carrier at a time

`carrierCode` is required on `POST /shipments/getrates`, so there is no
"rate my account" call. A comparable list means one request per connected
carrier, merged. Confirmed live: 2 connected carriers (`stamps_com`,
`ups_walleted`) produce 27 rates across 2 calls, and the account's carrier list
costs a third.

That interacts badly with the documented 40 req/min: a single multi-parcel
rate is `1 + carriers × parcels` requests. Three parcels across two carriers is
seven. `ShipStationV1Adapter(carriers=(...))` bounds it, and the adapter caches
the carrier list per instance. When a 429 does arrive mid-loop the adapter stops
rather than continuing to collect them, and names the carriers it never reached.

v1 also needs less to rate than any other surface — `fromPostalCode` alone for
origin, with no street address anywhere.

### The rate object has four fields, verified

```
{"serviceCode", "serviceName", "shipmentCost", "otherCost"}
```

That is the complete set, captured live and asserted in
`tests/test_real_payloads.py`. Two consequences:

* **No currency, no delivery estimate.** `returns_currency` and
  `returns_delivery_estimate` are both False and `Rate` leaves them None.
  Defaulting to USD would be an invention.
* **Cost is `shipmentCost + otherCost`.** `otherCost` carries surcharges, so
  quoting `shipmentCost` alone understates the price. Worth knowing: every
  `otherCost` in the captured sample is `0.0`, which means that fixture *cannot*
  detect a parser that forgets the addition. The test for that arithmetic is
  constructed on purpose.

### `testLabel` is the only safe way to exercise a v1 purchase

v1 accepts `testLabel: true` on label creation, returning a label response
without buying postage. Since the only v1 credentials in practice are
production, `ShipStationV1Adapter(test_labels=True)` is the **default**, and v1
has no key prefix to detect test mode from, unlike EasyPost's `EZTK` or Shippo's
`shippo_test_`. `is_test_credential()` therefore returns False always.

**Now exercised live, and honoured.** A `testLabel: true` purchase against
production credentials returned:

```
shipmentId      -1
trackingNumber  "99999999999999999999"
shipmentCost    0.0
```

No charge: both carriers' `balance` read 0.0 before and after. Those three
markers are unmistakable, which is why `Label.is_test` is a first-class field.

Two consequences the first implementation got wrong:

* **A test label cannot be voided.** `shipmentId` is `-1` because no shipment
  record exists, so `POST /shipments/voidlabel` would be a guaranteed failure.
  shipzil refuses locally and explains that nothing was purchased.
* **`shipmentCost` on the response is 0.0, not the quote.** `Label.amount`
  reports what was charged, which is zero; the quoted price stays on the `Rate`.
  Substituting the quote would misrepresent a free call as a spend.

A useful safety property of this particular account: both carriers have
`requiresFundedAccount: true` with a zero balance, so even a genuinely
mishandled `testLabel` would fail for insufficient funds rather than spend.

### One nice cross-surface detail

v1's `/carriers` reports `shippingProviderId: 30718` for Stamps.com. That is the
same id that appears in ShipStation **v2**'s exclusion text, "carrier 30718 does
not support multipackage" — the two APIs share underlying carrier provider ids,
which makes v1 and v2 responses correlatable.

## Two endpoints were invented, and only cross-checking code found them

Both were in purchase paths that had never executed. Documentation review had
not caught either; official SDK source and recorded test traffic did.

### EasyPost: orders and shipments buy completely differently

shipzil sent `POST /shipments/{order_id}/buy` with `{"rate": {"id": ...}}` for
an order-derived rate. Wrong endpoint and wrong body. The real contract, taken
from `tests/cassettes/test_order_buy.yaml` in EasyPost's own repository:

```
POST /orders/{id}/buy    {"carrier": "USPS", "service": "GroundAdvantage"}
-> {"object": "Order", "shipments": [
     {"id": "shp_...", "tracking_code": "...", "postage_label": {"label_url": ...}},
     {"id": "shp_...", "tracking_code": "...", "postage_label": {"label_url": ...}}]}
```

Orders buy by carrier and service **name**, not by rate id, and return one
shipment per parcel each with its own label and tracking code. This is why
`Label.parcel_labels` exists. It was deleted as dead code during a cleanup — it
was dead precisely because the feature it served was broken.

Multi-parcel is this library's headline feature, so this was the worst possible
place for an unverified assumption. Fixture at `tests/fixtures/ep_order_buy.json`.

### Easyship: the endpoint shipzil called does not exist

shipzil sent `POST /shipments/{id}/labels`, and its docstring claimed that path
was synchronous while "the batch endpoint is the asynchronous one and is
deliberately unused". Every part of that was invented. The live API replies:

```
The requested endpoint does not exist.
The request does not comply with the OpenAPI Specification.
```

What 2024-09 actually provides, per Easyship's published OpenAPI index:

* `POST /shipments` creates a shipment at `label_state: "not_created"`.
* `POST /batch_labels` (`batch_labels_create`) confirms shipments and *begins*
  document generation. Requires a `shipments` array, takes an optional
  `courier_service_id`.
* Generation is **asynchronous**: `not_created -> pending -> generated | failed`.
  There is no synchronous single-label endpoint.

shipzil now confirms via `batch_labels` and polls `GET /shipments/{id}` until the
state settles, bounded by `label_timeout`. Still unverified live, because the
sandbox allowance is spent — built from the same class of evidence that produced
the original bug, so treat it as unproven.

### What checked out

* **Shippo** `/transactions` body matches `TransactionCreateRequest` in the
  official SDK (`rate`, `async` via alias, `label_file_type`), and `/refunds`
  matches. Also verified live.
* **ShipStation v2** `POST /labels/rates/{rate_id}` matches
  `create_label_from_rate_id` in the ShipEngine SDK, and the route was probed on
  `api.shipstation.com/v2` with a nonexistent rate id: it answers
  "rate_id was invalid, unable to create label", so the route exists and
  validates. The purchase itself still has not run.
* **ShipStation v1** was already verified live including `testLabel`.

### Easyship's edge blocks the default Python user-agent

An unadorned `urllib` request gets Cloudflare error 1010 ("banned based on your
browser's signature"), which looks exactly like an auth failure. shipzil sets a
`User-Agent`, so this only bites when probing by hand.

## Customs items belong to the parcel, and the specs agree

| Provider | Where items live | Shape |
|---|---|---|
| Easyship | `parcels[].items[]` | per parcel |
| ShipEngine | `packages[].products[]` | per package |
| ShipEngine | `customs.customs_items[]` | **deprecated** |
| Shippo | `customs_declaration.items[]` | flat, shipment level, no parcel reference |

ShipEngine's deprecation note is explicit: *"Customs declarations for each item
in the shipment. (Please provide this information under `products` inside
`packages`)"*. So two of the three modern shapes are per parcel, and the one that
is not is the deprecated one.

That settles the modelling question. `Parcel.items` is lossless in the direction
that matters: a flat shipment-level list can always be produced by concatenating
per-parcel items, but per-parcel items cannot be recovered from a flat list
without inventing which box each line is in. The alternative encoding — a
shipment-level item list where each item names a parcel index — carries the same
information and adds a way to produce a dangling index, so nesting wins.

Shippo's `CustomsItem` fields, read from the spec: `description`, `quantity`,
`net_weight` + `mass_unit`, `value_amount` + `value_currency`, `origin_country`,
`hs_code`, `sku_code`, `tariff_number`, `eccn_ear99`, `metadata`.

### International purchase was broken on four of five providers

Only the Easyship adapter ever sent items. The other four collected
`Item.hs_code`, `origin_country` and `value` and discarded them, so no customs
declaration was built at all.

That failed in the worst possible way — rating succeeded and purchase did not:

```
US -> CA, fully declared item
  rating   4 rates  (USPS Priority Express Intl, Priority Intl, First Class Intl, DHL)
  purchase LabelPurchaseError: USPS - Customs declaration is required for
           international shipments via the USPS
```

A caller would see four perfectly good rates and discover the problem only when
buying. shipzil now builds the declaration on all five adapters and refuses at
*rating* time when it cannot, because a rate that can never be bought is worse
than no rate.

#### Two of the five builders were written and never called

The first pass added `_customs_info` (EasyPost) and `_international_options`
(ShipStation v1), and wired neither into a request. Both were correct. Both were
dead code, so EasyPost's international purchase still failed — now with a vaguer
error than before, `400 The request could not be understood by the server due to
malformed syntax`, because the shipment was created with no customs at all.

Unit tests did not catch it, and could not have: they asserted what the builders
*returned*, and a correct builder whose output is discarded still returns the
right thing. The tests that catch it patch `shipzil.http.request` and assert on
the bytes each adapter would actually send. `TestCustomsReachesTheWire` does that
for all five providers; deleting any single call site fails a named test.

The lesson generalises past customs, so there is also a test asserting that no
private helper in the package is defined-but-never-called.

### `eel_pfc` is a filing decision, so it is derived only where that is factual

The first declaration attempt failed with `customs_declaration.eel_pfc must not
be empty`. That field is the EEI exemption or citation, and choosing one is a
regulatory filing, not a formatting detail.

`NOEEI_30_37_a` is the Foreign Trade Regulations exemption for shipments valued
at $2,500 or less per Schedule B number. shipzil already knows the declared
value, so applying it below the threshold is a derivation from the caller's own
data. **Above the threshold it refuses**, because that case needs an AES filing
and an ITN that shipzil cannot produce. `Shipment(eei_exemption="AES_ITN")`
overrides either way.

Verified live, US to Toronto, one fully declared item, DDP:

| Provider | Tracking | Note |
|---|---|---|
| Shippo | `LS001791022US` | test token |
| EasyPost | `LM000024449US` | test key; rate count went 14 -> 18 once customs was attached |
| Easyship | — | rates DDU, zero rates DDP; see below |
| ShipStation v1 | — | no credentials; proven only at the payload layer |
| ShipStation v2 | — | no credentials; proven only at the payload layer |

The EasyPost rate count is worth keeping: attaching customs did not just make the
purchase work, it *unlocked four services* that the same shipment could not see
without a declaration. A missing declaration is not only a purchase-time failure,
it is silently a worse quote.

### Easyship DDP returns zero rates on the free sandbox, cause unresolved

US to CA, identical parcel and item, only `duties_paid_by` differing:

```
duties_paid_by unset (DDU default)   4 rates
duties_paid_by=SENDER  (DDP)         0 rates, HTTP 422
  message: The request body content is not valid.
  details: No shipping solutions available based on the information provided
```

The first read of that was "shipzil sends `incoterms` wrongly." **That was wrong,
and worth recording as wrong.** The v2024-09 OpenAPI definition puts `incoterms`
at the top level of `RateRequest`, typed `enum: ["DDU", "DDP", null]` — exactly
the placement and casing shipzil sends. `calculate_tax_and_duties`, which the
spec says "must be true when using DDP Incoterms", defaults to `true`, so
omitting it is also correct.

The `message` is a generic 422 envelope; the `details` line is the real content,
and *"no shipping solutions available"* is a courier-selection outcome, not a
schema complaint. The spec also documents a 402 for this exact feature: *"The DDP
RATES feature is not available for free subscription plan"*, and couriers
advertise per-service `supported_incoterms`.

So the likely cause is that no courier connected to this sandbox offers DDP on
this lane, or the plan gates it. **Not established**, because confirming it needs
a `/couriers` call to read `supported_incoterms` and the sandbox allowance was
spent probing the seven placement variants that ruled out a payload bug. No code
was changed on the strength of a guess.

## Every customs claim, re-checked against the provider's own schema

The customs work above was written from memory and from what the sandboxes
accepted. This section is the result of going back to the specifications. Two
claims in the code turned out to be **invented**, and one of them was hiding a
real defect.

### Where each provider's material actually comes from

| Provider | Source used | Status |
|---|---|---|
| Shippo | `public-api.yaml`, 936 KB OpenAPI 3.1, vendored | authoritative |
| ShipEngine / ShipStation v2 | `shipengine-openapi` repo + live `docs.shipstation.com` | vendored copy is **stale** |
| ShipStation v1 | `shipstation.com/docs/api/models/*` | authoritative, read live |
| Easyship | `developers.easyship.com` inline OpenAPI, vendored | authoritative |
| EasyPost | **nothing** | see below |

The vendored ShipEngine spec is behind the live docs: its `package_contents`
enum has six values where the live guide documents eight, adding
`e_commerce_goods` and `commercial_sale_of_goods_b2b`. shipzil sends
`merchandise`, valid in both, so nothing was wrong — but a vendored spec that is
silently a version behind is not a substitute for the live docs.

**The EasyPost scrape is empty.** `.apidocs/easypost/` holds six `.md` files
named `customs-infos.md`, `customs-items.md`, `shipments.md` and so on. All six
are byte-identical — the same 39,193-byte `404 - EasyPost` HTML page, MD5
`a0f08e70…`. Zero occurrences of `hs_tariff_number`, `eel_pfc` or `customs_info`
across all of them. The scrape reported success and captured nothing, and the
plausible filenames made it look like evidence. That is the same failure shape as
the customs builders that were written and never called: an artefact that passes
a glance and contains nothing.

### The customs value basis is not uniform, and shipzil assumed it was

The single most consequential finding. Every adapter was sending **line totals**,
on the strength of a comment claiming providers want them. Two of the four
providers that document it want the opposite:

| Provider | Basis | Documented as |
|---|---|---|
| Shippo | line total | *"Total value of this item, i.e. quantity \* value per item"* |
| ShipStation v1 | line total | *"The value (in USD) of the line item"* |
| ShipEngine / v2 | **per unit** | *"The declared value of \*each\* item"* (emphasis theirs) |
| Easyship | **per unit** | *"Please note that this value refers to the unit rather than the total"* |
| EasyPost | unknown | no source |

So ShipStation v2 was over-declaring by a factor of the quantity: two shirts at
$15 were declared at $30 each, $60 for the line. That inflates duty and misstates
the shipment to the destination authority. Easyship was already correct, by luck
rather than design — it builds its own item dicts and never adopted the shared
line-total helper.

Corroborated twice for v2, because one ambiguous sentence is not enough: the API
guide's *each*, and ShipStation's own help centre describing the same field in the
UI as **"Item Value (each) — the declared value per unit"** with **"Total Value
AUTO-CALCULATED … as Quantity × Item Value"**.

`CustomsLine` now carries both figures and every adapter declares a
`customs_value_basis`, so the choice is visible per provider instead of implied
by which helper someone reached for.

### Two citations in the code were fabricated

Both were specific, quoted, attributed to a provider, and used to justify a
decision. Neither can be sourced.

**1. EasyPost, quoted as documenting `"Total value (unit value * quantity)"`** in
three places. There is no such reading: the docs were never consulted and the
local scrape is 404s. This was the justification for line totals on EasyPost, and
it is now marked `customs_value_basis = "unverified"` — because the honest
position is not that line totals are wrong, but that nobody checked. Note the
live purchase succeeding is *no* evidence: a wrong declared value is accepted just
as readily as a right one.

**2. ShipStation v2, quoted as rejecting `delivery_duty_paid` with `"Unknown
TermsOfTradeCode value"`** in two places. shipzil has no ShipStation credentials
of any kind, so that error was never seen. `grep -ri termsoftrade .probe/` returns
nothing; the only ShipStation v2 error ever recorded is *"carrier 30718 does not
support multipackage"*. The **conclusion** was right for an unrelated reason —
the documented enum really is lowercase (`exw fca cpt cip dpu dap ddp fas fob cfr
cif ddu daf deq des`) — but the evidence was invented. Worth noting their own
example request sends `"DDP"` uppercase, so the field may be case-insensitive and
shipzil has never tested it.

The pattern in both: a real decision, dressed in a quotation that made it look
measured. A comment saying "assumed, unverified" would have been worth more,
because it would have flagged itself for exactly this pass.

### What checked out, verbatim

- Shippo `contents_type` **is** uppercase: `DOCUMENTS GIFT SAMPLE MERCHANDISE
  HUMANITARIAN_DONATION RETURN_MERCHANDISE OTHER`. `non_delivery_option` is
  `ABANDON | RETURN`. Both differ in case from every other provider, which is why
  the per-provider literals in section 2b of `GAPS.md` are not an accident.
- Shippo `eel_pfc` enum is exactly the five tokens shipzil maps:
  `NOEEI_30_37_a NOEEI_30_37_h NOEEI_30_37_f NOEEI_30_36 AES_ITN`.
- Shippo `incoterm` is `DDP DDU FCA DAP eDAP` — note `eDAP` is mixed case.
- ShipStation v1 `internationalOptions.customsItems` has exactly five fields:
  `customsItemId description quantity value harmonizedTariffCode
  countryOfOrigin`. **No weight and no EEI field**, as the adapter's docstring
  claimed. The dashboard caveat was also accurate, verbatim: supplied
  `customsItems` are overwritten unless *International Settings > Customs
  Declarations* is set to "Leave blank (Enter Manually)".
- ShipStation v1 `nonDelivery` defaults to `return_to_sender` on the
  `Shipments/CreateLabel` endpoint, which is the endpoint shipzil uses, so the
  explicit value matches the default rather than fighting it.
- ShipEngine `customs_items` really is deprecated in favour of
  `packages[].products[]`, as of **31 July 2023**, and the two are mutually
  exclusive: *"you cannot use both … Your request can only contain one of them."*
  shipzil sends only `products`.
- EasyPost's prose `eel_pfc` — `"NOEEI 30.37(a)"` rather than the token — is
  verified, not by documentation but by a successful live international purchase
  carrying it in the request body.

## Fan-out was quietly rewriting the shipment

Everything above about customs was verified on single-parcel shipments. Four of
six provider surfaces cannot rate multiple parcels natively, so shipzil fans out —
and `_single_parcel_shipment` was building each leg by naming fields:

```python
return Shipment(
    from_address=shipment.from_address,
    to_address=shipment.to_address,
    parcels=(parcel,),
    reference=shipment.reference,
)
```

Four of `Shipment`'s seven fields. `duties_paid_by`, `eei_exemption` and
`ship_date` were dropped on every multi-parcel shipment. Measured on the wire, one
parcel against two, identical in every other respect:

```
1 parcel,  DDP requested   customs_declaration.incoterm = "DDP"
2 parcels, DDP requested   customs_declaration.incoterm = None   (both legs)
```

So a two-parcel DDP shipment silently reverted duty liability to the recipient.

The second case is worse. With a declared value above the EEI threshold and an
explicit `eei_exemption="AES_ITN"`, the override is lost on each leg, so
`render_eei` returns None and the adapter declines to build a declaration at all —
while `customs_gap` still passes, because the client checks the gap against the
*original* shipment, which still has the override:

```
2 parcels, $8,000 declared, eei_exemption="AES_ITN"
  gap check      passes (no CUSTOMS_DECLARATION_REQUIRED)
  leg 1 payload  customs_declaration present = False
  leg 2 payload  customs_declaration present = False
```

Rating succeeds, nothing is declared, the purchase fails at the carrier. That is
precisely the failure mode the customs work was written to eliminate, reachable
by adding a second parcel.

The fix is `replace(shipment, parcels=(parcel,))`, which cannot drift as
`Shipment` grows. The regression test is field-driven — it walks
`dataclasses.fields(Shipment)` and asserts every field except `parcels` survives —
so adding a field cannot reintroduce this, and a fifth test asserts the method is
not written out by hand again.

The general lesson is narrower than "use replace": **a hand-copied constructor is
a silent truncation waiting for the next field.** The customs builders failed by
never being called; this failed by being called with a quietly diminished
argument. Both are invisible to a test that checks return values.

## One concept, several spellings: what is centralised and what is not

Four enum families cross all five providers. Two are now mapped in one place:

| Concept | Mechanism | Spellings reconciled |
|---|---|---|
| EEI citation | `eei_style` + `render_eei` | `NOEEI 30.37(a)` vs `NOEEI_30_37_a` |
| duty liability | `incoterm_style` + `render_incoterm` | `DDP` vs `ddp` vs *absent* |
| contents type | hardcoded per adapter | `merchandise` vs `MERCHANDISE` |
| non-delivery | hardcoded per adapter | `return` vs `RETURN` vs `return_to_sender` |

Duty liability was three near-identical `if/elif DutiesPaidBy` blocks in three
adapters, each with its own casing, plus two adapters with no block at all. The
two with none were not visibly different from the three with one, which is how
they went unnoticed: measured on the wire, **DDP and DDU produced byte-identical
payloads on EasyPost and ShipStation v1.** The caller's commercial decision was
being dropped without a word.

Those two now declare `incoterm_style = None`, and `duties_gap` returns
`DUTIES_UNSUPPORTED` on the quote — the same treatment `hazmat_fidelity_gap`
already gave to dropped hazmat detail. A test asserts no adapter outside
`base.py` branches on `DutiesPaidBy` again, with one allowed exception:
ShipEngine's `advanced_options.delivered_duty_paid` is a boolean rather than an
incoterm string, so it cannot come from the shared renderer. shipzil sends both
that and `customs.terms_of_trade_code`; the spec documents no precedence between
them.

The bottom two rows are deliberately left duplicated. Neither is caller-settable,
so a renderer would have a single input and no second caller. `docs/GAPS.md`
section 2b records the trigger for centralising them.

## Hazmat changes which rates come back, not just the price

Measured on Shippo with a test token, identical parcel, the only difference being
a declared lithium battery:

```
no hazmat            11 rates   2nd Day Air A.M., 2nd Day Air, 3 Day Select,
                                Ground, Ground Advantage, Ground Saver, ...
lithium batteries     3 rates   Ground Advantage, Priority Mail,
                                Priority Mail Express
```

Every UPS and FedEx service disappears; only USPS survives. This matches Shippo's
own note that dangerous-goods contents restrict eligibility to certain USPS
service levels.

The consequence for the previous version of shipzil is worse than a missing
field: it quoted **11 rates for a battery shipment, 8 of which the carrier would
refuse.** A silently omitted hazmat declaration is not a smaller answer, it is a
wrong one.

### Where each provider keeps hazmat, from their OpenAPI specs

| Provider | Location | Detail carried |
|---|---|---|
| ShipEngine | `packages[].products[].dangerous_goods[]` | full IATA: UN number, shipping name, hazard class, subsidiary class, packing group i/ii/iii, packing instruction + section, regulation level and authority, transport mode, tunnel code, radioactive, reportable quantity |
| ShipEngine | `advanced_shipment_options` | `dangerous_goods`, `dangerous_goods_contact`, `dry_ice`, `dry_ice_weight` (4 units), `contains_alcohol`, `regulated_content_type` (day_old_poultry, other_live_animal), `limited_quantity`, `non_machinable`, `fragile` |
| Shippo | `extra.dangerous_goods` | `contains`, `lithium_batteries.contains`, `biological_material.contains`; plus `extra.dry_ice` (kg only, must not exceed parcel weight), `extra.alcohol` (`recipient_type` licensee/consumer, mandatory for FedEx) |
| Easyship | `parcels[].items[]` | `contains_battery_pi966`, `contains_battery_pi967`, `contains_liquids`, `cpsc_compliance` |
| ShipStation v1 | — | nothing found |

Three different levels of granularity — per product, per shipment, per item — for
the same regulatory concept. shipzil accepts a declaration per `Parcel`, maps it
down to whatever the provider carries, and reports the remainder as
`HAZMAT_DETAIL_UNSUPPORTED` rather than dropping it. `Adapter.hazmat_fields`
records what each provider can actually take, read from these specs.

**PI966 versus PI967 is not cosmetic.** PI966 covers batteries packed *with*
equipment, PI967 batteries *contained in* equipment, with different labelling and
documentation duties. Easyship is the only provider that models the distinction;
Shippo collapses both to one boolean and shipzil says so.

USPS added a **Hazmat Handling Fee** plus a separate noncompliance fee for
improperly prepared hazardous material on 12 July 2026 (Publication 52).

## Residential is worth $6.15, measured

Same parcel, same lane, only the destination classification changing, live on
Easyship:

```
commercial    FedEx 2Day  19.55
residential   FedEx 2Day  25.70
unknown       FedEx 2Day  19.55     <- provider defaults to the cheaper answer
```

Exactly $6.15, and `residential_full_fee: 6.15` was present in the response the
whole time while shipzil discarded it. Note the third line: an unclassified
address quotes as commercial, so the caller sees the *cheaper* number and the
invoice arrives higher. shipzil cannot fix that, but it now surfaces
`residential_full_fee` in `Rate.surcharges` so the exposure is visible.

`Address.address_class` is an enum rather than a boolean because Shippo's v2
address model made the same change: PO boxes and military addresses are neither
residential nor commercial. `Address.residential` remains as a tri-state view for
providers that take a boolean, and **None means omit the field** — the previous
code did `bool(addr.residential)`, turning silence into "commercial".

## Test mode is knowable on four surfaces out of five

| Provider | How | Value |
|---|---|---|
| EasyPost | key prefix `EZTK` vs `EZAK` | True / False |
| Shippo | token prefix `shippo_test_` | True / False |
| Easyship | sandbox is a separate host, chosen at construction | True / False |
| ShipStation v1 | no key marker, but `testLabel` is explicit | True / False |
| ShipStation v2 | **no marker of any kind** | None |

`Adapter.is_test_mode()` returns `bool | None`, and `Label.is_test` carries it
through. None means shipzil cannot tell, which is deliberately not False —
reporting False would assert "this is a real purchase" on no evidence.

A dry run always reports `is_test=True`, since it never reached the network.

## Idempotency: one provider out of four

Checked against provider documentation rather than assumed, because the first
version of shipzil accepted an `idempotency_key` on all four adapters and only
EasyPost put it on the wire. The other three took the argument and dropped it,
which is worse than not offering it: the caller believes a repeat purchase is
safe when nothing enforces that.

| Provider | Client-supplied key? | What actually protects you |
|---|---|---|
| EasyPost | **Yes**, `Idempotency-Key` header on `POST /shipments/{id}/buy` | provider-side deduplication |
| Shippo | No | nothing beyond not retrying |
| ShipStation v2 | No | nothing beyond not retrying |
| Easyship | No | structural: a shipment's label can only be bought once |

Notes on the three that lack a key:

* **Shippo** documents no idempotency header on `/transactions`. Searching for
  one surfaces only their *internal* billing-reconciliation design, which
  dedupes carrier invoice charges and has nothing to do with the public API.
  The documented request body is `rate`, `async`, `label_file_type`, `metadata`,
  `order`. `metadata` is free-form and is **not** a deduplication key, so
  putting a key there would look like protection while providing none.
* **ShipStation v2 / ShipEngine** states plainly: "There are two HTTP headers
  that you need to set in your request" — `API-Key` and `Content-Type`.
* **Easyship** has no header, but is protected by its two-step model. A second
  `POST /shipments/{id}/labels` for the same shipment returns
  `Shipments not found or labels already requested: ESHK…`. So a duplicate label
  call fails loudly instead of double-charging. Related trap from the same
  thread: `buy_label_synchronous: true` on shipment creation buys the label at
  creation time, so a follow-up label call then reports "already requested."
  shipzil does not set that flag.

shipzil's resolution is to refuse rather than pretend. `Adapter.supports_idempotency_key`
is True only for EasyPost. Pass an explicit key to any other provider and the
client raises `CapabilityError` naming the provider and telling you to omit the
key if the weaker guarantee is acceptable. No adapter retries a purchase
(`retries=0` everywhere), which covers the common duplicate-charge case, but
that is not the same thing as provider-side deduplication and shipzil does not
conflate the two.

## Sync vs async — verified from docs, per operation

The library is synchronous throughout. Where a provider is genuinely async, it
polls to present a synchronous result.

| Operation | Reality |
|---|---|
| Easyship single label — `POST /2024-09/shipments/{id}/labels` | **synchronous** — docs: *"Create a label for an existing shipment and retrieve it synchronously"* (beta) |
| Easyship **batch** labels | **async** — `label_state` goes `pending` → `generated`, optional callback URL. Not used; single-label path is sync |
| Shippo shipment create | sync when `async: false` is passed explicitly — the default is async |
| EasyPost shipment/label buy | sync |
| ShipStation v1 / v2 rate quotes | sync |

`label_state` values worth handling if batch is ever used: `not_created`,
`pending`, `generating`, `generated`, `printed`, `shipping_document_generated`,
`failed`, `technical_failed`, `reported`, `voided`, `void_failed`.

## Easyship has a native exclusions endpoint

`GET /2024-09/shipment_unavailable_couriers` — *"List Unavailable Couriers for a
Shipment"*. Easyship is the only provider with a dedicated surface for "who
can't carry this and why", which is a useful second source for populating
`Quote.excluded`.

## Shippo returns 2xx for failures, consistently

Four separate observations of the same anti-pattern. This is why `ShipzilError`
carries provider messages and why `Quote.excluded` exists:

| Operation | Response | Real outcome |
|---|---|---|
| Rate a multi-parcel shipment | `201`, `status: SUCCESS`, `rates: []` | unrated; reason in `messages[]` prose |
| Rate while throttled | `201` with message `"UPS - Hard: Too Many Requests"` | rate limited, no 429 |
| Refund a test-mode label | **`201`, refund `status: "ERROR"`, `messages: []`** | rejected; transaction → `REFUNDREJECTED`, **no reason given** |
| Async default | `async: true` unless told otherwise | a caller expecting rates gets a queued object |

Refund rejection persists on polling (`ERROR` at +2s, +4s, +6s), so it is a
decision rather than a race. `shipzil`'s `void()` therefore raises rather than
returning a silent `False`, and states that the provider gave no reason.

Buying a test label works normally: `POST /transactions/` with `async: false`
returns `status: SUCCESS`, a tracking number and a PDF URL.
