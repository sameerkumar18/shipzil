"""HTTP layer: status classification and retry policy."""

from __future__ import annotations

import pytest

import shipzil as _z
from shipzil import errors, http

_SHIPMENT = _z.Shipment(
    _z.Address(street1="1 A St", city="San Francisco", state="CA", postal_code="94117"),
    _z.Address(street1="1 B St", city="New York", state="NY", postal_code="10020"),
    (_z.Parcel(weight=_z.Weight.of(1, "lb"), dimensions=_z.Dimensions.of(6, 4, 2, "in")),),
)


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

    No current adapter publishes a usable idempotency key. Purchases are simply
    never retried, and callers dedupe at their own layer on an id they own.
    """

    def test_buy_signature_takes_no_key(self) -> None:
        import inspect

        from shipzil._client import Client as _Client
        from shipzil.providers import Adapter

        assert "idempotency_key" not in inspect.signature(_Client.buy).parameters
        assert "idempotency_key" not in inspect.signature(Adapter.buy).parameters

    def test_no_adapter_advertises_idempotency_support(self) -> None:
        from shipzil.providers import Adapter

        assert not hasattr(Adapter, "supports_idempotency_key")

    def test_no_money_moving_request_is_ever_retried(self) -> None:
        """Every request to a purchase, label or refund route must pass retries=0.

        Checked by walking the AST rather than slicing source text, so moving a
        call into a helper cannot silently drop the guarantee. Adapters call
        `self.http(...)`, so that is what is matched; `checked` is asserted at the
        end so a rename that stops matching fails loudly instead of passing
        vacuously.
        """
        import ast
        import pathlib as _p

        spends = ("buy", "labels", "transactions", "refunds", "voidlabel", "createlabel")
        checked = 0
        for path in sorted(_p.Path("shipzil/providers").glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                is_http_call = (
                    isinstance(func, ast.Attribute) and func.attr == "http"
                ) or getattr(func, "id", "") == "request"
                if not is_http_call:
                    continue
                url = " ".join(
                    ast.unparse(a) for a in node.args[1:2]
                ) + " ".join(ast.unparse(k.value) for k in node.keywords if k.arg == "url")
                if not any(tok in url.lower() for tok in spends):
                    continue
                retries = next((k.value for k in node.keywords if k.arg == "retries"), None)
                assert retries is not None, f"{path.name}: {url} has no retries="
                assert getattr(retries, "value", None) == 0, (
                    f"{path.name}: {url} must pass retries=0, got {ast.unparse(retries)}"
                )
                checked += 1
        assert checked >= 6, f"expected to find several money-moving calls, found {checked}"


class TestCredentialGuardsAreBools:
    """The live-test guards read these without parentheses:

        assert adapter.is_test_key, "refusing to run live tests against production"

    If either ever becomes a plain method again, that expression evaluates a
    bound method, which is always truthy, and the guard protecting against
    buying real postage with production credentials silently always passes.
    mypy does not flag it. These tests exist because that regression was
    actually introduced once.
    """

    def test_shippo_is_test_token_is_a_bool_property(self) -> None:
        from shipzil.providers import ShippoAdapter

        assert ShippoAdapter("shippo_test_x").is_test_token is True
        assert ShippoAdapter("shippo_live_x").is_test_token is False
        assert isinstance(ShippoAdapter("shippo_live_x").is_test_token, bool)

    def test_a_production_key_cannot_pass_the_guard(self) -> None:
        """The exact expression the live tests use, against a production key."""
        from shipzil.providers import ShippoAdapter

        assert not ShippoAdapter("shippo_live_x").is_test_token

    def test_is_test_mode_is_three_state_across_adapters(self) -> None:
        from shipzil.providers import (
            EasyshipAdapter,
            ShippoAdapter,
            ShipStationV1Adapter,
            ShipStationV2Adapter,
        )

        assert ShippoAdapter("shippo_test_x").is_test_mode() is True
        assert EasyshipAdapter("sand_x").is_test_mode() is True
        assert ShipStationV1Adapter("k", "s", test_labels=True).is_test_mode() is True
        assert ShipStationV1Adapter("k", "s", test_labels=False).is_test_mode() is False
        # v2 keys carry no marker. None, not False: shipzil cannot tell.
        assert ShipStationV2Adapter("k").is_test_mode() is None


class TestTransportIsInjectable:
    """A caller-supplied transport is the supported seam for logging or replay.

    Tests used to reassign each provider module's `request` global. Adapters now
    route every call through `Adapter.http`, so a transport set once cannot be
    bypassed at one forgotten call site.
    """

    def test_a_supplied_transport_receives_the_request(self) -> None:
        from transport import RecordingTransport

        from shipzil.providers import ShippoAdapter

        transport = RecordingTransport(default=(200, {"rates": []}))
        adapter = ShippoAdapter("shippo_test_x")
        adapter.transport = transport

        adapter.rate_single(_SHIPMENT)

        assert len(transport.requests) == 1
        sent = transport.requests[0]
        assert sent.method == "POST"
        assert sent.url.endswith("/shipments/")
        assert sent.headers["Authorization"] == "ShippoToken shippo_test_x"
        assert "shipzil/" in sent.headers["User-Agent"]

    def test_every_adapter_routes_through_its_transport(self) -> None:
        """No adapter may call the module-level `request` directly."""
        import ast
        import pathlib as _p

        offenders = []
        for path in sorted(_p.Path("shipzil/providers").glob("*.py")):
            if path.name in {"base.py", "__init__.py"}:
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "request":
                    offenders.append(f"{path.name}:{node.lineno}")
        assert not offenders, (
            f"these call http.request directly and would bypass a caller's "
            f"transport: {offenders}"
        )

    def test_an_http_error_status_still_maps_to_a_shipzil_error(self) -> None:
        """Error mapping is policy above the transport, so a custom one keeps it."""
        from transport import RecordingTransport

        import shipzil as z
        from shipzil.http import HttpResponse
        from shipzil.providers import ShippoAdapter

        adapter = ShippoAdapter("shippo_test_x")
        adapter.transport = RecordingTransport(
            default=HttpResponse(status=401, body=b'{"detail":"bad token"}')
        )

        with pytest.raises(z.AuthenticationError, match="bad token"):
            adapter.rate_single(_SHIPMENT)

    def test_a_transport_failure_becomes_a_provider_error(self) -> None:
        import shipzil as z
        from shipzil.http import HttpRequest, HttpResponse
        from shipzil.providers import ShippoAdapter

        class Broken:
            def send(self, request: HttpRequest) -> HttpResponse:
                raise OSError("connection reset")

        adapter = ShippoAdapter("shippo_test_x")
        adapter.transport = Broken()

        with pytest.raises(z.ProviderError, match="network error"):
            adapter.rate_single(_SHIPMENT)
