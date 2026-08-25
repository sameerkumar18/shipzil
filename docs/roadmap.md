# Roadmap

Where shipzil is and where it is going. Ordering and dependencies are real; dates
are deliberately absent.

shipzil is a **shipping gateway**: one Python interface to every provider, with a
stable address for each carrier service, and provider differences surfaced rather
than hidden. The near-term goal is that adding or switching a provider is a
configuration change rather than a rewrite.

---

## Shipped

- **Five providers** — EasyPost (with automatic `/orders` routing for
  multi-parcel), ShipStation v2 (native `packages[]`), Shippo (prose failures
  normalised into structured exclusions), Easyship (including item-to-box packing),
  ShipStation v1 (legacy: rates per carrier, then merges)
- **Multi-parcel on all six surfaces**, emulated on the four that lack it, and
  always labelled so a combined rate is never mistaken for a native one
- **Normalised errors** — an 11-code exclusion taxonomy, provider prose parsed into
  structured reasons, and a short rate list that always explains itself
- **Buy and void**, spend limits enforced before any network call, `dry_run`
- **Customs on all five providers** — per-unit versus line-total basis declared per
  provider, EEI citations rendered per provider, and refusal above the \$2,500
  threshold rather than a fabricated exemption
- **Duty liability** through one shared mapping, with `DUTIES_UNSUPPORTED` reported
  where a provider has no field for it
- **Honest idempotency** — EasyPost enforces a key; providers that publish no such
  header refuse one rather than silently discarding it
- **150 tests**, including parser tests against captured real payloads and
  payload-level tests asserting what each adapter puts on the wire

---

## Next

### v0.2.0 — make it installable

- **PyPI release.** The name is currently unclaimed, not reserved.
- **Public repository**, so docs can be hosted and issues filed.

Distribution only. No features.

### Stable service addressing

Today `Rate.carrier` and `Rate.service` are whatever the provider called them. The
same USPS service arrives as:

```
EasyPost         USPS                             | GroundAdvantage
Shippo           USPS                             | Ground Advantage
ShipStation v2   USPS                             | USPS Ground Advantage
ShipStation v1   USPS Ground Advantage - Package  | usps_ground_advantage
```

Carrier names fragment too — `USPS`, `UPSDAP`, `UPS`, `UPS® Ground`,
`FedExDefault` — and on ShipStation v1 USPS rates arrive under carrier code
`stamps_com`, so the "carrier" field is the reseller channel rather than the
carrier.

Every rate gets a stable, addressable identifier:

```
{provider}-{carrier}-{service}

easypost-usps-groundadvantage
shippo-usps-ground_advantage
shipstation_v2-usps-usps_ground_advantage
```

Planned:

- a structured `ServiceId` on `Rate` — provider, carrier, service, and optional
  packaging, with a `.slug` for display and storage
- **`carrier` normalised**, because it is a small closed set and it fixes
  `stamps_com` appearing where `usps` belongs
- **`service` left as the provider spells it**, because normalising it means
  deciding which services are equivalent, and that is a separate problem with a
  much higher correctness bar
- packaging included where a provider needs it: v1 returns two rates sharing
  `usps_ground_advantage`, differing only by packaging

!!! note "Why the provider is part of the address"
    `easypost-usps-groundadvantage` and `shippo-usps-ground_advantage` are
    different addresses on purpose. They may well be the same service, but
    asserting that is an equivalence claim, and an incorrect one silently ships a
    different service than the caller asked for. The gateway addresses what
    exists; it does not yet claim what substitutes for what.

### Service catalog

`client.services()` — what you can address through this provider, with the
capability flags already declared per adapter (customs basis, hazmat fields,
multi-parcel support). Discovery is what makes addressing usable.

### Configured fallback

Declarative, caller-stated, no decisions made on your behalf:

```python
order=[...]              # try these providers in this sequence
allow_fallbacks=True     # or fail rather than moving on
only=[...]              # pin, and never leave this provider
```

You state the policy; shipzil executes it. Anything where shipzil chooses for you
is a later and separate concern.

---

## Later

- **Intelligent routing** — selection informed by provider health and outcome, as
  opposed to the declarative fallback above. Design in progress, and it depends on
  addressing and the catalog landing first.
- **Carrier-direct adapters**, starting with a USPS rating-only adapter. The
  `Adapter` interface already accommodates a provider that returns one carrier.
- Tracking, and webhook payload normalisation
- Cost aggregation and per-provider observability
- `tax_identifiers` — VAT, EORI, IOSS. Effectively mandatory for commercial EU
  traffic and currently the largest customs gap.
- USPS six-digit HS code enforcement, required on every item since 1 Sept 2025
- Third-party duty billing — EasyPost `options.duty_payment`, ShipStation
  `canada_delivered_duty`
- Wider incoterms: `FCA`, `DAP`, `eDAP`
- An async client, if there is real demand

### On cross-provider rate comparison

Frequently requested, and deliberately not promised yet. Comparing prices requires
knowing that two services are interchangeable, and equivalence is subtle: requesting
DDP on EasyPost drops the rate list from 18 to 14 because four services will not
carry it. A comparison that ignores that silently selects a rate which cannot carry
the shipment. Addressing comes first, equivalence after, and only then price.

There is also an operational constraint worth stating plainly: labels are grouped
into carrier manifests by account, so spreading a day's labels across providers
multiplies the manifests a warehouse has to hand over. Any routing shipzil offers
has to respect that, which rules out naive per-label price shopping.

---

## Commitments

Two, because they affect whether shipzil is worth trusting rather than what it can
do.

**Neutrality.** shipzil will not let commercial relationships shape provider
ordering. There is no built-in default preference; ordering is yours to configure,
and that will remain enforced by a test rather than by good intentions.

**Verification, stated honestly.** Every claim in these docs is marked as measured,
read from a specification, secondary research, or unverified. See
[why that section exists](research.md).

---

## Not planned

So you can rule shipzil out quickly:

- **Address validation** — use your provider's
- **Insurance** — likewise
- **Manifest generation** — shipzil will respect manifest grouping when routing, but
  it will not produce SCAN forms or close-outs
- **Batch and returns**

Each of these pulled a previous attempt at this library out of shape. If it is not
on the path to getting a label out the door, it is not in v1.

---

## Known unverified

Published because a library handling label purchases should be auditable:

- Neither **ShipStation adapter has ever bought an international label** — no
  non-production credentials exist, so their customs handling is asserted against
  the request body shipzil would send, not against a carrier accepting it
- **ShipStation v2 purchase has never been run** at all
- **Easyship DDP** returns zero rates on the free sandbox where DDU returns four.
  The payload matches the published schema, so the likely cause is a plan gate or a
  lane no connected courier serves — unproven in either direction.
- EasyPost's **lithium hazmat classes** are deliberately unmapped: choosing among
  `CLASS_9_NEW_LITHIUM_INDIVIDUAL`, `_DEVICE`, `_UNMARKED` and `_USED` from
  PI965/966/967 is a regulatory classification, not a rename

Full detail in [API reality](API-REALITY.md) and [Gaps](GAPS.md).
