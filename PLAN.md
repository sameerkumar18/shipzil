# shipzil — implementation plan

A Python library for parcel shipping across providers, designed to be driven by
an agent as comfortably as by a human.

Grounded in `docs/API-REALITY.md`, which is live-probe evidence rather than
documentation. Read that first; every decision below traces to something in it.

## What this is for

Not portability. Nobody switches shipping providers on a Tuesday.

The problem is that **all five provider surfaces answer "can you ship these three
boxes?" differently, and three of them answer wrongly without erroring.** EasyPost
ignores the array. Shippo returns `SUCCESS` with no rates. ShipStation v1 400s.
An integration built against one provider's behaviour is silently wrong on the
others, and an agent given an empty rate list will invent a reason for it.

So: **one honest answer, with the reasons attached.**

## The data model

Shipment-focused, always multi-parcel, provider-agnostic.

```python
Shipment(
    from_address=Address(...),
    to_address=Address(...),
    parcels=[Parcel(...), Parcel(...)],   # always a list, even at length 1
)
```

### Parcel has two modes, because Easyship forces it

```python
# Mode A — box-centric. EasyPost, Shippo, ShipStation.
Parcel(weight=Weight(16, "oz"), dimensions=Dimensions(10, 8, 4, "in"))

# Mode B — item-centric. Easyship can pack these for you.
Parcel(items=[Item(sku="TSHIRT-M", quantity=2), Item(...)])
```

A parcel must resolve to *something shippable*. Adapters declare
`requires_explicit_dimensions`; those that do reject Mode B with a structured
exclusion rather than guessing a bounding box. **The library never invents
dimensions** — that would produce a quote the carrier will not honour.

### Rates come back as a quote, not a list

This is the central design decision, and ShipStation v2 already proves the shape:

```python
quote = client.get_rates(shipment)

quote.rates       # [Rate, ...]        what actually came back
quote.excluded    # [Exclusion, ...]   what could not be rated, and why
quote.via         # "easypost:orders"  which resource was used — debug only
```

```python
Exclusion(
    carrier="usps",
    code="multipackage_not_supported",
    message="carrier 30718 does not support multipackage",
    source="provider",   # or "shipzil" when we normalised prose into a code
)
```

**Partial success with structured reasons.** An agent asking for three parcels
gets UPS Ground at $25.27 *and* the fact that USPS cannot do it — instead of a
shorter list and no explanation.

Where a provider only gives prose, the adapter normalises it into the same
`code` vocabulary and marks `source="shipzil"` so the inference is visible.
ShipStation v2's `error_code` values are the vocabulary; the others are mapped
onto it.

### Rate fields are optional because ShipStation v1 exists

```python
Rate(
    carrier: str,
    service: str,
    amount: Decimal,
    currency: str | None,        # v1 returns none
    delivery_days: int | None,   # v1 returns none
    guaranteed: bool | None,
    raw: dict,                   # escape hatch, always present
)
```

v1 splits cost across `shipmentCost + otherCost`; the adapter sums them. Anything
that requires `currency` or `delivery_days` must degrade rather than crash.

## Capability routing

The user asks for N parcels. The adapter decides how:

| Provider | 1 parcel | N parcels |
|---|---|---|
| EasyPost | `POST /shipments` | **`POST /orders`** — order-level aggregate rates |
| ShipStation v2 | `POST /v2/rates` | `POST /v2/rates` with `packages[]` |
| Shippo | `POST /shipments` | accepted but unrated → `Exclusion` |
| ShipStation v1 | `POST /shipments/getrates` | unsupported → `Exclusion` |
| Easyship | `POST /shipments` | native `parcels[]` |

Capabilities are **probed, not declared.** `scripts/probe_capabilities.py`
regenerates the matrix from live calls, because the documentation was wrong about
multi-parcel for two providers today.

## Agent-facing design

Four things an agent needs that a human does not:

