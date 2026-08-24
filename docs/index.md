# shipzil

One Python interface for **EasyPost, Shippo, ShipStation and Easyship** — with
multi-parcel support on all of them, including the four surfaces that cannot do
it natively.

```python
import shipzil as z
from shipzil.providers import EasyPostAdapter

client = z.Client(EasyPostAdapter("EZTK..."))
quote = client.get_rates(shipment)
label = client.buy(shipment, min(quote.rates, key=lambda r: r.amount))
print(label.tracking_number)
```

shipzil sits on top of the shipping accounts you already have. It is a **client
library, not a service**, and not a replacement for your provider. You keep your
EasyPost or Shippo contract, your negotiated rates and your carrier connections.
What you stop doing is rewriting your integration every time you add or switch
one.

!!! warning "Alpha, and not yet on PyPI"
    `shipzil` is version 0.1.0 and the repository is still private. The API can
    still change. See the [roadmap](roadmap.md) for what lands at v0.2.0.

## What makes this different

Most wrappers hide the providers behind a clean-looking interface and quietly
lose whatever does not fit. shipzil is built the other way round: the design
started by measuring five real APIs and writing down where they disagree, and
those disagreements are surfaced rather than smoothed over.

<div class="grid cards" markdown>

-   **Multi-parcel actually works**

    Only two of six provider surfaces can rate multiple parcels natively. Three
    of the rest fail *without raising an error* — they return `200 OK` with an
    empty rate list. shipzil emulates the gap and labels the result, so you can
    tell a native quote from a combined one.

-   **Short rate lists come with reasons**

    When a carrier drops out, you get an `Exclusion` explaining why, not a
    shorter list. See [Errors and exclusions](errors.md).

-   **Customs is per-provider, because it has to be**

    Two providers want the per-unit customs value; three want the line total.
    Getting that backwards multiplies your declared value by the quantity.
    shipzil declares the basis per provider. See
    [International shipping](international.md).

-   **Nothing is guessed**

    Where shipzil cannot derive a value from your data, it refuses instead of
    inventing one — an EEI exemption above $2,500 being the clearest case.

</div>

## Install

Not on PyPI yet. If you have repository access:

```bash
git clone git@github.com:sameerkumar18/shipzil.git
cd shipzil
uv sync
```

**Zero runtime dependencies**, standard library only. Tested on CPython 3.9
through 3.14.

## Where to go next

| If you want to… | Read |
|---|---|
| get a label out the door | [Quickstart](quickstart.md) |
| understand the object model | [Concepts](concepts.md) |
| know what each provider supports | [Providers](providers.md) |
| ship across a border | [International shipping](international.md) |
| handle failures properly | [Errors and exclusions](errors.md) |
| look up a class or field | [Reference](reference.md) |
| know what is coming | [Roadmap](roadmap.md) |

## A note on the "Provider research" section

The [API reality](API-REALITY.md) and [Gaps](GAPS.md) documents are unusual for a
library's docs: they record what was measured against each provider, what was
only read in a specification, and what is still unverified — including mistakes
made along the way and later corrected.

They are published deliberately. If you are trusting a library with label
purchases, you should be able to see which of its claims were tested and which
were assumed. [Why this section exists](research.md) explains the reasoning.
