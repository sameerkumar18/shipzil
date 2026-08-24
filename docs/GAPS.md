# Gaps

What shipzil does not model, taken from the providers' own OpenAPI specifications
rather than their prose documentation. Specs were scraped into `.apidocs/`
(gitignored) and read directly:

| Provider | Source |
|---|---|
| Shippo | `docs.goshippo.com/spec/shippoapi/public-api.yaml`, 938 KB, OpenAPI 3.1 |
| ShipEngine / ShipStation v2 | `github.com/ShipEngine/shipengine-openapi`, 17 MB, 70 paths, 337 schemas |
| Easyship | `developers.easyship.com/llms.txt` → 213 reference pages with inline OpenAPI |
| ShipStation v1 | `shipstation.com/docs/api/*` HTML |

EasyPost was **absent for two compounding reasons, both now resolved.** Its docs
were off-limits pending clearance, and the scrape that had been attempted
silently failed: `.apidocs/easypost/` still contains six plausibly-named `.md`
files that are all the same 39,193-byte 404 page (MD5 `a0f08e70…`), with zero
occurrences of `hs_tariff_number`, `eel_pfc` or `customs_info`. The directory
looked like evidence and was not, which is worth leaving in place as a caution:
**that directory is not a source, delete or re-scrape it before trusting it.**

`docs.easypost.com` has since been read. `customs_value_basis`, `incoterm_style`
and `hazmat_fields` are now sourced rather than assumed — see the EasyPost section
of `docs/API-REALITY.md`, including the correction of a wrong accusation made
while the docs were still unread. Gaps below that predate that pass and are marked
where they have been superseded.

Ordered by commercial risk, not by effort.

**Reading this document.** It was written as a survey of what shipzil did not
model, and several entries have since been implemented. Rather than delete them —
the provider research in each is still the reference for that concept — entries
that have been addressed now say so in bold, and state what is *still* missing.
An entry with no such note has not been touched. Sections 1 and 2 carry one.

---

## 1. Hazmat and dangerous goods — nothing modelled at all

**Partly addressed.** `DangerousGoods` and `DryIce` now model the common flags,
each adapter declares `hazmat_fields` for what it can carry, and
`hazmat_fidelity_gap` reports declared detail a provider will drop rather than
discarding it silently. What remains unmodelled is the fully regulated
consignment: ShipEngine's 19-field per-product declaration below is still
unreachable through shipzil, so `un_number`, `hazard_class` and `packing_group`
are reported as dropped on every provider rather than sent to any of them.

The largest gap, and the three aggregators disagree profoundly about *where*
hazmat lives. Any abstraction has to reconcile shipment-level booleans against
per-item flags against full IATA/DOT declarations.

### ShipEngine — per-product, fully regulated

`packages[].products[].dangerous_goods[]` is a real hazmat declaration, 19 fields:

```
id_number                       UN number
shipping_name                   proper shipping name
technical_name                  chemical name
product_class                   hazard class
product_class_subsidiary        secondary hazard
packaging_group                 i | ii | iii
packaging_instruction           e.g. PI 965
packaging_instruction_section   section_1 | section_2 | section_1a | section_1b
packaging_type
regulation_authority            IATA, DOT, ADR…
regulation_level                lightly_regulated | fully_regulated
                                | limited_quantities | excepted_quantity
transport_mean                  ground | water | cargo_aircraft_only
                                | passenger_aircraft
transport_category
tunnel_code                     ADR tunnel restriction
radioactive                     boolean
reportable_quantity             boolean
quantity, dangerous_amount, additional_description
```

Plus shipment-level in `advanced_shipment_options`: `dangerous_goods` (boolean),
`dangerous_goods_contact {name, phone}`, `dry_ice` (boolean),
`dry_ice_weight {value, unit}` where unit accepts pound/ounce/gram/kilogram,
`contains_alcohol`, `regulated_content_type` (`day_old_poultry` |
`other_live_animal`), `limited_quantity`, `non_machinable`, `fragile`,
`additional_handling`.

### Shippo — shipment-level `extra`, nested objects