1. **Capability truth.** Never an empty list without a reason. This is what
   `quote.excluded` is for, and it is the reason the library exists.
2. **Idempotency.** A retried label buy must not double-purchase. Client-supplied
   idempotency key, reconciliation on ambiguous timeout, explicit policy when the
   outcome cannot be determined.
3. **Guardrails.** `dry_run=True`, `max_spend`, confirmation before buy. Agents
   make expensive mistakes and a label is real money.
4. **Structured errors.** Typed exceptions carrying provider `messages`, never
   swallowed.

An MCP server over this is a thin later layer, not a v1 concern.

## Build order

Each step ends in something demonstrable. Deliberate — the previous attempt
stalled because value only arrived at high parity.

**Status:** steps 1–4 done and verified live; Easyship added as a fifth surface
once the key arrived. Step 5 partially done (buy/void exercised on Shippo).
45 offline tests plus 5 live tests, ruff and mypy strict clean.

1. ✅ **Models + probe harness.** `Address`, `Parcel`, `Item`, `Weight`,
   `Dimensions`, `Shipment`, `Rate`, `Exclusion`, `Quote`.
   `scripts/probe_capabilities.py` regenerates the matrix.
   *Ends with:* the capability table, generated from live calls.

2. ✅ **EasyPost adapter, including Orders routing.** The only surface that fully
   works for multi-parcel today.
   *Ends with:* three parcels rated for real, `via="easypost:orders"`.

3. ✅ **ShipStation v2 adapter.** Native `packages[]`, and the exclusion vocabulary
   comes from here.
   *Ends with:* partial success — 7 rates plus a USPS exclusion.

4. ✅ **Shippo adapter + prose normalisation.** Turn *"doesn't support one or more
   shipment options"* into `multipackage_not_supported`.
   *Ends with:* Shippo answering honestly instead of returning nothing.

4b. ✅ **Easyship adapter.** Only surface that packs items into a box. Forced
   three model changes: `Item.dimensions`, `can_derive_box_from_items`, and
   idempotent-POST retries. Live-verified before its sandbox quota ran out.

5. 🔜 **Guardrails + idempotency.** `dry_run`, `max_spend`, idempotency keys, the
   buy/void path. Buy/void proven on Shippo; `max_spend` and keys still to do.

6. 🔜 **Failover.** `Client(primary=..., fallback=[...])`, once single-provider is
   solid.

Deferred: ShipStation v1 (poorest rate shape, single-parcel only — worth an
adapter mainly for coverage), async, customs/duties, returns, batch.

## Testing rule earned the hard way

Parsers are tested against **captured real responses** in `tests/fixtures/`, not
hand-written dicts. A hand-written fixture encodes the same assumption as the
parser it checks, so it cannot catch a wrong field name. Proven, not assumed:
the Easyship carrier mapping read two fields that do not exist and returned an
empty carrier on every rate, while its hand-written test passed. Restoring that
bug fails `tests/test_real_payloads.py` and leaves the rest of the suite green.

Fixtures are scrubbed of contact details and credentials, structure untouched.
Re-capture with `scripts/probe_capabilities.py`; `.probe/` stays gitignored.

## Scope fence

If it is not on the path to getting a label out the door, it is not in v1. That
rule deletes address validation, insurance, manifests, batch, returns and
tracking — the sprawl that made the previous attempt confusing.

## Open decisions

- **ShipStation v1: adapter or skip?** Its rate shape lacks currency and delivery
  days, and it cannot multi-parcel. It exists mainly because ShipStation's own
  users are still on it.
- **Easyship sandbox quota is finite** and is now spent. Its plan allowance
  returns `403`, not `429` — see docs/API-REALITY.md. Live Easyship tests can
  fail for reasons unrelated to the code, so treat them as advisory.
- **No version control yet.** 14 modules and 50 tests exist with no history.
- **Sync only for v1** — proposed, keeps the adapter surface half the size.
- **Test-mode label buys** — needed to verify buy/void and idempotency for real.
