"""HTTP layer: status classification and retry policy."""

from __future__ import annotations

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


class TestNoIdempotencyConcept:
    """shipzil takes no idempotency key, deliberately.

    Only EasyPost publishes one, and shipzil generated a fresh UUID per call,
    which deduplicates nothing. The earlier design raised CapabilityError when
    an explicit key was passed to the other four, which was ceremony that
    refused to help rather than helping. Both are gone. Purchases are simply
    never retried, and callers dedupe at their own layer on an id they own.
    """

    def test_buy_signature_takes_no_key(self) -> None:
        import inspect

        import shipzil
        from shipzil.providers import Adapter

        assert "idempotency_key" not in inspect.signature(shipzil.Client.buy).parameters
        assert "idempotency_key" not in inspect.signature(Adapter.buy).parameters

    def test_no_adapter_advertises_idempotency_support(self) -> None:
        from shipzil.providers import Adapter

        assert not hasattr(Adapter, "supports_idempotency_key")

    def test_purchases_are_never_retried(self) -> None:
        """The one guarantee that actually holds, on every provider."""
        import pathlib as _p

        for name in ("easypost", "shippo", "shipstation_v1", "shipstation_v2", "easyship"):
            src = _p.Path(f"shipzil/providers/{name}.py").read_text()
            buy = src[src.index("    def buy("):]
            buy = buy[: buy.index("\n    def ", 10)] if "\n    def " in buy[10:] else buy
            assert "retries=0" in buy, f"{name}.buy must pass retries=0"


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
