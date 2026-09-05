# Examples

The examples are intentionally safe by default.

```bash
SHIPPO_TEST_TOKEN=shippo_test_... uv run python examples/gateway.py
```

That requests rates only. To create a test label explicitly:

```bash
SHIPPO_TEST_TOKEN=shippo_test_... uv run python examples/gateway.py --buy
```

The example uses `Gateway`, filters to USPS, prints the source-specific service
key, and only buys when `--buy` is present.
