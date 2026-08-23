"""Tests for the parts where a silent mistake becomes a wrong shipping quote.

Live tests are marked and skipped unless credentials are present, so the suite
runs offline. Each case here corresponds to something observed in
docs/API-REALITY.md.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

import shipzil
from shipzil.models import Exclusion, ExclusionCode, Quote, Rate, Strategy
from shipzil.multiparcel import combine_parcel_quotes
from shipzil.normalize import code_from_provider_code, code_from_text

# ── units ───────────────────────────────────────────────────────────


def test_weight_conversions_are_exact_by_definition():
    assert shipzil.Weight.of(1, "kg").to("g") == Decimal("1000.0000")
    assert shipzil.Weight.of(1, "lb").to("oz") == Decimal("16.0000")
    assert shipzil.Weight.of(16, "oz").to("lb") == Decimal("1.0000")


def test_dimension_conversions():
    d = shipzil.Dimensions.of(10, 8, 4, "in")
    assert d.to("cm") == (Decimal("25.40"), Decimal("20.32"), Decimal("10.16"))


def test_weights_add_in_their_own_unit():
    total = shipzil.Weight.of(8, "oz") + shipzil.Weight.of(1, "lb")
    assert total.unit == "oz"
    assert total.to("oz") == Decimal("24.0000")


def test_nonsense_units_and_values_are_rejected():
    with pytest.raises(ValueError):
        shipzil.Weight.of(1, "stone")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        shipzil.Weight.of(0, "oz")
    with pytest.raises(ValueError):
        shipzil.Dimensions.of(0, 8, 4)


# ── model invariants ────────────────────────────────────────────────


def test_a_parcel_needs_weight_or_items():
    with pytest.raises(ValueError):
        shipzil.Parcel()


def test_item_centric_parcel_derives_weight_only_when_every_item_has_one():
    with_weights = shipzil.Parcel(
        items=(
            shipzil.Item("a", quantity=2, weight=shipzil.Weight.of(4, "oz")),
            shipzil.Item("b", weight=shipzil.Weight.of(8, "oz")),
        )
    )
    assert with_weights.derived_weight is not None
    assert with_weights.derived_weight.to("oz") == Decimal("16.0000")

    # One item without a weight means no derivation. Never a guess.
    partial = shipzil.Parcel(
        items=(shipzil.Item("a", weight=shipzil.Weight.of(4, "oz")), shipzil.Item("b"))
    )
    assert partial.derived_weight is None
    assert partial.is_item_centric


def test_shipment_requires_a_parcel_and_knows_its_shape():
    addr = shipzil.Address(street1="1 A St", city="SF", postal_code="94117", state="CA")
    with pytest.raises(ValueError):
        shipzil.Shipment(addr, addr, ())

    one = shipzil.Shipment(addr, addr, (shipzil.Parcel(weight=shipzil.Weight.of(1, "lb")),))
    assert not one.is_multi_parcel
    assert not one.is_international


# ── multi-parcel emulation: the honesty rules ───────────────────────


def _rate(carrier, service, amount, days=None, currency="USD"):
    return Rate(
        carrier=carrier,
        service=service,
        amount=Decimal(str(amount)),
        currency=currency,
        delivery_days=days,
        provider="test",
        raw={"id": f"{carrier}-{service}-{amount}"},
    )


def test_fanout_sums_only_services_that_cover_every_parcel():
    # USPS Ground covers all three; UPS Ground covers only two.
    quotes = [
        Quote(rates=(_rate("USPS", "Ground", 10), _rate("UPS", "Ground", 15))),
        Quote(rates=(_rate("USPS", "Ground", 12), _rate("UPS", "Ground", 18))),
        Quote(rates=(_rate("USPS", "Ground", 8),)),
    ]
    combined = combine_parcel_quotes(quotes, provider="test", via="test:fanoutx3")

    assert [(r.carrier, str(r.amount)) for r in combined.rates] == [("USPS", "30")]
    only = combined.rates[0]
    assert only.strategy is Strategy.FANOUT
    assert only.is_synthesized
    assert only.parcel_count == 3
    # The per-parcel breakdown is retained so the sum can be audited.
    assert only.raw["amounts"] == ["10", "12", "8"]

    # UPS is excluded with a reason, not silently dropped.
    ups = [e for e in combined.excluded if e.carrier == "UPS"]
    assert len(ups) == 1
    assert ups[0].code is ExclusionCode.SERVICE_UNAVAILABLE
    assert ups[0].source == "shipzil"
    assert "1 of 3 parcels" in ups[0].message


def test_fanout_delivery_days_is_the_slowest_parcel():
    quotes = [
        Quote(rates=(_rate("USPS", "Ground", 10, days=2),)),
        Quote(rates=(_rate("USPS", "Ground", 10, days=5),)),
    ]
    combined = combine_parcel_quotes(quotes, provider="test", via="v")
    assert combined.rates[0].delivery_days == 5


def test_fanout_drops_delivery_days_when_any_parcel_lacks_one():
    # ShipStation v1 returns no delivery estimate at all; a partial max would lie.
    quotes = [
        Quote(rates=(_rate("USPS", "Ground", 10, days=2),)),
        Quote(rates=(_rate("USPS", "Ground", 10, days=None),)),
    ]
    combined = combine_parcel_quotes(quotes, provider="test", via="v")
    assert combined.rates[0].delivery_days is None


def test_fanout_refuses_to_add_different_currencies():
    quotes = [
        Quote(rates=(_rate("USPS", "Ground", 10, currency="USD"),)),
        Quote(rates=(_rate("USPS", "Ground", 10, currency="CAD"),)),
    ]
    with pytest.raises(ValueError, match="different currencies"):
        combine_parcel_quotes(quotes, provider="test", via="v")


def test_fanout_tolerates_missing_currency():
    quotes = [
        Quote(rates=(_rate("USPS", "Ground", 10, currency=None),)),
        Quote(rates=(_rate("USPS", "Ground", 10, currency=None),)),
    ]
    combined = combine_parcel_quotes(quotes, provider="test", via="v")
    assert combined.rates[0].currency is None


def test_fanout_keeps_cheapest_when_a_parcel_has_duplicate_service_rows():
    quotes = [
        Quote(rates=(_rate("USPS", "Ground", 10), _rate("USPS", "Ground", 7))),
        Quote(rates=(_rate("USPS", "Ground", 5),)),
    ]
    combined = combine_parcel_quotes(quotes, provider="test", via="v")
    assert str(combined.rates[0].amount) == "12"


def test_fanout_passes_provider_exclusions_through_once():
    exc = Exclusion(
        code=ExclusionCode.MULTIPACKAGE_NOT_SUPPORTED,
        message="carrier 30718 does not support multipackage",
        carrier="usps",
    )
    quotes = [Quote(rates=(), excluded=(exc,)), Quote(rates=(), excluded=(exc,))]
    combined = combine_parcel_quotes(quotes, provider="test", via="v")
    assert len(combined.excluded) == 1
    assert combined.excluded[0].source == "provider"


def test_single_parcel_passes_straight_through():
    quotes = [Quote(rates=(_rate("USPS", "Ground", 10),), via="x", strategy=Strategy.NATIVE)]
    combined = combine_parcel_quotes(quotes, provider="test", via="v")
    assert combined.strategy is Strategy.NATIVE
    assert not combined.rates[0].is_synthesized


# ── quote reporting ─────────────────────────────────────────────────


def test_a_quote_with_no_rates_still_explains_itself():
    q = Quote(
        excluded=(
            Exclusion(
                ExclusionCode.MULTIPACKAGE_NOT_SUPPORTED,
                "usps cannot multipackage",
                carrier="usps",
            ),
        ),
        via="shipstation_v2:rates",
    )
    assert not q
    assert q.cheapest is None
    text = q.explain()
    assert "0 rate(s)" in text
    assert "multipackage_not_supported" in text
    assert "usps" in text


def test_cheapest_and_fastest_ignore_incomparable_rates():
    q = Quote(rates=(_rate("A", "s", 10, days=9), _rate("B", "s", 20, days=1), _rate("C", "s", 5)))
    assert q.cheapest.carrier == "C"
    assert q.fastest.carrier == "B"  # C has no estimate and cannot be fastest


# ── guardrails ──────────────────────────────────────────────────────


class _StubAdapter(shipzil.providers.Adapter):
    name = "stub"

    def rate_single(self, shipment):  # pragma: no cover - not exercised
        return Quote()

    def buy(self, shipment, rate, *, idempotency_key):  # pragma: no cover
        raise AssertionError("should never be reached in these tests")


def _shipment():
    a = shipzil.Address(street1="1 A St", city="SF", postal_code="94117", state="CA")
    return shipzil.Shipment(a, a, (shipzil.Parcel(weight=shipzil.Weight.of(1, "lb")),))


def test_max_spend_blocks_a_purchase_before_any_network_call():
    client = shipzil.Client(_StubAdapter(), max_spend="10")
    with pytest.raises(shipzil.SpendLimitExceeded):
        client.buy(_shipment(), _rate("USPS", "Ground", 25))


def test_a_synthesized_rate_cannot_be_bought_as_one_label():
    # Summing per-parcel quotes produces a number, not a purchasable consignment.
    client = shipzil.Client(_StubAdapter())
    synthetic = Rate(
        carrier="USPS", service="Ground", amount=Decimal(30), provider="stub",
        strategy=Strategy.FANOUT, parcel_count=3,
    )
    with pytest.raises(shipzil.CapabilityError, match="synthesized"):
        client.buy(_shipment(), synthetic)


def test_dry_run_never_calls_the_adapter():
    client = shipzil.Client(_StubAdapter(), dry_run=True)
    label = client.buy(_shipment(), _rate("USPS", "Ground", 5))
    assert label.tracking_number == "DRYRUN"
    assert label.raw["dry_run"] is True


# ── live: EasyPost routes multi-parcel to /orders ───────────────────

_EP_KEY = os.environ.get("EASYPOST_TEST_KEY", "")


@pytest.mark.live
@pytest.mark.skipif(not _EP_KEY, reason="EASYPOST_TEST_KEY not set")
def test_live_easypost_routes_by_parcel_count():
    from shipzil.providers import EasyPostAdapter

    adapter = EasyPostAdapter(_EP_KEY)
    assert adapter.is_test_key, "refusing to run live tests against a production key"
    client = shipzil.Client(adapter)

    frm = shipzil.Address(street1="215 Clayton St", city="San Francisco", state="CA",
                          postal_code="94117", name="S", phone="4151234567")
    to = shipzil.Address(street1="1 Rockefeller Plaza", city="New York", state="NY",
                         postal_code="10020", name="R", phone="2125551234")

    def parcel(oz):
        return shipzil.Parcel(weight=shipzil.Weight.of(oz, "oz"),
                              dimensions=shipzil.Dimensions.of(10, 8, 4, "in"))

    single = client.get_rates(shipzil.Shipment(frm, to, (parcel(16),)))
    assert single.rates
    assert single.via == "easypost:shipments"
    assert single.strategy is Strategy.NATIVE

    multi = client.get_rates(shipzil.Shipment(frm, to, (parcel(16), parcel(32), parcel(8))))
    assert multi.rates, multi.explain()
    assert multi.via == "easypost:orders"
    assert multi.strategy is Strategy.ORDER
    # Order-level rates are real provider quotes, not sums we computed.
    assert not multi.rates[0].is_synthesized
    assert multi.rates[0].parcel_count == 3
    # Three parcels should cost more than one.
    assert multi.cheapest.amount > single.cheapest.amount


@pytest.mark.live
@pytest.mark.skipif(not _EP_KEY, reason="EASYPOST_TEST_KEY not set")
def test_live_easypost_refuses_item_only_parcels_with_a_reason():
    from shipzil.providers import EasyPostAdapter

    client = shipzil.Client(EasyPostAdapter(_EP_KEY))
    a = shipzil.Address(street1="215 Clayton St", city="San Francisco", state="CA",
                        postal_code="94117", name="S", phone="4151234567")
    b = shipzil.Address(street1="1 Rockefeller Plaza", city="New York", state="NY",
                        postal_code="10020", name="R", phone="2125551234")
    itemised = shipzil.Parcel(items=(shipzil.Item("tshirt", quantity=2, category="fashion"),))

    quote = client.get_rates(shipzil.Shipment(a, b, (itemised,)))
    assert not quote.rates
    assert quote.excluded[0].code is ExclusionCode.DIMENSIONS_REQUIRED
    assert "cannot derive" in quote.excluded[0].message


# ── normalisation: prose to the ShipStation v2 vocabulary ───────────



def test_provider_codes_are_taken_verbatim():
    assert code_from_provider_code("multipackage_not_supported") is (
        ExclusionCode.MULTIPACKAGE_NOT_SUPPORTED
    )
    assert code_from_provider_code("MULTIPACKAGE_NOT_SUPPORTED") is (
        ExclusionCode.MULTIPACKAGE_NOT_SUPPORTED
    )
    assert code_from_provider_code(None) is None
    assert code_from_provider_code("something_new") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Real strings observed from each provider — see docs/API-REALITY.md.
        ("carrier 30718 does not support multipackage. unable to rate the shipment",
         ExclusionCode.MULTIPACKAGE_NOT_SUPPORTED),
        ("Carrier account shippo_usps_master doesn't support one or more shipment options",
         ExclusionCode.CARRIER_ACCOUNT_MISCONFIGURED),
        ("UPS - Hard: Too Many Requests", ExclusionCode.RATE_LIMITED),
        ("parcels[0].items[0].category can't be blank if hs_code is blank",
         ExclusionCode.ITEM_CLASSIFICATION_REQUIRED),
        ("A to_address, from_address and parcel are required for rating.",
         ExclusionCode.DIMENSIONS_REQUIRED),
        ("No shipping solutions available based on the information provided",
         ExclusionCode.SERVICE_UNAVAILABLE),
        ("", ExclusionCode.UNKNOWN),
        ("something nobody has ever seen", ExclusionCode.UNKNOWN),
    ],
)
def test_prose_maps_onto_the_shared_vocabulary(text, expected):
    assert code_from_text(text) is expected


# ── live: ShipStation v2 (rating only — production credentials) ─────

_SS2_KEY = os.environ.get("SHIPSTATION_V2_KEY", "")


@pytest.mark.live
@pytest.mark.skipif(not _SS2_KEY, reason="SHIPSTATION_V2_KEY not set")
def test_live_shipstation_v2_native_multi_parcel_with_attributed_exclusions():
    """v2 rates 3 packages natively AND names the carrier that cannot."""
    from shipzil.providers import ShipStationV2Adapter

    client = shipzil.Client(ShipStationV2Adapter(_SS2_KEY))
    frm = shipzil.Address(street1="215 Clayton St", city="San Francisco", state="CA",
                          postal_code="94117", name="S", phone="4151234567")
    to = shipzil.Address(street1="1 Rockefeller Plaza", city="New York", state="NY",
                         postal_code="10020", name="R", phone="2125551234")

    def parcel(oz):
        return shipzil.Parcel(weight=shipzil.Weight.of(oz, "oz"),
                              dimensions=shipzil.Dimensions.of(10, 8, 4, "in"))

    single = client.get_rates(shipzil.Shipment(frm, to, (parcel(16),)))
    assert single.rates, single.explain()
    assert single.strategy is Strategy.NATIVE
    assert single.cheapest.currency == "USD"

    multi = client.get_rates(shipzil.Shipment(frm, to, (parcel(16), parcel(32), parcel(8))))
    assert multi.rates, multi.explain()
    # One call, not a fan-out: v2 supports packages[] natively.
    assert multi.strategy is Strategy.NATIVE
    assert not multi.rates[0].is_synthesized
    assert multi.rates[0].parcel_count == 3

    # The whole point: USPS cannot multipackage and we are told so, by name.
    mp = [e for e in multi.excluded if e.code is ExclusionCode.MULTIPACKAGE_NOT_SUPPORTED]
    assert mp, multi.explain()
    assert mp[0].source == "provider"  # v2 gave a real error_code
    assert mp[0].carrier


# ── live: Shippo — the first real fan-out ───────────────────────────

_SHIPPO_TOKEN = os.environ.get("SHIPPO_TEST_TOKEN", "")


@pytest.mark.live
@pytest.mark.skipif(not _SHIPPO_TOKEN, reason="SHIPPO_TEST_TOKEN not set")
def test_live_shippo_multi_parcel_is_emulated_and_labelled_as_such():
    """Shippo accepts parcels[] and rates it as nothing, so shipzil fans out."""
    from shipzil.providers import ShippoAdapter

    adapter = ShippoAdapter(_SHIPPO_TOKEN)
    assert adapter.is_test_token, "refusing to run live tests against a live token"
    assert adapter.capabilities.emulates_multi_parcel
    client = shipzil.Client(adapter)

    frm = shipzil.Address(street1="215 Clayton St", city="San Francisco", state="CA",
                          postal_code="94117", name="S", phone="4151234567",
                          email="s@example.com")
    to = shipzil.Address(street1="1 Rockefeller Plaza", city="New York", state="NY",
                         postal_code="10020", name="R", phone="2125551234",
                         email="r@example.com")

    def parcel(oz):
        return shipzil.Parcel(weight=shipzil.Weight.of(oz, "oz"),
                              dimensions=shipzil.Dimensions.of(10, 8, 4, "in"))

    single = client.get_rates(shipzil.Shipment(frm, to, (parcel(16),)))
    assert single.rates, single.explain()
    assert single.strategy is Strategy.NATIVE
    assert not single.cheapest.is_synthesized

    multi = client.get_rates(shipzil.Shipment(frm, to, (parcel(16), parcel(32), parcel(8))))
    assert multi.rates, multi.explain()
    # Emulated, and honest about it.
    assert multi.strategy is Strategy.FANOUT
    assert "fanout" in multi.via
    best = multi.cheapest
    assert best.is_synthesized
    assert best.parcel_count == 3
    # The sum is auditable and actually adds up.
    amounts = [Decimal(a) for a in best.raw["amounts"]]
    assert len(amounts) == 3
    assert sum(amounts) == best.amount
    # Three parcels cost more than one.
    assert best.amount > single.cheapest.amount
    # And it cannot be bought as a single label.
    with pytest.raises(shipzil.CapabilityError, match="synthesized"):
        client.buy(shipzil.Shipment(frm, to, (parcel(16), parcel(32), parcel(8))), best)


@pytest.mark.live
@pytest.mark.skipif(not _SHIPPO_TOKEN, reason="SHIPPO_TEST_TOKEN not set")
def test_live_shippo_buy_and_void_a_test_label():
    """Exercise the real purchase path end to end on a test token."""
    from shipzil.providers import ShippoAdapter

    client = shipzil.Client(ShippoAdapter(_SHIPPO_TOKEN))
    frm = shipzil.Address(street1="215 Clayton St", city="San Francisco", state="CA",
                          postal_code="94117", name="S", phone="4151234567",
                          email="s@example.com")
    to = shipzil.Address(street1="1 Rockefeller Plaza", city="New York", state="NY",
                         postal_code="10020", name="R", phone="2125551234",
                         email="r@example.com")
    shipment = shipzil.Shipment(
        frm, to,
        (shipzil.Parcel(weight=shipzil.Weight.of(16, "oz"),
                        dimensions=shipzil.Dimensions.of(10, 8, 4, "in")),),
    )
    quote = client.get_rates(shipment)
    usps = [r for r in quote.rates if "usps" in r.carrier.lower()]
    rate = usps[0] if usps else quote.cheapest

    label = client.buy(shipment, rate)
    assert label.tracking_number
    assert label.label_url.startswith("http")
    assert label.provider == "shippo"

    # Shippo rejects refunds on test-mode labels: HTTP 201, status "ERROR",
    # transaction -> REFUNDREJECTED, messages empty. A silent False would hide
    # that, so void() raises and says the provider gave no reason.
    with pytest.raises(shipzil.ShipzilError) as caught:
        client.void(label)
    assert "rejected the refund" in str(caught.value)
    assert "gave no reason" in str(caught.value)