```
extra.dangerous_goods.contains                            boolean
extra.dangerous_goods.lithium_batteries.contains          boolean
extra.dangerous_goods.biological_material.contains        boolean
extra.dangerous_goods_code                                DHL eCommerce category codes
extra.dry_ice.contains_dry_ice                            boolean, mandatory
extra.dry_ice.weight                                      mandatory, KILOGRAMS ONLY,
                                                          must not exceed parcel weight
extra.alcohol.contains_alcohol                            boolean, FedEx + UPS only
extra.alcohol.recipient_type                              licensee | consumer,
                                                          mandatory for FedEx
```

Shippo's own note: dangerous-goods contents restrict eligibility to certain USPS
service levels, so this changes *which rates come back*, not only the price.

### Easyship — per item, and the only one modelling battery packing instructions

```
parcels[].items[].contains_battery_pi966    batteries PACKED WITH equipment
parcels[].items[].contains_battery_pi967    batteries CONTAINED IN equipment
parcels[].items[].contains_liquids
parcels[].items[].cpsc_compliance           regulated HTS items
```

PI966 vs PI967 is a real regulatory distinction with different labelling and
documentation duties. Easyship is the only one of the three that captures it, and
it is per item, which is the correct granularity.

### ShipStation v1

No hazmat fields found in the scraped documentation. Treat as unsupported.

### Carrier-side, USPS, effective 12 July 2026

A new **Hazmat Handling Fee** on Parcel Select / Priority Express / Priority /
Ground Advantage, **plus a separate noncompliance fee for improperly prepared
hazardous material**. Reference Publication 52. Also a new Live-Animal and
Perishable Handling Fee for Priority Express (DMM 283.1.9).

### Consequence

shipzil cannot ship a battery, an aerosol, a bottle of wine, dry ice or anything
lightly regulated without silently omitting a legally required declaration. It
will *appear* to work: the label prints, and the shipper carries the liability.

---

## 2. Hardcoded `incoterms: "DDU"`

`easyship.py` sets DDU in both rating and purchase. DDU means **the recipient pays
import duty and tax on arrival**. That is a commercial liability decision made
silently on the caller's behalf.

Easyship supports `DDU`, `DDP` and `null`. Shippo's `incoterm` enum is wider:
`DDP`, `DDU`, `FCA`, `DAP`, `eDAP`, with carrier restrictions (FCA is DHL Express
and FedEx only; DAP is DHL Express and DPD UK; eDAP is DPD UK). ShipEngine
expresses the same idea in at least three places —
`customs.terms_of_trade_code`, `advanced_options.delivered_duty_paid`, and
DHL-specific `duties_taxes_paid` / `bill_duties_to_sender` — with no documented
precedence.

A merchant selling landed-cost DDP gets the wrong duty model *and* a quote missing
`ddp_handling_fee`.

**Addressed, on three providers of five.** `Shipment.duties_paid_by` drives the
field through one shared mapping, `Adapter.render_incoterm`, and `UNSPECIFIED`
sends nothing so the account default applies rather than a hardcoded liability.

Measured on the wire afterwards, DDP and DDU produced **byte-identical payloads
on EasyPost and ShipStation v1**: those two adapters had no duty field at all, so
the caller's choice was being discarded in silence. They now declare
`incoterm_style = None` and `duties_gap` reports it as
`DUTIES_UNSUPPORTED` on the quote. Worded as a shipzil limitation rather than a
provider one, because EasyPost's documentation is off-limits here and v1's
absence rests on scraped HTML.

Still missing: the wider Shippo enum (`FCA`, `DAP`, `eDAP`) is not exposed.
ShipEngine expresses duty liability in two places and shipzil now sends **both**
`customs.terms_of_trade_code` and `advanced_options.delivered_duty_paid`; no
precedence is documented, so if they ever disagree the outcome is undefined.
Easyship DDP is unproven live — see *Still unresolved*.

---

## 2b. Enum families still spelled per adapter

Two concepts are centralised — the EEI citation via `eei_style` / `render_eei`,
and duty liability via `incoterm_style` / `render_incoterm`. Two are not, and are
currently hardcoded four times each:

