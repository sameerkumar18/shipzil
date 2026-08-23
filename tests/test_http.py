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


class TestCredentialGuardsAreBools:
    """The live-test guards read these without parentheses:

        assert adapter.is_test_key, "refusing to run live tests against production"

    If either ever becomes a plain method again, that expression evaluates a
    bound method, which is always truthy, and the guard protecting against
    buying real postage with production credentials silently always passes.
    mypy does not flag it. These tests exist because that regression was
    actually introduced once.
    """

    def test_easypost_is_test_key_is_a_bool_property(self) -> None:
        from shipzil.providers import EasyPostAdapter

        assert EasyPostAdapter("EZTKtest").is_test_key is True
        assert EasyPostAdapter("EZAKlive").is_test_key is False
        assert isinstance(EasyPostAdapter("EZAKlive").is_test_key, bool)

    def test_shippo_is_test_token_is_a_bool_property(self) -> None:
        from shipzil.providers import ShippoAdapter

        assert ShippoAdapter("shippo_test_x").is_test_token is True
        assert ShippoAdapter("shippo_live_x").is_test_token is False
        assert isinstance(ShippoAdapter("shippo_live_x").is_test_token, bool)

    def test_a_production_key_cannot_pass_the_guard(self) -> None:
        """The exact expression the live tests use, against a production key."""
        from shipzil.providers import EasyPostAdapter, ShippoAdapter

        assert not EasyPostAdapter("EZAKlive").is_test_key
        assert not ShippoAdapter("shippo_live_x").is_test_token

    def test_is_test_mode_is_three_state_across_adapters(self) -> None:
        from shipzil.providers import (
            EasyPostAdapter,
            EasyshipAdapter,
            ShippoAdapter,
            ShipStationV1Adapter,
            ShipStationV2Adapter,
        )

        assert EasyPostAdapter("EZTKtest").is_test_mode() is True
        assert ShippoAdapter("shippo_test_x").is_test_mode() is True
        assert EasyshipAdapter("sand_x").is_test_mode() is True
        assert ShipStationV1Adapter("k", "s", test_labels=True).is_test_mode() is True
        assert ShipStationV1Adapter("k", "s", test_labels=False).is_test_mode() is False
        # v2 keys carry no marker. None, not False: shipzil cannot tell.
        assert ShipStationV2Adapter("k").is_test_mode() is None
