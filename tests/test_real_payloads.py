"""Parsing tests against payloads actually returned by the live sandboxes.

Every other parsing test in this suite runs against dicts written by hand, and
a hand-written fixture encodes the *same assumption* as the parser it is meant
to check. That is not a hypothetical: the Easyship carrier mapping read
`courier_service.courier.name` and `courier_service.courier_name`, neither of
which exists, and it passed its hand-written test while returning an empty
carrier on every real rate.

These fixtures are captured responses (contact details and credentials
scrubbed, structure untouched), so a wrong field name fails here.
"""

from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from typing import Any

import pytest

from shipzil.models import Strategy
from shipzil.providers import (
    EasyPostAdapter,
    EasyshipAdapter,
    ShippoAdapter,
    ShipStationV2Adapter,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


class TestEasyshipRealRates:
    def test_carrier_and_service_are_distinct_and_populated(self) -> None:
        adapter = EasyshipAdapter("k")
        rates = [adapter._parse_rate(r) for r in load("es_rates_single.json")["rates"]]
        assert rates, "fixture should contain rates"
        for rate in rates:
            assert rate.carrier, f"empty carrier on {rate.service!r}"
            assert rate.service, "empty service"
            assert rate.carrier != rate.service
            assert rate.amount > 0
            assert rate.currency == "USD"

    def test_carrier_is_the_umbrella_not_the_service(self) -> None:
        adapter = EasyshipAdapter("k")
        rates = [adapter._parse_rate(r) for r in load("es_rates_single.json")["rates"]]
        fedex = [r for r in rates if r.carrier == "FedEx"]
        assert fedex, f"expected FedEx rates, got carriers {sorted({r.carrier for r in rates})}"
        # The service name carries the trademark noise, the carrier does not.
        assert any("2Day" in r.service for r in fedex)

    def test_service_code_is_bookable_id(self) -> None:
        adapter = EasyshipAdapter("k")
        for raw in load("es_rates_single.json")["rates"]:
            rate = adapter._parse_rate(raw)
            assert rate.service_code == raw["courier_service"]["id"]


class TestOtherProvidersRealRates:
    """Cross-provider invariants, checked against real bodies."""

    @pytest.mark.parametrize(
        "fixture,builder",
        [
            ("ep_single.json", "easypost"),
            ("shippo_single.json", "shippo"),
            ("ss2_rates_single.json", "shipstation_v2"),
        ],
    )
    def test_fixture_is_a_recognisable_shape(self, fixture: str, builder: str) -> None:
        # Guards against a fixture being silently truncated or reshaped.
        data = load(fixture)
        assert isinstance(data, (dict, list))
        assert json.dumps(data)  # round-trips

    def test_easypost_rates_populate_carrier_and_currency(self) -> None:
        data = load("ep_single.json")
        raw_rates = data.get("rates") or []
        if not raw_rates:
            pytest.skip("fixture has no rates array")
        quote = EasyPostAdapter("k")._quote_from_rates(
            raw_rates,
            messages=[],
            via="easypost:shipments",
            strategy=Strategy.NATIVE,
            parcel_count=1,
            container_id="shp_test",
        )
        assert quote.rates
        for rate in quote.rates:
            assert rate.carrier
            assert rate.amount > 0

    def test_shippo_rates_populate_carrier(self) -> None:
        data = load("shippo_single.json")
        raw_rates = data.get("rates") or []
        if not raw_rates:
            pytest.skip("fixture has no rates array")
        rates = [
            ShippoAdapter("k")._parse_rate(r, shipment_id="shp_test") for r in raw_rates
        ]
        assert rates
        for rate in rates:
            assert rate.carrier
            assert rate.amount > 0

    def test_shipstation_v2_rates_populate_carrier(self) -> None:
        data = load("ss2_rates_single.json")
        raw = data.get("rate_response", data)
        raw_rates = raw.get("rates") if isinstance(raw, dict) else None
        if not raw_rates:
            pytest.skip("fixture has no rates array")
        adapter = ShipStationV2Adapter("k")
        rates = [
            adapter._parse_rate(r, strategy=Strategy.NATIVE, parcel_count=1)
            for r in raw_rates
        ]
        assert rates
        for rate in rates:
            assert rate.amount > 0


class TestShipStationV1RealRates:
    """v1's rate object has exactly four fields and no currency.

    Captured live, so if v1 ever starts sending a currency or delivery estimate
    these assertions fail and the capability flags get revisited.
    """

    def _adapter(self) -> Any:
        from shipzil.providers import ShipStationV1Adapter

        return ShipStationV1Adapter("k", "s")

    def test_rate_object_is_still_only_four_fields(self) -> None:
        rows = load("ss1_rates_single.json")
        assert rows
        for row in rows:
            assert set(row) == {"serviceCode", "serviceName", "shipmentCost", "otherCost"}, (
                f"v1 rate shape changed: {sorted(row)}"
            )

    def test_amount_is_shipment_plus_other_cost(self) -> None:
        adapter = self._adapter()
        for row in load("ss1_rates_single.json"):
            rate = adapter._parse_rate(row, "stamps_com")
            expected = Decimal(str(row["shipmentCost"])) + Decimal(str(row["otherCost"]))
            assert rate.amount == expected
            # Quoting shipmentCost alone is the bug this guards against.
            assert rate.amount >= Decimal(str(row["shipmentCost"]))

    def test_other_cost_is_actually_added(self) -> None:
        """Constructed, because the captured fixture cannot test this.

        Every `otherCost` in the live sample is 0.0, so `shipmentCost + otherCost`
        and `shipmentCost` alone are indistinguishable against it. The
        surcharge case needs data that exercises it, otherwise the test only
        appears to cover the arithmetic.
        """
        adapter = self._adapter()
        rate = adapter._parse_rate(
            {
                "serviceCode": "usps_priority_mail",
                "serviceName": "USPS Priority Mail",
                "shipmentCost": 11.41,
                "otherCost": 2.59,
            },
            "stamps_com",
        )
        assert rate.amount == Decimal("14.00")

    def test_currency_and_delivery_days_stay_none(self) -> None:
        adapter = self._adapter()
        for row in load("ss1_rates_single.json"):
            rate = adapter._parse_rate(row, "stamps_com")
            # v1 sends neither. Defaulting to USD would be an invention.
            assert rate.currency is None
            assert rate.delivery_days is None

    def test_carrier_code_is_preserved_for_purchase(self) -> None:
        adapter = self._adapter()
        for row in load("ss1_rates_single.json"):
            rate = adapter._parse_rate(row, "ups_walleted")
            # buy() needs carrierCode and v1 does not echo it on the rate.
            assert rate.raw["_carrier_code"] == "ups_walleted"
            assert rate.carrier == "ups_walleted"

    def test_capabilities_match_the_captured_reality(self) -> None:
        caps = self._adapter().capabilities
        assert caps.returns_currency is False
        assert caps.returns_delivery_estimate is False
        assert caps.native_multi_parcel is False
        assert caps.emulates_multi_parcel is True

    def test_test_labels_default_on_because_creds_are_production(self) -> None:
        assert self._adapter().test_labels is True
        # The credential itself is opaque; the flag is what makes it a test.
        assert self._adapter().is_test_mode() is True


class TestShipStationV1TestLabel:
    """Captured from a real `testLabel: true` call against production creds.

    The markers below are what make a test label safe to recognise, and are the
    reason `Label.is_test` exists as a first-class field rather than something
    you dig out of `raw`.
    """

    def test_response_carries_unmistakable_test_markers(self) -> None:
        data = load("ss1_testlabel.json")
        assert data["shipmentId"] == -1, "a test label creates no shipment record"
        assert data["trackingNumber"] == "9" * 20, "placeholder tracking number"
        assert float(data["shipmentCost"]) == 0.0, "nothing was charged"

    def test_label_marks_itself_as_test_and_reports_zero_charge(self) -> None:
        from decimal import Decimal

        from shipzil.models import Rate, Strategy
        from shipzil.providers import ShipStationV1Adapter

        adapter = ShipStationV1Adapter("k", "s", test_labels=True)
        quote = Rate(
            carrier="stamps_com",
            service="USPS Media Mail",
            amount=Decimal("4.39"),
            provider="shipstation_v1",
            strategy=Strategy.NATIVE,
        )
        label = adapter._label(load("ss1_testlabel.json"), quote)
        assert label.is_test is True
        assert label.shipment_id == "-1"
        # The charge, not the quote. The quote is still on the Rate.
        assert label.amount == Decimal("0.0")
        assert quote.amount == Decimal("4.39")

    def test_voiding_a_test_label_is_refused_locally(self) -> None:
        from decimal import Decimal

        from shipzil.errors import LabelPurchaseError
        from shipzil.models import Rate
        from shipzil.providers import ShipStationV1Adapter

        adapter = ShipStationV1Adapter("k", "s", test_labels=True)
        rate = Rate(carrier="stamps_com", service="x", amount=Decimal(1), provider="shipstation_v1")
        label = adapter._label(load("ss1_testlabel.json"), rate)
        with pytest.raises(LabelPurchaseError, match="no shipment to void"):
            adapter.void(label)


class TestEasyPostOrderBuy:
    """Buying an EasyPost *order* is a different endpoint and a different body.

    The first implementation sent `POST /shipments/{order_id}/buy` with
    `{"rate": {"id": ...}}`. Both were wrong, and nothing caught it because only
    the single-parcel shipment path had ever run against live credentials. The
    real contract, from EasyPost's own recorded test traffic:

        POST /orders/{id}/buy   {"carrier": "USPS", "service": "GroundAdvantage"}
        -> {"shipments": [{..., "postage_label": {...}, "tracking_code": ...}, ...]}

    Multi-parcel is this library's headline feature, so this is the single most
    important purchase path to get right.
    """

    def test_fixture_is_a_real_multi_shipment_order_buy(self) -> None:
        d = load("ep_order_buy.json")
        assert d["object"] == "Order"
        assert d["id"].startswith("order_")
        assert len(d["shipments"]) == 2, "one shipment per parcel"
        for s in d["shipments"]:
            assert s["postage_label"]["label_url"], "each parcel gets its own label"
            assert s["tracking_code"], "each parcel gets its own tracking code"

    def test_order_buy_yields_one_label_per_parcel(self) -> None:
        from shipzil.providers import EasyPostAdapter

        adapter = EasyPostAdapter("EZTKtest")
        body = load("ep_order_buy.json")
        labels = tuple(adapter._label(s) for s in body["shipments"])
        assert len(labels) == 2
        # Distinct labels, not the same one twice.
        assert labels[0].tracking_number != labels[1].tracking_number
        assert labels[0].label_url != labels[1].label_url
        assert all(lbl.is_test is True for lbl in labels)

    def test_an_order_rate_routes_to_the_orders_endpoint(self) -> None:
        """The routing decision, without touching the network."""
        from decimal import Decimal

        from shipzil.models import Rate, Strategy
        from shipzil.providers import EasyPostAdapter

        adapter = EasyPostAdapter("EZTKtest")
        seen: dict[str, object] = {}

        def fake_request(method: str, url: str, **kw: object) -> tuple[int, object]:
            seen["url"] = url
            seen["json"] = kw.get("json")
            seen["retries"] = kw.get("retries")
            return 200, load("ep_order_buy.json")

        import shipzil.providers.easypost as mod

        original = mod.request
        mod.request = fake_request  # type: ignore[assignment]
        try:
            rate = Rate(
                carrier="USPS",
                service="GroundAdvantage",
                amount=Decimal("11.74"),
                provider="easypost",
                strategy=Strategy.ORDER,
                parcel_count=2,
                raw={"_container_id": "order_c91fef40e21c48a3a4846ab323722583"},
            )
            label = adapter.buy(None, rate)  # type: ignore[arg-type]
        finally:
            mod.request = original  # type: ignore[assignment]

        assert "/orders/order_c91fef40e21c48a3a4846ab323722583/buy" in str(seen["url"])
        assert "/shipments/" not in str(seen["url"])
        # Carrier and service by name, never a rate id.
        assert seen["json"] == {"carrier": "USPS", "service": "GroundAdvantage"}
        assert seen["retries"] == 0
        assert len(label.parcel_labels) == 2