| Concept | EasyPost | Shippo | v1 | v2 |
|---|---|---|---|---|
| contents type | `merchandise` | `MERCHANDISE` | `merchandise` | `merchandise` |
| non-delivery | `return` | `RETURN` | `return_to_sender` | `return_to_sender` |

No bug today, because shipzil always sends the same value and each literal is
correct for its provider. It is listed because the casing split is real — Shippo
uppercases where EasyPost does not — and that is exactly the shape of the `eel_pfc`
defect, where one provider wanted `NOEEI 30.37(a)` and another the token
`NOEEI_30_37_a`.

Deliberately **not** abstracted yet. Neither is a caller-facing option, so a
renderer would have one input and no second caller — speculative generality. The
trigger to centralise is the moment `contents_type` becomes settable (gift,
documents, sample, returned goods, each with per-provider spellings and duty
consequences) or `non_delivery` becomes a choice, since abandonment destroys the
goods and return costs money. At that point it should follow `eei_style`, not
grow a fifth hardcoded literal.

---

## 2c. Customs fields the specs document and shipzil does not send

Found by reading the schemas rather than by hitting an error, so none of these
has ever produced a failure — they are simply unreachable through shipzil.

**ShipStation v2 / ShipEngine**

- `advanced_options.canada_delivered_duty: "sender_prepay"` — the actual
  mechanism for prepaid DDP on US→Canada USPS, a flat $9.95 surfaced in
  `other_amount`. shipzil sends `terms_of_trade_code` and
  `delivered_duty_paid` but not this, so the one lane most likely to be used for
  DDP is the one where shipzil expresses it least directly. Worth noting the
  library's own test lane is US→Toronto by USPS.
- `tax_identifiers[]` — `vat eori ssn ein tin ioss pan voec pccc oss passport
  abn ukims`. Not modelled at all. IOSS and EORI are effectively mandatory for
  commercial EU traffic, so this is the largest of these.
- `products[].vat_rate`, `mid_code`, `product_url`, `sku_description`, and
  per-product `dangerous_goods[]` — the last being the fully regulated hazmat
  declaration that section 1 says is unreachable.
- `customs.declaration`, `invoice_additional_details`, `importer_of_record`,
  `license_number`, `certificate_number`.
- `products[].sku` is marked required in the guide's table while the prose says
  "required only by some carriers". shipzil sends it only when the caller
  supplied one, which follows the prose.

**Shippo**

- `tariff_number` as distinct from `hs_code`, with documented precedence: *"If
  `tariff_number` is not provided, `hs_code` will be used."* shipzil only sends
  `hs_code`, so the fallback path is the only one exercised.
- `eccn_ear99` — *"Export Control Classification Number, required on some
  exports from the United States."*
- `CustomsDeclarationB13AFilingOption` — Canadian export declaration.
- `CustomsExporterIdentification`, `CustomsTaxIdentification`,
  `CustomsInvoicedCharges`.
- The wider `incoterm` values `FCA`, `DAP`, `eDAP`.

**EasyPost** (read after this section was first written)

- `options.duty_payment` — `{type: SENDER|THIRD_PARTY|RECEIVER, account, country,
  postal_code}`, for billing duty to a third-party account. FedEx and UPS only,
  and *"may not be supported for EasyPost Wallet carrier account types"*.
  shipzil expresses only sender-vs-recipient via `options.incoterm`, so
  third-party duty billing is unreachable.
- `options.payment` — the same shape for **postage** billing, including
  `COLLECT`. Unmodelled, so every label bills the sender.
- `options.hazmat` — the full enum. shipzil emits only `dry_ice` and `alcohol`;
  see API-REALITY for why lithium is deliberately not mapped.
- `options.suppress_etd`, `invoice_number`, `customs_info.declaration`,
  `contents_explanation`, `restriction_comments`, and CustomsItem
  `manufacturer` / `eccn` / `printed_commodity_identifier`.
- **UPS caps customs items at 100.** shipzil does not check, so a 101-item
  shipment fails at purchase with a carrier error rather than a shipzil gap.

**ShipStation v1**

