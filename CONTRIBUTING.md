# Contributing

## Adding a provider

You write one file. Nothing in `shipzil/` needs to change.

This is verified, not aspirational: an adapter defined entirely outside the
package gets rating, multi-parcel fan-out, exclusion de-duplication, `max_spend`,
`dry_run`, and the refusal to buy a synthesized rate, purely by implementing two
methods.

```python
from shipzil.models import Exclusion, ExclusionCode, Label, Quote, Rate, Shipment, Strategy
from shipzil.providers import Adapter, Capabilities


class AcmeCapabilities(Capabilities):
    native_multi_parcel = False        # shipzil will fan out for you
    returns_currency = True
    returns_delivery_estimate = False  # say so; do not invent one


class AcmeAdapter(Adapter):
    name = "acme"
    capabilities = AcmeCapabilities()

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def is_test_mode(self) -> bool | None:
        return self.api_key.startswith("test_")   # None if you cannot tell

    def rate_single(self, shipment: Shipment) -> Quote:
        ...   # required

    def buy(self, shipment: Shipment, rate: Rate) -> Label:
        ...   # required, and must pass retries=0
```

That is the whole contract. `rate_native_multi` and `void` are optional; the base
class raises a clear `NotImplementedError` naming your provider if someone calls
one you did not implement.

To ship it in-tree, add two lines to `shipzil/providers/__init__.py` (the import
and `__all__`). For anything out-of-tree, you do not even need that — just
`Client(YourAdapter(...))`.

### The four rules

1. **Never return an empty `Quote.rates` without populating `Quote.excluded`.**
   An unexplained absence of rates is the bug this library exists to remove.
2. **Never invent input.** If the provider needs dimensions, a customs category,
   or a company name and the caller did not supply it, raise or exclude with a
   message naming the missing field. Do not substitute something plausible.
3. **Mark inferences.** `Exclusion(source="provider")` means the provider said
   it. `source="shipzil"` means you concluded it. Never blur the two.
4. **Never retry a purchase.** Pass `retries=0` on any request that spends money.
   A test walks the AST of every adapter to enforce this, so it cannot be
   forgotten by moving code into a helper.

### The two places you may have to touch shared code

Everything else is genuinely decoupled — no core module branches on a provider
name. These two are honest exceptions:

- **`ExclusionCode` in `models.py`** is a closed enum of 8 members. If your
  provider fails in a way none of them describes, add one. It is a `str` enum and
  dataclasses do not validate, so a bare string works at runtime for
  experimentation, but it will not type-check and should not be merged.
- **`normalize.py`** holds the central prose-to-code maps (`_PROVIDER_CODES` for
  machine-readable codes, `_PATTERNS` for error text). If your provider reports
  failures as prose, add patterns there rather than parsing text in your adapter.

## Verifying against a provider

The most valuable thing you can contribute is evidence.

**Prefer captured real responses over hand-written fixtures.** A fixture written
by hand encodes the same assumption as the parser it checks, so it cannot catch a
wrong field name. Put captured responses in `tests/fixtures/`, scrubbed of
contact details and credentials, structure untouched. Scrub on **exact key
names** — a substring match on `state` will silently redact `label_state`.

**Read the schema, not the prose.** Every hallucinated field in this library's
history came from trusting documentation prose. Easyship's own docs say to
"assign a courier using `courier_service_id`"; the actual `ShipmentCreate` schema
rejects it at the top level, because it is nested inside `courier_settings`.

**Know that a captured fixture only tests what its data exercises.** Every
`otherCost` in the ShipStation v1 sample is `0.0`, so that fixture cannot tell
`shipmentCost + otherCost` from `shipmentCost` alone. That arithmetic needs a
constructed case, and it is marked as such.

**Check your test has teeth.** Reintroduce the bug and watch the test fail. Two
tests in this repo looked protective and were not until that was done.

## Toolchain

```bash
uv sync                          # dev toolchain, pinned by uv.lock
uv run pytest -m "not live"      # offline
uv run pytest -m live            # needs credentials in .env
uv run ruff check shipzil tests
uv run mypy shipzil
```

Compatibility across the supported range:

```bash
for v in 3.9 3.10 3.11 3.12 3.13 3.14; do
  uv run --python $v --isolated --with pytest pytest
done
```

The library targets 3.9+; the dev tools do not (mypy and pytest 9 both dropped
it), which is why the dev group uses environment markers and mypy is configured
at 3.10 while ruff lints at `py39`. A real 3.9 test run is what actually guards
the floor.

## Live tests

Live tests refuse to run against a production key, and that guard is read as a
property:

```python
assert adapter.is_test_key, "refusing to run live tests against a production key"
```

If that ever becomes a method, the expression evaluates a bound method, which is
always truthy, and the guard silently always passes. There is a test asserting
both credential checks return `bool`. Do not remove it.

Where a provider offers a no-charge purchase path, use it and default to it:
ShipStation v1 has `testLabel: true`, and the adapter defaults it on because the
only v1 credentials that exist in practice are production.
