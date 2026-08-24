# Roadmap

Where shipzil is, and where it is going. Dates are deliberately absent; ordering
and dependencies are not.

The medium-term direction is a **carrier-first router**: a merchant asks for
"USPS Ground Advantage", and shipzil obtains it from the healthiest available
source — the USPS API directly, or EasyPost, or Shippo — according to a
preference order the merchant sets. The value is uptime, not arbitrage.

---

## Shipped

- **Five providers**: EasyPost (with automatic `/orders` routing), ShipStation v2
  (native `packages[]`), Shippo (prose failures normalised into structured
  exclusions), Easyship (including item-to-box packing), ShipStation v1 (legacy,
  rates per carrier and merges)
- **Multi-parcel** on all six surfaces, emulated on the four that lack it, and
  always labelled so a combined rate is never mistaken for a native one
- **Buy and void**, spend limits, `dry_run`
- **Honest idempotency**: EasyPost enforces a key; the providers that publish no
  such header refuse one rather than silently discarding it
- **Customs on all five providers**, with the per-unit-versus-line-total basis
  declared per provider, EEI citations rendered per provider, and refusal above
  the \$2,500 threshold rather than a fabricated exemption
- **Duty liability** mapped through one shared renderer, with
  `DUTIES_UNSUPPORTED` reported where a provider has no field
- **150 tests**, including parser tests against captured real payloads, and
  payload-level tests asserting what each adapter puts on the wire

---

## Next

### v0.2.0 — make it installable

- **PyPI release.** The name is currently unclaimed, not reserved.
- **Public repository**, so the docs can be hosted and issues filed.
- Nothing else. This release is about distribution, not features.

### A1 — canonical carrier and service identity

**The prerequisite for everything below**, and load-bearing sooner than it looks.

Today `Rate.carrier` and `Rate.service` are whatever the provider called them. The
same USPS service appears as:

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

Identity is the **triple** `(carrier, service, packaging)`, not a pair: v1 returns
two rates sharing `usps_ground_advantage`, differing only by packaging.

Planned:

- `CarrierId` / `ServiceId` value types on `Rate`, **nullable** — `None` where a
  mapping is not confident, never a guess
- mapping declared per adapter as a class attribute, following the existing
  `customs_value_basis` / `eei_style` / `incoterm_style` pattern
- mappings derived from captured traffic, with a test per provider
- scoped to USPS first, and only the services in real use

!!! warning "Why this is needed for failover, not just for price comparison"
    Failover has to land on the **same** service. A wrong mapping silently ships a
    different service, which is worse than the outage being routed around. So this
    is safety-critical even with no cross-provider price comparison anywhere in
    the picture.

### A2 — shared source vocabulary

The library and the [status page](https://github.com/sameerkumar18/shipzil) do not
agree on what a source is called. The library has five adapters
(`easypost`, `shippo`, `shipstation_v1`, `shipstation_v2`, `easyship`); the status
feed has four platforms (`easypost`, `shippo`, `shipstation`, `shipengine`).
`easyship` is absent, and `shipstation` / `shipengine` do not map cleanly onto
`v1` / `v2`. Reconcile before anything consumes the feed programmatically.

### A3 — the manifest group, written down

A first-class constraint before anything is designed around it implicitly. See
[the manifest problem](#the-manifest-problem-and-a-reversed-decision) below.

---

## Then — the router

Design decisions already made, so the shape is not open:

| Decision | Choice | Consequence |
|---|---|---|
| What drives v1 | **Resilience**, not price | no cross-provider price comparison, so no service-equivalence problem to solve first |
| Routing granularity | **Carrier-day**, pinned | one manifest normally; a second only during an incident |
| Neutrality | **Strictly neutral**, merchant-configured | no built-in default ordering, enforced by a test |

Planned shape:

- `Router` as a **distinct type**, not a `Client` parameter. `Client(adapter)`
  stays exactly as it is.
- **Stateless.** Carrier-day pinning needs state; shipzil returns a routing
  decision plus a serializable pin, and the caller persists it. This preserves the
  zero-dependency, no-persistence property the library trades on, and lets a
  hosted service persist it later without changing the library.
- **Health injected**, combining the external status feed with local circuit
  state. Local matters independently: account-level outages are invisible to a
  global status page — Easyship returning `403 "API usage limit exceeded"` being a
  worked example. The error taxonomy already distinguishes `RateLimitError` from
  `AuthenticationError`, which is exactly the classification a breaker needs.
- **Failover policy:** route reads freely; **never** auto-fail-over a write after
  an ambiguous failure. `AmbiguousPurchaseError` exists for precisely this.

!!! note "This supersedes the old plan"
    An earlier roadmap described failover as `Client(primary=..., fallback=[...])`.
    That shape cannot express carrier-day pinning, because it holds no state, so it
    would fragment manifests on every failover. The `Router` type replaces it.

### Carrier-first

Once the router exists, a carrier-direct adapter is just an `Adapter` that returns
one carrier's rates — the interface already accommodates it. First step is a
**USPS-direct, rating-only** adapter: it proves the seam survives carrier-first and
surfaces the credential, manifest and postage-payment shape early, without
committing to a purchase path.

---

## The manifest problem, and a reversed decision

A manifest (USPS SCAN form, FedEx close-out, UPS end-of-day) groups labels bought
on **one account** into one physical handoff. Route 100 labels across three sources
and you hand the driver three manifests.

This creates a real tension, and the two goals want different answers:

| Goal | Frequency | Manifest cost |
|---|---|---|
| Failover during an outage | episodic | one extra manifest, occasionally — acceptable |
| Cheapest-rate arbitrage | every label | continuous fragmentation — not acceptable |

So the router's unit of decision is a **manifest group** (carrier × account × day),
not a label. Pin a primary source per carrier per day; deviate only on outage.

!!! warning "This reverses a previous 'not planned'"
    An earlier roadmap listed manifests under *not planned*, on the grounds that
    they pulled a previous attempt at this library out of shape. That still holds
    for **manifest generation**, which remains out of scope. But **manifest-group
    awareness** is now a routing prerequisite: a router that ignores it produces an
    operational problem worse than the downtime it solves. Awareness in, generation
    out.

---

## Later

- Rate shopping across providers in one call — **blocked on A1**, and on
  accessorial equivalence. "Cheapest" is only meaningful if the services are
  genuinely comparable, and equivalence is subtle: requesting DDP on EasyPost drops
  the rate list from 18 to 14 because four services will not carry it. A naive
  cheapest-wins router can silently select a rate that cannot carry the shipment's
  requirements.
- Tracking, and webhook payload normalisation
- `tax_identifiers` — VAT, EORI, IOSS. Effectively mandatory for commercial EU
  traffic and currently the largest customs gap.
- USPS six-digit HS code enforcement in `customs_gap`, required on every item since
  1 September 2025
- Third-party duty billing — EasyPost `options.duty_payment`, ShipStation
  `canada_delivered_duty`
- Wider incoterms: `FCA`, `DAP`, `eDAP`
- An async client, if there is real demand

---

## Not planned

So you can rule shipzil out quickly:

- **Address validation** — use your provider's
- **Insurance** — likewise
- **Manifest *generation*** — awareness only, see above
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
