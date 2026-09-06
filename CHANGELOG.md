# Changelog

The project has not published a release. All current work is listed under
Unreleased.

## Unreleased

### Added

- Multi-source `Gateway` with direct credential configuration.
- Concurrent source rating and concurrent per-parcel FANOUT.
- Source provenance on rates and labels.
- Provider, carrier and exact-service filtering.
- `GatewayQuote` sequence operations, diagnostics, messages, `cheapest` and
  `fastest`.
- Structured exclusions for locally filtered and unaddressable rates.
- Replaceable HTTP `Transport` protocol.
- Fumadocs site, generated Python reference and agent-readable Markdown routes.
- CI checks for Python 3.10 through 3.14 and a trusted-publishing release workflow.

### Changed

- Minimum Python version is 3.10.
- `Gateway` is the caller entry point; the single-source client is internal.
- `Rate` and `Label` constructors are keyword-only.
- `Capabilities` is a frozen dataclass.
- `SourceResult` exposes rates, exclusions and messages directly.
- `cheapest` returns `None` for mixed or unknown currencies.
- Cross-border rating stops before a provider call when any item lacks weight or
  value.
- Easyship requires explicit items and values; it no longer creates placeholder
  merchandise or a default customs value.
- ShipStation v1 label bytes are available in `Label.label_data`.
- Purchase-path `ProviderError` becomes `AmbiguousPurchaseError`.

### Fixed

- A rate list shortened by a transient carrier failure is now reported. Measured
  against a Shippo test account on a US domestic lane, a rating call returns 11
  rates normally and 3 when UPS answers "Hard: Too Many Requests". The reason was
  present in the response messages and already classifiable, but no exclusion was
  produced because the list was not empty, so a caller could lose 8 of 11 options
  with no signal. Structural account and lane messages are still not promoted;
  they are permanent and would be noise on every quote.

- Carrier family filters now match variants such as DHL Express and DHL eCommerce
  without matching unrelated carriers.
- Rates removed by shipzil filters return an exclusion instead of disappearing.
- Provider warning messages survive Gateway aggregation.
- Provider counts, customs value basis and idempotency documentation were reconciled
  with current source and provider schemas.

### Verified

- Offline tests cover all four adapters with sanitized responses and payload
  assertions.
- Live rating covers Shippo and ShipStation v2, including a two-source Gateway
  call with provenance checks.
- Shippo test purchase and refund are exercised with a `shippo_test_` token.
- ShipStation v2 purchase and void remain unverified live.
