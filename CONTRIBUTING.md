# Contributing

## Development setup

```bash
uv sync
make check
```

Useful commands:

```bash
make test           # offline tests only
make test-live      # loads .env and calls real providers
make check-compat   # offline tests on Python 3.10 through 3.14
make docs           # local docs server
make docs-build     # types, static docs and agent outputs
```

Do not run `uv run pytest -m live` expecting `.env` to load automatically. Use
`make test-live` or pass `--env-file .env` explicitly.

## Adding an adapter

```python
from shipzil.models import Label, Rate, Shipment
from shipzil.providers import Adapter, Capabilities, Quote


class AcmeAdapter(Adapter):
    name = "acme"
    capabilities = Capabilities(
        native_multi_parcel=False,
        returns_currency=True,
        returns_delivery_estimate=False,
    )

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def rate_single(self, shipment: Shipment) -> Quote:
        ...

    def buy(self, shipment: Shipment, rate: Rate) -> Label:
        ...
```

`rate_single()` and `buy()` are required. Implement `rate_native_multi()` and
`void()` only when supported.

An out-of-tree adapter is configured through the Gateway:

```python
gateway = shipzil.Gateway({"acme": AcmeAdapter(api_key)})
```

An in-tree adapter also needs an export and registry entry in
`shipzil/providers/__init__.py`.

## Adapter rules

1. Populate `Quote.excluded` when the provider returns no rates and supplies a
   reason. Do not claim reasons for services the provider silently omitted.
2. Do not fabricate dimensions, contents, customs values, categories or company
   names. Return an exclusion or validation error naming the missing input.
3. Use `Exclusion(source="provider")` for provider output and `source="shipzil"`
   for local validation or filtering.
4. Set `Rate.provider`, `Rate.service_key` and provider purchase tokens.
5. Pass `retries=0` on purchase, cancel and refund requests.
6. Add only dangerous-goods fields the adapter sends on the wire to
   `hazmat_fields`.

Shared code changes may be required when a provider introduces a new exclusion
code or carrier spelling. Add those centrally in `models.py`, `normalize.py` or
`services.py`; do not branch shared behavior on provider names.

## Evidence and tests

Provider changes need:

1. A current provider-owned schema or model page for request fields.
2. A payload test proving the value reaches the outgoing request.
3. A sanitized provider response for parser tests when available.
4. A constructed edge case when the captured response cannot exercise the logic.
5. A live read-only or sandbox call when credentials and provider safety allow.

Sanitize credentials, addresses, emails, phone numbers, tracking ids and label
bytes. Preserve response shape and enum values needed by the parser. Document any
synthetic annotations in `tests/fixtures/README.md`.

Before keeping a regression test, restore the bug temporarily and confirm the test
fails.

## Live-test safety

- Shippo purchase tests assert that the token starts with `shippo_test_`.
- ShipStation v2 live tests are rating only.
- ShipStation v1 has no sandbox. The retained no-charge evidence covers one
  Stamps.com/USPS `testLabel` response; do not generalize that to every carrier.
- Easyship sandbox calls consume finite quota.

Review a live marker before running it. Never place production purchase credentials
in CI.

## Maintainer release process

A git install resolves a commit, not the working tree. The first public tag must be
created only after the work is committed and the repository is public.

The package version is read from `shipzil/__init__.py`. The release workflow rejects
a tag that does not match it.