- `value` is documented *"(in USD)"* and there is no currency field. A caller
  shipping with `Item(currency="EUR")` has the number passed through as if it
  were dollars — mis-declared, not converted. shipzil does not currently refuse
  or warn on this, and should.

**Regulatory, not a field gap**

- USPS requires a six-digit HS code on **all** international commercial
  shipments for **each item** as of **1 September 2025**, aligning with UPU
  rules. shipzil treats `hs_code` as optional and will happily build a
  declaration without one, which is now a purchase-time failure waiting to
  happen on the single most common carrier. `customs_gap` should probably refuse
  a USPS international shipment with any line missing an `hs_code`.

---

## 3. Residential: dropped on two providers, fabricated on one

| Provider | Field | shipzil today |
|---|---|---|
| Shippo | `is_residential`, on **both** addresses | never sent |
| Easyship | `set_as_residential`, destination only | never sent |
| ShipStation v1 | `residential` | `bool(None)` → **`False`** |
| ShipStation v2 | `address_residential_indicator` (`unknown`/`yes`/`no`) | correct |
| EasyPost | unverified — docs off-limits | sends a boolean |

The v1 line asserts "commercial" when the caller said "unknown". That is inventing
data, which this library forbids everywhere else.

Cost: UPS US residential surcharge is $6.60 per package. Easyship's own rate
object returns `residential_full_fee: 6.15`. Omitting the flag understates a quote
by roughly $6 per parcel, so about $18 on a three-parcel fan-out.

Shippo is mid-migration: v1 `is_residential` (boolean) versus v2 `address_type`
(`residential` | `commercial` | `unknown` | `po_box` | `military`). A boolean
cannot express PO Box or military, so a future model should not be boolean.

---

## 4. Predefined and flat-rate packaging

No support for any of:

- Shippo `parcels[].template` — `USPS_FlatRateEnvelope`, `USPS_SmallFlatRateBox`,
  `USPS_MediumFlatRateBox1/2`, `USPS_LargeFlatRateBox`, `USPS_RegionalRateBoxA1/A2`,
  `FedEx_Box_Small_1`, and more. Read-only carrier templates are discoverable via
  `GET /carrier-parcel-templates`, which also exposes `is_variable_dimensions`.
- ShipEngine `packages[].package_code` — `flat_rate_envelope`,
  `small/medium/large_flat_rate_box`, `regional_rate_box_a/b`, `letter`,
  `large_package`, `package`. Discoverable via
  `GET /v1/carriers/{carrier_id}/packages`.
- Easyship `box.slug`, discoverable via `GET /2024-09/boxes`. Slugs carry the
  dimensions, so supplying a slug replaces L/W/H.

**This is not merely a missing feature.** With a template, dimensions must be
*omitted*: Shippo enforces it schematically with two mutually exclusive request
bodies — `ParcelCreateFromTemplateRequest` requires `template` + `weight` and the
dimension fields *must be empty*, while `ParcelCreateRequest` requires them.
shipzil's `Parcel` cannot express the first, and its dimension pre-flight would
refuse the shipment. So the "never invent dimensions" rule produces a refusal
exactly where the provider would quote happily — and Flat Rate is frequently the
cheapest option, so shipzil systematically returns worse prices than the
provider's own UI.

Weight is still required either way, because flat rate has a weight ceiling
(70 lb on every USPS template).

---

## 5. Dimensional weight, oversize and cubic — the concept is absent

No mention of DIM weight, billable weight, girth, oversize or cubic anywhere in
the project, and `Dimensions` has no maximum validation.

USPS, current:

- Divisor is **139**, changed from 166 on **12 July 2026**.
- Applies above **1 cubic foot (1,728 in³)**, zones **1–9**, to Priority Express,
  Priority, **Ground Advantage** and Parcel Select.
- Each dimension now rounds **up** to the whole inch (previously nearest), which
  compounds across three dimensions.
- Nonstandard fees: >22–30in **+$4.50**, >30in **+$10.00**, >2 ft³ **+$21.00**, and
  a piece can incur both a length and a cube fee.
