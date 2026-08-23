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
