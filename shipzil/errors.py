"""Exception hierarchy.

Provider messages are never swallowed. On three of the five surfaces the only
statement of what actually went wrong arrives in a `messages` array alongside a
2xx — see docs/API-REALITY.md — so every error carries them through.
"""

from __future__ import annotations


class ShipzilError(Exception):
    """Base for everything this library raises."""

    def __init__(self, message: str, *, provider: str = "", messages: list[str] | None = None):
        super().__init__(message)
        self.provider = provider
        self.messages = messages or []

    def __str__(self) -> str:
        base = super().__str__()
        if self.provider:
            base = f"[{self.provider}] {base}"
        if self.messages:
            base += "\n  provider said: " + "\n  provider said: ".join(self.messages)
        return base


class ConfigurationError(ShipzilError):
    """Missing credentials, unknown provider, contradictory options."""


class AuthenticationError(ShipzilError):
    """Credentials rejected."""


class ValidationError(ShipzilError):
    """The request was malformed or incomplete, per the provider."""


class CapabilityError(ShipzilError):
    """The provider cannot do what was asked, and emulation was not possible.

    Distinct from a rate simply being unavailable. Raised only when there is no
    path at all — otherwise the reason belongs in `Quote.excluded`.
    """


class RateLimitError(ShipzilError):
    """Throttled. Note Shippo reports this as a message on a 201, not a 429."""


class ProviderError(ShipzilError):
    """Provider returned an error we do not model more specifically."""

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        status: int | None = None,
        messages: list[str] | None = None,
        raw: object = None,
    ):
        super().__init__(message, provider=provider, messages=messages)
        self.status = status
        self.raw = raw


class LabelPurchaseError(ShipzilError):
    """A buy failed."""


class AmbiguousPurchaseError(LabelPurchaseError):
    """A buy may or may not have succeeded, and we could not determine which.

    The dangerous case: a timeout after the request reached the provider. Retrying
    risks double-buying real postage, so this is surfaced rather than retried, and
    carries whatever identifiers exist to reconcile by hand.
    """

    def __init__(self, message: str, *, provider: str = "", idempotency_key: str = "", **kw: object):
        super().__init__(message, provider=provider, messages=kw.get("messages"))  # type: ignore[arg-type]
        self.idempotency_key = idempotency_key


class SpendLimitExceeded(ShipzilError):
    """A guardrail stopped a purchase before it happened."""

    def __init__(self, message: str, *, limit: object = None, attempted: object = None):
        super().__init__(message)
        self.limit = limit
        self.attempted = attempted