- Oversized: length + girth >108in → oversized price **regardless of weight**;
  maximum 130in.
- **Dimension Noncompliance Fee $3.00** when dimensions are omitted or inaccurate
  for Ground Advantage over 1 ft³ or 22in.
- Cubic pricing rounds **down** to the nearest ¼ inch — opposite direction to DIM.
- Nonrectangular parcels take a 0.785 adjustment factor.

Three consequences specific to shipzil:

1. **The fan-out sum is wrong in a predictable direction.** The current warning
   says a carrier "may price a consignment differently from the sum of its parts".
   With DIM thresholds and oversize bands, three 12in boxes versus one 36in box is
   deterministic, not probabilistic. The disclosure understates the problem.
2. **Weight-only parcels are a fee condition, not just a rating limitation.**
   shipzil permits `Parcel(weight=...)` with no dimensions; USPS charges $3.00 for
   exactly that on qualifying Ground Advantage parcels.
3. No girth calculation, so nothing can warn before a carrier reclassifies a piece
   as oversized.

---

## 6. Multi-tracking: one string where there are four mechanisms

`Label.tracking_number` is a single string, and the Easyship parser `break`s on the
first leg. Shipments legitimately carry more than one number for four independent
reasons:

| Mechanism | Shape |
|---|---|
| Multi-leg international | Easyship `trackings[]` with `leg_number`; *"if a shipment is passed to a new courier, it begins a new leg"* |
| Multi-piece (MPS) | ShipEngine `packages[].tracking_number` + `sequence`, master at label level; UPS allows up to 20 |
| Postal hybrid handoff | usually one number, carrier changes mid-route |
| Carrier-internal alias | `local_tracking_number`, `alternate_tracking_number` (DHL eCommerce) |

Shippo prints `MSTR` plus a per-parcel number on the label, and the other parcels'
labels need a second call, `GET /transactions?rate=<rate_object_id>`, which shipzil
never makes.

---

## 7. Cost breakdown discarded

Easyship returns 25 cost components; `Rate` keeps one number. From a single real
rate:

```
shipment_charge         56.12   base
fuel_surcharge           3.03
remote_area_surcharge    4.45
residential_full_fee     6.15
total_charge            63.60   the only value shipzil keeps
```

A 13% gap between base and total, entirely surcharges, invisible. That blocks
answering "why is this $63", comparing base rates across providers, and detecting
quote-versus-invoice drift.

---

## 8. Insurance and declared value

Not modelled at all. Three distinct values must not be collapsed: **customs
declared value**, **insured value**, and **COD amount**. `Item.value` is the first
only.

- Shippo `extra.insurance {amount, content, currency, provider}`; provider defaults
  to XCover, or `FEDEX`/`UPS`/`ONTRAC` for carrier cover. Also per parcel.
- ShipEngine `packages[].insured_value {currency, amount}` — per package — plus
  shipment-level `insurance_provider`.
- Easyship `insurance {is_insured, insured_amount, insured_currency}`; rate exposes
  `insurance_fee`.

Coupling worth knowing: declaring ≥$500 insurance with no signature is overridden
by FedEx to Direct Signature Required. Insurance and signature are not independent.

---

## 9. Everything else, by concept

