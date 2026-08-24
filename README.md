# shipzil

[![Python](https://img.shields.io/badge/python-3.9%20%E2%80%93%203.14-blue)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green)](#license)
[![Typed](https://img.shields.io/badge/mypy-strict-brightgreen)](#development)
[![Ruff](https://img.shields.io/badge/lint-ruff-purple)](#development)
[![Status](https://img.shields.io/badge/status-alpha-orange)](#roadmap)

One Python interface for EasyPost, Shippo, ShipStation and Easyship, with
multi-parcel support on all of them, including the four surfaces that can't do
it natively.

shipzil sits on top of the shipping accounts you already have. It is a client
library, not a service, and not a replacement for your provider. You keep your
EasyPost or Shippo contract, your negotiated rates, and your carrier
connections. What you stop doing is rewriting your integration every time you
add or switch one.

**Documentation:** [sameerkumar18.github.io/shipzil](https://sameerkumar18.github.io/shipzil/)
— quickstart, the object model, a per-provider support matrix, an international
shipping guide, and the [roadmap](docs/roadmap.md). Build locally with
`make docs`.

## The thing that made me build this

Ask five shipping APIs to rate three boxes. You get five different answers, and
three of them are wrong without telling you:

| Surface | 1 parcel | 3 parcels |
|---|---|---|
| EasyPost `/shipments` | 19 rates | **0 rates, HTTP 201** — the array is silently ignored |
| EasyPost `/orders` | n/a | 17 order-level rates |
| Shippo `/shipments` | 11 rates | **0 rates, HTTP 201, `status: SUCCESS`** |
| ShipStation v2 | 27 rates | 7 rates, plus a structured USPS exclusion |
| ShipStation v1 | 19 rates | **HTTP 400** |
| Easyship | 5 rates | **HTTP 422**, "No shipping solutions available" |

Every cell is measured against a live sandbox, not read off a docs page. The
raw evidence, including the exact error strings, is in
[docs/API-REALITY.md](docs/API-REALITY.md).

The failure mode that costs you a day: an empty rate list looks exactly like
"no service available." You ship nothing and never find out why. Hard-code one
provider's behaviour and your integration is quietly wrong on the rest.

## Install

Not on PyPI yet, and the repository is still private. Both change at v0.2.0,
which is what the [roadmap](#roadmap) is for. If you have access, install from
source:

```bash
git clone git@github.com:sameerkumar18/shipzil.git
cd shipzil
uv sync
```

Zero runtime dependencies, standard library only. Tested on CPython 3.9
through 3.14. Note that 3.9 reached end of life in October 2025 — it is
supported because the only thing it cost was `slots=True` on the dataclasses,
not because running it is a good idea.

## Usage

```python
import shipzil
from shipzil.providers import EasyPostAdapter

client = shipzil.Client(EasyPostAdapter(api_key))

shipment = shipzil.Shipment(
    from_address=shipzil.Address(street1="215 Clayton St", city="San Francisco",
                                 state="CA", postal_code="94117"),
    to_address=shipzil.Address(street1="1 Rockefeller Plaza", city="New York",
                               state="NY", postal_code="10020"),
    parcels=(
        shipzil.Parcel(weight=shipzil.Weight.of(16, "oz"),
                       dimensions=shipzil.Dimensions.of(10, 8, 4, "in")),
        shipzil.Parcel(weight=shipzil.Weight.of(32, "oz")),
        shipzil.Parcel(weight=shipzil.Weight.of(8, "oz")),
    ),
)

quote = client.get_rates(shipment)
print(quote.explain())
# 17 rate(s) via easypost:orders (order)

label = client.buy(shipment, quote.cheapest)
```

`parcels` is always a sequence, even when there is one of them. Picking the
provider resource that can satisfy the request is shipzil's job: EasyPost gets
routed to `/orders` here without you asking, and `quote.via` tells you what
happened if you care.

Swapping providers is the import line and the adapter:

```python
from shipzil.providers import ShippoAdapter
client = shipzil.Client(ShippoAdapter(api_key))   # same Shipment, same call
```

## Provider support

| Provider | Rating | Multi-parcel | Buy | Void | Notes |
|---|---|---|---|---|---|
| EasyPost | yes | native, via `/orders` | yes | yes | fullest support |
| ShipStation v2 | yes | native, `packages[]` | yes | yes | best exclusion reporting |
| Shippo | yes | emulated | yes | yes | reports failures as HTTP 201 |
| Easyship | yes | emulated | yes | yes | only surface that packs items into a box |
| ShipStation v1 | yes | emulated | yes | yes | no currency or delivery estimate; rates one carrier per call |

"Emulated" means shipzil rates each parcel and combines them, and labels the
result so you know it was combined rather than quoted. More on that below.

**"Yes" means implemented, which is not the same as proven.** Rating is verified
against live credentials on all five surfaces. Purchasing is not:

| | Rating | Purchase |
|---|---|---|
| EasyPost | live-verified | shipment: live-verified. order: verified against EasyPost's recorded traffic |
| Shippo | live-verified | live-verified (test token, buy and void) |
| ShipStation v2 | live-verified | route verified, purchase **never run** — production keys only |
| ShipStation v1 | live-verified | live-verified via `testLabel` (no charge) |
| Easyship | live-verified | live-verified (sandbox) |

ShipStation v1's purchase path was verified through `testLabel: true`, which
returns a real label response without buying postage — confirmed by an unchanged
account balance. Every `Label` carries `is_test`, which is `True`, `False`, or
`None` when the provider gives shipzil no way to tell (ShipStation v2 is the
only such case). A dry run always reports `True`.

## What "honest" means here concretely

I use that word a lot, so here is what it buys you.

### Short rate lists come with reasons

No silent omissions:

```python
quote = client.get_rates(three_boxes)
print(quote.explain())
# 7 rate(s) via shipstation_v2:rates (native)
#   excluded usps: multipackage_not_supported — carrier 30718 does not support
#   multipackage. unable to rate the shipment
```

The exclusion vocabulary is lifted from ShipStation v2, which is the only
surface that reports this per-carrier in a structured way. Everyone else's
prose gets normalised onto that vocabulary and tagged `source="shipzil"`, so
you can always tell an inference from something the provider actually said.

### Combined rates are labelled as combined

A carrier may price one consignment differently from the sum of its parts, so
shipzil never passes off arithmetic as a quote:

```python
rate.strategy        # Strategy.FANOUT
rate.is_synthesized  # True
rate.raw["amounts"]  # ["10", "12", "8"] — check the sum yourself
```

A combined rate is only offered when every parcel got a quote for that same
service. Two of three boxes is not a way to ship three boxes, so it becomes an
exclusion instead. And because a synthesized rate is not one label, `client.buy()`
refuses it rather than guessing which of several purchases you meant.

### Missing input raises an error

Supply items with no dimensions to a provider that requires a box and you get
`dimensions_required`. What you never get is an invented bounding box that
returns a rate the carrier won't honour. Easyship's customs categories work the
same way: there are exactly 20 of them, none is "other," and a category is a
customs declaration, so shipzil asks you instead of declaring one for you.

### Units convert once, at the boundary

Five providers, five conventions (oz/in, kg/cm, `"ounces"` vs `"ounce"`). Values
keep the unit you gave them and convert through `Decimal` inside the adapter, so
nothing drifts.

## Hazmat, duties and address class

These are not optional extras. A declared lithium battery cuts Shippo from 11
rates to 3, all USPS, because dangerous goods restrict service eligibility. Quote
without the declaration and 8 of those 11 are rates the carrier will refuse.

```python
parcel = shipzil.Parcel(
    weight=shipzil.Weight.of(16, "oz"),
    dimensions=shipzil.Dimensions.of(10, 8, 4, "in"),
    dangerous_goods=shipzil.DangerousGoods(
        lithium_batteries=shipzil.LithiumBatteryPacking.CONTAINED_IN_EQUIPMENT,
        un_number="UN3481", hazard_class="9", packing_group="ii",
    ),
    insured_value=100,
)
shipment = shipzil.Shipment(
    frm, to, (parcel,),
    duties_paid_by=shipzil.DutiesPaidBy.SENDER,   # DDP. Nothing is assumed.
)
```

Providers keep hazmat at three different levels — ShipEngine per product,
Shippo per shipment, Easyship per item — so anything a provider cannot carry comes
back as an exclusion instead of being dropped:

```
hazmat_detail_unsupported [shipzil] — shippo cannot carry these declared hazmat
details: regulated_detail. They will not reach the carrier, so the shipment may be
under-declared.
```

`DutiesPaidBy.UNSPECIFIED` is the default and sends nothing. An earlier version
hardcoded DDU, which silently made the **recipient** liable for import duty on
every international shipment.

`Address.address_class` is an enum, not a boolean, because a PO box is neither
residential nor commercial. Unknown is never downgraded to commercial: on Easyship
the residential surcharge measures **$6.15 per parcel**, and it is surfaced in
`Rate.surcharges` alongside fuel and remote-area components rather than buried in
one total.

Full inventory of what is still missing, taken from the providers' OpenAPI specs:
[docs/GAPS.md](docs/GAPS.md).

## International and customs

Items belong to the parcel that contains them, not to the shipment:

```python
parcel = shipzil.Parcel(
    weight=shipzil.Weight.of(16, "oz"),
    dimensions=shipzil.Dimensions.of(10, 8, 4, "in"),
    items=(
        shipzil.Item("cotton t-shirt", quantity=2, weight=shipzil.Weight.of(6, "oz"),
                     value=15, hs_code="610910", origin_country="US"),
    ),
)
shipment = shipzil.Shipment(frm, toronto, (parcel,),
                            duties_paid_by=shipzil.DutiesPaidBy.SENDER)
```

That nesting matches where the providers put it. Easyship uses
`parcels[].items[]`, ShipEngine uses `packages[].products[]` and has
**deprecated** its shipment-level `customs_items` in favour of it. Shippo's list
is flat, so shipzil concatenates — which works in that direction only, since a
flat list cannot be split back into boxes without inventing the assignment.

The EEI exemption is derived, not guessed. Below $2,500 declared value shipzil
asserts `NOEEI_30_37_a`, which follows from the value the caller already gave it.
Above that it refuses, because an AES filing and an ITN are needed and shipzil
cannot produce them — set `Shipment(eei_exemption="AES_ITN")` yourself.

Cross-border shipments where shipzil cannot build a declaration are refused at
**rating** time, not at purchase. Before this, Shippo returned four healthy
international rates and then failed the buy with *"Customs declaration is
required for international shipments via the USPS"*.

## Why not just use the provider's own SDK?

Use it, if you are certain you will only ever have one provider. The official
SDKs are good at the thing they are for.

shipzil is worth it when any of these is true:

- You run more than one provider, or expect to. Rate shopping across accounts,
  or a fallback for when one has an outage.
- You need multi-parcel and discovered your provider drops it silently.
- You are a platform shipping on behalf of many merchants with different
  carrier accounts.
- You want the option to leave. A provider-shaped integration is a switching
  cost, and that is by design.

If none of those apply, the honest answer is that you don't need this.

## Designed to be driven by agents too

An LLM buying a label needs guarantees a human developer can work around:

An empty result with a stated reason beats an empty result, because a model
with no reason will invent one. Spend limits and `dry_run=True` stop a bad
decision before the network call, not after. Errors are typed but keep the
provider's original message, which on three of these surfaces is the only place
the real failure ever appears.

Purchases are never retried, on any provider. Where the provider enforces an
idempotency key shipzil sends one; where it publishes no such header shipzil
refuses the key rather than accepting it and hoping. `adapter.supports_idempotency_key`
tells you which world you are in before you buy anything.

```python
client = shipzil.Client(adapter, max_spend="50", dry_run=True)
```

## Synchronous on purpose

Every call is request/response. Where a provider is genuinely async, shipzil
polls and hands back a finished result. No futures, no callbacks, no event
loop. If you want concurrency, run it in a thread pool.

## Roadmap

Full version, with dependencies and the reasoning: **[docs/roadmap.md](docs/roadmap.md)**.

The short form:

- **Shipped** — five providers, multi-parcel on all six surfaces, buy and void,
  customs and duty liability across all five with per-provider bases declared,
  spend limits, `dry_run`, 150 tests
- **Next** — PyPI release as v0.2.0, then **canonical carrier and service
  identity**, which everything else depends on
- **Then** — a **resilience-first router**: ask for "USPS Ground Advantage" and
  get it from the healthiest source in a merchant-configured preference order.
  Pinned per carrier-day so you do not end up with a manifest per source.
- **Later** — cross-provider rate shopping (blocked on service identity),
  tracking, `tax_identifiers` for EU traffic, USPS HS-code enforcement
- **Not planned** — address validation, insurance, manifest *generation*, batch,
  returns

Two decisions worth flagging, because they changed:

- Failover is a separate `Router` type, not `Client(primary=, fallback=[])`. That
  earlier shape holds no state, so it cannot pin a carrier-day and would fragment
  manifests on every failover.
- Manifest **awareness** is now in scope, where it was previously ruled out.
  Manifest *generation* is still out. A router that ignores manifest grouping
  creates an operational problem worse than the downtime it solves.

## Development

```bash
uv sync                      # dev toolchain, pinned by uv.lock
uv run pytest -m "not live"  # offline, no credentials needed
uv run pytest -m live        # hits real sandboxes, needs .env
uv run ruff check shipzil tests
uv run mypy shipzil
```

Compatibility across the supported range:

```bash
for v in 3.9 3.10 3.11 3.12 3.13 3.14; do
  uv run --python $v --isolated --with pytest pytest
done
```

The dev toolchain runs on 3.10+ only, because mypy and pytest 9 both dropped
3.9. That is why the dev group uses environment markers rather than dragging
the library's floor up to match its tools, and why mypy is configured at 3.10
while ruff lints at `py39` — mypy refuses to target 3.9 at all, so ruff plus a
real 3.9 test run is what actually guards the floor.

Live tests refuse to run against a production key.

Parsers are tested against real captured responses in `tests/fixtures/`, not
hand-written dicts. This is not pedantry. A fixture you write by hand encodes
the same assumption as the parser it is checking, so it cannot catch a wrong
field name. The Easyship carrier mapping read two fields that do not exist and
returned an empty carrier on every single rate, and its hand-written test
passed the whole time.

## Adding a provider

One file. Nothing in `shipzil/` changes — implement `rate_single` and `buy` on a
subclass of `Adapter` and you inherit multi-parcel fan-out, exclusion
de-duplication, `max_spend`, `dry_run`, and the refusal to buy a synthesized
rate. No core module branches on a provider name. Full contract, and the two
places you may legitimately have to touch shared code, in
[CONTRIBUTING.md](CONTRIBUTING.md).

## Contributing

Issues and pull requests welcome, particularly:

- Provider behaviour that contradicts [docs/API-REALITY.md](docs/API-REALITY.md).
  Include the request and response and I will re-probe it.
- Adapters for surfaces not covered here.
- Any place shipzil states something as fact that it actually inferred. That is
  the bug class I care most about.

Run `ruff`, `mypy` and the offline tests before opening a PR.

## Get in touch

If you are integrating more than one shipping provider and something here is
wrong, missing, or nearly-but-not-quite what you need, email me directly:
**sam@sameerkumar.website**.

I am especially interested in hearing from you if you have a requirement that
doesn't fit the current design, or a provider you need supported. Happy to talk
about working on it together.

## License

MIT.
