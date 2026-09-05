# Provider response fixtures

These files are sanitized provider responses used for parser regression tests.
Constructed dictionaries are used separately for edge cases that the captures do
not contain.

| Fixture family | Source | Safe operation |
|---|---|---|
| `shippo_single.json`, `shippo_multiparcel.json` | Shippo test token | rating, purchase and void |
| `es_rates_single.json`, `es_rates_multi.json`, `es_label.json` | Easyship sandbox | rating and test purchase |
| `ss1_carriers.json`, `ss1_rates_single.json` | ShipStation v1 production credentials | read-only rating |
| `ss1_testlabel.json` | ShipStation v1 production credentials | one Stamps.com/USPS `testLabel` response with zero shipment cost |
| `ss2_carriers.json`, `ss2_rates_single.json`, `ss2_rates_multi.json` | ShipStation v2 production credentials | read-only rating |

The fixtures preserve fields and nesting needed by the parser. Credentials,
contacts, identifiers, tracking values and label bytes are sanitized. Some files
also contain explicit test annotations such as `_test_label`; they are not raw
provider payloads.

ShipStation v1 and v2 credentials are production because those APIs have no
sandbox. Current live tests use v2 for rating only. Shippo purchase tests assert a
`shippo_test_` token. The two-source Gateway live test performs rating only.

The live suite currently covers Shippo and ShipStation v2. Easyship and
ShipStation v1 are fixture, payload and specification based. Fixtures provide
repeatable regression coverage without spending quota or requiring credentials:

```bash
make check                 # fixture-backed/offline suite
make test-live             # real provider calls; loads .env
```

When adding a provider or changing a request field, capture a response when it is
safe, sanitize it, record the source here and add a parser assertion. Mark
constructed edge cases as synthetic in the test.