| Concept | Shippo | ShipEngine | Easyship |
|---|---|---|---|
| Signature | `extra.signature_confirmation` (STANDARD/ADULT/CERTIFIED/INDIRECT/CARRIER_CONFIRMATION) | `confirmation` (none/delivery/signature/adult_signature/direct_signature/delivery_mailed/verbal_confirmation) | not found |
| Authority to leave | `extra.authority_to_leave` | `advanced_options.shipper_release` | not found |
| Saturday | `extra.saturday_delivery` | `advanced_options.saturday_delivery` | not found |
| Ship date | `shipment_date` | `ship_date` (manifests are keyed to it) | not confirmed |
| Pickup vs dropoff | `POST /pickups` (USPS + DHL Express only) | `advanced_options.origin_type` | `rates[].available_handover_options`, `minimum_pickup_fee`, `pickup_state` |
| Manifest / SCAN | `POST /manifests` | `POST /v1/manifests`, USPS 9pm cutoff | not confirmed |
| Return label | `extra.is_return`, `extra.rma_number`, `address_return` | `POST /v1/labels/{id}/return`, `charge_event: on_carrier_acceptance`, `rma_number` | `return`, `original_easyship_shipment_id` |
| Address validation | `GET /addresses/{id}/validate`; v2 `address_type` | `POST /v1/addresses/validate`, `/parse` | implicit for US |
| Third-party billing | `extra.billing {type, account, country, zip}` (SENDER/RECIPIENT/THIRD_PARTY/THIRD_PARTY_CONSIGNEE/COLLECT) | `advanced_options.bill_to_party` + `bill_to_account` | `courier_settings.courier_account_number` (LYOC) |
| Published vs negotiated | `extra.request_retail_rates` | `comparison_rate_type` | `payment_recipient` |
| COD | `extra.cod {amount, currency, payment_method}` | `advanced_options.collect_on_delivery` | not found |
| EEI / ITN | `customs.eel_pfc` (NOEEI_30_37_a/h/f, NOEEI_30_36, AES_ITN) | — | `eei_reference` |
| Non-delivery | `customs.non_delivery_option` (ABANDON/RETURN) | `customs.non_delivery` | not found |
| Commercial invoice | `commercial_invoice_url` on transaction | `invoice_additional_details` | `shipping_documents[]` |
| Tax IDs | — | `importer_of_record` | `regulatory_identifiers {eori, ioss, vat}`, `buyer_regulatory_identifiers`, `consignee_tax_id` |
| Freight / LTL | — | `freight_class`, `fedex_freight`, `use_ups_ground_freight_pricing` | — |
| Zone skipping | `extra.carrier_hub_id`, `carrier_hub_travel_time`, `fulfillment_center`, `critical_pull_time` | — | — |
| References | ~19 UPS-only structured fields | `label_messages.reference1-3`, `custom_field1-3` | `metadata` |
| Label format | `label_file_type` | `label_format` **and** `label_layout` | `printing_options` per document |
| Carbon | `extra.carbon_neutral` (UPS) | — | — |
| Delivery instructions | `extra.delivery_instructions` (≤500 chars, FedEx + OnTrac) | — | `destination_address.delivery_instructions` |
| Alcohol | `extra.alcohol` | `advanced_options.contains_alcohol` | — |
| Non-machinable | — | `advanced_options.non_machinable` | — |
| Fragile | — | `advanced_options.fragile` | — |

`Address` also has no `street3`, which ShipStation v1 (`street3`) and Easyship
(`line_3`) both accept.

Operational note: **Shippo does not return rates or shipments older than 390
days**, which matters to anything caching a rate `object_id`.

---

## 10. A claim of ours that may be false

shipzil classifies **Shippo as fan-out only**, based on one probe that returned
zero rates for three parcels. Shippo documents native multi-piece via `parcels[]`,
returning a combined amount and a master tracking number, and that same probe
surfaced *"UPS — Hard: Too Many Requests"*.

So the zero may have been rate limiting or one carrier's restriction rather than a
capability limit. If so, `README.md` and `docs/API-REALITY.md` both assert
something untrue, and shipzil is fanning out where it could rate natively, losing
both the combined discount and the master tracking number.

Needs re-probing across several carrier accounts before either document is trusted
on this point.

---

## Where semantics differ under one name

Things an abstraction must not flatten:

- ShipEngine's `confirmation` values mean different things per carrier. The DHL
  Express MyDHL mapping **inverts** "Signature Required" against the generic table.
- Dry ice weight is kilograms-only on Shippo, four units on ShipEngine.
- Easyship's Boxes API is **metric by default**, switchable only through a
  dashboard setting — a unit dependency invisible to the API contract.
- Easyship `shipments_update` **silently nulls** fields outside the resulting
  `coc_type` group, with no error.
- DIM rounds up; cubic rounds down.


---

## Verification status of this document, and of the code

Written after an independent review specifically looking for claims made without
a real check. Three classes are distinguished, because collapsing them is how the
hallucinated endpoints got in.

