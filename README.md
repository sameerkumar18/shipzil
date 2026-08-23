# shipzil

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
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
pip install -e .
```

Zero runtime dependencies. Python 3.10 or newer, standard library only.

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
| ShipStation v1 | planned | emulated | planned | planned | see [roadmap](#roadmap) |

"Emulated" means shipzil rates each parcel and combines them, and labels the
result so you know it was combined rather than quoted. More on that below.

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

```python
client = shipzil.Client(adapter, max_spend="50", dry_run=True)
```

## Synchronous on purpose

Every call is request/response. Where a provider is genuinely async, shipzil
polls and hands back a finished result. No futures, no callbacks, no event
loop. If you want concurrency, run it in a thread pool.

## Roadmap

Shipped:

- EasyPost, including automatic `/orders` routing for multi-parcel
- ShipStation v2, native `packages[]`
- Shippo, with its prose failures normalised into structured exclusions
- Easyship, including item-to-box packing
- Multi-parcel emulation for the four surfaces that lack it
- Buy and void, spend limits, `dry_run`
- 50 tests, including parser tests against captured real payloads

Next, roughly in order:

- **Real idempotency across all providers.** Today only EasyPost sends the key
  on the wire. The others accept the argument and drop it, which is exactly the
  kind of quiet lie this library exists to prevent. Either it gets sent or the
  adapter says it can't.
- **ShipStation v1**, the last unbuilt surface. Poor rate shape, single-parcel
  only, but a lot of ShipStation users are still on it.
- **PyPI release** as v0.2.0, so the install instructions above get shorter.
  The name is currently unclaimed, not reserved.
- **Failover**, `Client(primary=..., fallback=[...])`.

Later, once the core is boring and stable:

- Rate shopping across several providers in one call
- Tracking, and webhook payload normalisation
- Customs and duties beyond the minimum Easyship demands
- An async client, if there is real demand for one

Not planned, so you can rule shipzil out quickly: address validation,
insurance, manifests, batch, returns. Every one of those pulled the previous
attempt at this library out of shape. If it isn't on the path to getting a
label out the door, it isn't in v1.

## Development

```bash
pip install -e ".[dev]"
pytest -m "not live"    # offline, no credentials needed
pytest -m live          # hits real sandboxes, needs .env
ruff check shipzil tests
mypy shipzil
```

Live tests refuse to run against a production key.

Parsers are tested against real captured responses in `tests/fixtures/`, not
hand-written dicts. This is not pedantry. A fixture you write by hand encodes
the same assumption as the parser it is checking, so it cannot catch a wrong
field name. The Easyship carrier mapping read two fields that do not exist and
returned an empty carrier on every single rate, and its hand-written test
passed the whole time.

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
