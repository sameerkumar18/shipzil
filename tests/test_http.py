"""HTTP layer: status classification and retry policy."""

from __future__ import annotations

from typing import Any

import pytest

from shipzil import errors, http


class TestQuotaMisclassification:
    """Easyship answers an exhausted plan with 403, not 429.

    Treating that as an auth failure sends the caller to rotate a credential
    that was never broken, so the message decides the exception, not the code.
    """

    def test_403_quota_is_rate_limit_not_auth(self) -> None:
        body = {
            "error": {
                "message": (
                    "API usage limit exceeded. Please upgrade your plan or wait "
                    "for your usage period to reset."
                )
            }
        }
        with pytest.raises(errors.RateLimitError) as caught:
            http._raise_for_status(403, body, "easyship")
        assert "usage limit exceeded" in str(caught.value)

    def test_403_without_quota_language_is_still_auth(self) -> None:
        with pytest.raises(errors.AuthenticationError):
            http._raise_for_status(403, {"error": {"message": "Invalid API key"}}, "easyship")

    def test_401_is_always_auth(self) -> None:
        with pytest.raises(errors.AuthenticationError):
            http._raise_for_status(401, {"error": {"message": "unauthorized"}}, "x")

    @pytest.mark.parametrize(
        "phrase",
        [
            "You have exceeded the maximum number of requests per second",
            "Monthly quota reached",
            "Too many requests",
            "rate limit reached",
        ],
    )
    def test_quota_phrasings(self, phrase: str) -> None:
        with pytest.raises(errors.RateLimitError):
            http._raise_for_status(403, {"error": {"message": phrase}}, "x")


class TestIdempotencyHonesty:
    """A key is either enforced by the provider or refused. Never dropped.

    Before this, all four adapters took `idempotency_key` and only EasyPost put
    it on the wire. The other three accepted the argument and discarded it,
    which handed the caller a duplicate-purchase guarantee that did not exist.
    """

    def _client(self, adapter_cls: Any) -> Any:
        import shipzil

        return shipzil.Client(adapter_cls("k"))

    def test_only_easypost_claims_support(self) -> None:
        from shipzil.providers import (
            EasyPostAdapter,
            EasyshipAdapter,
            ShippoAdapter,
            ShipStationV2Adapter,
        )

        assert EasyPostAdapter("k").supports_idempotency_key is True
        for cls in (ShippoAdapter, ShipStationV2Adapter, EasyshipAdapter):
            assert cls("k").supports_idempotency_key is False, cls.__name__

    def test_supporting_provider_generates_a_key_when_none_given(self) -> None:
        from shipzil.providers import EasyPostAdapter

        key = self._client(EasyPostAdapter)._resolve_idempotency_key(None)
        assert key and key.startswith("shipzil-")

    def test_supporting_provider_passes_through_an_explicit_key(self) -> None:
        from shipzil.providers import EasyPostAdapter

        assert self._client(EasyPostAdapter)._resolve_idempotency_key("mine") == "mine"

    @pytest.mark.parametrize("name", ["ShippoAdapter", "ShipStationV2Adapter", "EasyshipAdapter"])
    def test_explicit_key_is_refused_not_dropped(self, name: str) -> None:
        from shipzil import providers

        cls = getattr(providers, name)
        with pytest.raises(errors.CapabilityError) as caught:
            self._client(cls)._resolve_idempotency_key("mine")
        msg = str(caught.value)
        # The refusal has to be actionable, not just a denial.
        assert "omit idempotency_key" in msg
        assert "never retries a purchase" in msg

    @pytest.mark.parametrize("name", ["ShippoAdapter", "ShipStationV2Adapter", "EasyshipAdapter"])
    def test_no_key_is_fabricated_for_unsupporting_providers(self, name: str) -> None:
        from shipzil import providers

        cls = getattr(providers, name)
        assert self._client(cls)._resolve_idempotency_key(None) is None