### Measured against a live API

- Hazmat changes rate eligibility: Shippo 11 rates to 3, A/B tested.
- Residential surcharge: exactly $6.15 on Easyship, A/B tested across
  commercial / residential / unknown.
- Flat-rate templates: Shippo `USPS_FlatRateEnvelope` 2 rates at $9.62,
  `USPS_SmallFlatRateBox` 1 rate at $10.59; ShipStation v1
  `packageCode: flat_rate_envelope` 2 rates at $9.62.
- International purchase with a customs declaration: Shippo test token,
  `LS001790923US`, US to Toronto, DDP.
- Easyship DDP incoterm, item hazmat flags, `set_as_residential`, surcharge
  parsing, tracking legs.

### Read from a provider's OpenAPI specification but never sent

Correct as far as the schema goes, unexercised as behaviour:

- ShipEngine `advanced_options` hazmat, `dangerous_goods_contact`,
  `dry_ice_weight`, `delivered_duty_paid`, `package_code`, `insured_value`,
  `address_residential_indicator: "unknown"`. Blocked: production keys only.
- Shippo `extra.dry_ice`, `extra.alcohol`, `extra.insurance`, `is_residential`,
  `street3`. Wired and unit-tested, not A/B tested live.
- EasyPost `street3`. Nothing else changed there.
- ShipStation v1 `street3`, `residential`.

### Taken from secondary research and never verified first-hand

Cited here because they motivated design decisions, and flagged because a
downstream reader would otherwise take them as measured:

- **UPS US residential surcharge $6.60 per package.** From a UPS rate-change
  page via a research pass, not read directly. The $6.15 figure in this document
  *is* measured, on Easyship; the $6.60 is not. `models.py` repeats the $6.60 in
  a docstring and should be read with that caveat.
- **Every USPS DIM and nonstandard-fee number in section 5** — divisor 139,
  effective 12 July 2026, the 1 cubic foot threshold, zones 1 to 9, the $4.50 /
  $10.00 / $21.00 nonstandard fees, the $3.00 dimension noncompliance fee, cubic
  rounding direction. Sourced to USPS pages through a research pass. Not one of
  them is exercised by shipzil, because dimensional weight is not modelled at
  all, so nothing in the code depends on them being right. Verify before acting.
- **USPS hazmat handling fee, 12 July 2026.** Same provenance.
- **"ShipStation v1 has no hazmat fields."** This is absence of evidence from six
  scraped HTML pages, not proof of absence. `hazmat_fields = frozenset()` is the
  safe reading either way, since under-claiming only produces a warning.

### Deliberately not claimed

**Superseded.** This previously said `EasyPostAdapter.hazmat_fields` is empty
because `docs.easypost.com` had not been consulted. It has now been read:
`hazmat_fields` is `{dry_ice, contains_alcohol}`, which is what the adapter
emits, not the larger set EasyPost supports. Lithium and the fully regulated
classes remain unclaimed on purpose — mapping PI965/966/967 onto EasyPost's
`CLASS_9_*` values is a regulatory classification, not a rename.

### Still unresolved

Shippo may be misclassified as fan-out only, from a single probe that returned
zero rates alongside a *"UPS — Hard: Too Many Requests"* message. Both
`README.md` and `docs/API-REALITY.md` assert it. Needs re-probing across carrier
accounts.

Easyship returns zero rates for DDP on the free sandbox while returning four for
DDU on the same lane. shipzil's payload matches the published v2024-09 schema, so
the working hypothesis is a plan gate or a courier that does not support DDP on
this lane, not a shipzil defect. Confirming it needs one `/couriers` call to read
`supported_incoterms`, which the spent sandbox allowance currently blocks. Until
then, treat Easyship DDP as unproven in both directions: not shown broken, not
shown working.

Neither ShipStation adapter has ever bought an international label. v1 and v2
customs are asserted only against the request body shipzil would send, because
the only ShipStation credentials available are production and nothing mutating
has been sent with them. A payload assertion catches an unwired builder or a
misspelled field; it cannot catch a field ShipStation rejects at purchase.
