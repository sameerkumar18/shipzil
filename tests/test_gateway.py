from __future__ import annotations

from decimal import Decimal

import pytest

import shipzil as z
from shipzil.providers import Adapter, Quote
from shipzil.services import ServiceKey

FROM = z.Address(street1="1 A St", city="San Francisco", state="CA", postal_code="94117")
TO = z.Address(street1="1 B St", city="New York", state="NY", postal_code="10020")
SHIPMENT = z.Shipment(FROM, TO, (z.Parcel(weight=z.Weight.of(1, "lb")),))


class StubAdapter(Adapter):
    def __init__(self, name: str, rates: tuple[z.Rate, ...] = (), error: Exception | None = None):
        self.name = name
        self.rates = rates
        self.error = error
        self.buy_calls = 0

    def rate_single(self, shipment: z.Shipment) -> Quote:
        if self.error is not None:
            raise self.error
        return Quote(rates=self.rates, via=f"{self.name}:rates")

    def buy(self, shipment: z.Shipment, rate: z.Rate) -> z.Label:
        self.buy_calls += 1
        return z.Label(
            tracking_number=f"{self.name}-tracking",
            label_url="https://example.test/label",
            carrier=rate.carrier,
            service=rate.service,
            amount=rate.amount,
            currency=rate.currency,
            provider=self.name,
            shipment_id="shipment-1",
        )


def rate(provider: str, carrier: str, service: str, amount: str) -> z.Rate:
    key = ServiceKey.build(provider=provider, carrier=carrier, service=service)
    assert key is not None
    return z.Rate(
        carrier=carrier,
        service=service,
        amount=Decimal(amount),
        currency="USD",
        provider=provider,
        service_key=key,
    )


def test_gateway_calls_all_sources_when_no_fallback_is_configured() -> None:
    ep = StubAdapter("shippo", (rate("shippo", "usps", "ground", "10"),))
    es = StubAdapter("easyship", (rate("easyship", "fedex", "ground", "12"),))
    gateway = z.Gateway({"shippo-primary": ep, "easyship-primary": es})

    result = gateway.get_rates(SHIPMENT)

    assert [r.source for r in result.rates] == ["shippo-primary", "easyship-primary"]
    assert [r.service_key.provider for r in result.rates] == ["shippo", "easyship"]
    assert len(result.services or ()) == 2
    assert not result.errors


def test_cheapest_requires_one_known_currency() -> None:
    usd = rate("shippo", "usps", "ground", "10")
    eur = z.Rate(
        carrier="dhl",
        service="express",
        amount=Decimal("8"),
        currency="EUR",
        provider="easyship",
        service_key=ServiceKey.build(
            provider="easyship", carrier="dhl", service="express"
        ),
    )
    unknown = z.Rate(
        carrier="usps",
        service="ground",
        amount=Decimal("7"),
        provider="shipstation_v1",
        service_key=ServiceKey.build(
            provider="shipstation_v1", carrier="usps", service="ground"
        ),
    )

    assert z.GatewayQuote(rates=(usd,)).cheapest is usd
    assert z.GatewayQuote(rates=(usd, eur)).cheapest is None
    assert z.GatewayQuote(rates=(unknown,)).cheapest is None


def test_gateway_returns_partial_results_and_source_errors() -> None:
    good = StubAdapter("shippo", (rate("shippo", "usps", "ground", "10"),))
    bad = StubAdapter("shipstation_v2", error=z.RateLimitError("slow", provider="shipstation_v2"))
    gateway = z.Gateway({"shippo-primary": good, "ss-primary": bad})

    result = gateway.get_rates(SHIPMENT)

    assert len(result.rates) == 1
    assert result.sources[1].source == "ss-primary"
    assert isinstance(result.sources[1].error, z.RateLimitError)
    assert len(result.errors) == 1


def test_gateway_provider_and_carrier_filters_intersect() -> None:
    shippo = StubAdapter(
        "shippo",
        (
            rate("shippo", "usps", "ground", "10"),
            rate("shippo", "ups", "ground", "11"),
        ),
    )
    easyship = StubAdapter("easyship", (rate("easyship", "usps", "ground", "12"),))
    gateway = z.Gateway({"shippo-primary": shippo, "easyship-primary": easyship})

    result = gateway.get_rates(SHIPMENT, providers={"shippo"}, carriers={"usps"})

    assert [(r.source, r.service_key.carrier) for r in result.rates] == [
        ("shippo-primary", "usps")
    ]


def test_gateway_exact_service_filter_is_provider_namespaced() -> None:
    shippo = StubAdapter("shippo", (rate("shippo", "usps", "ground", "10"),))
    easyship = StubAdapter("easyship", (rate("easyship", "usps", "ground", "12"),))
    gateway = z.Gateway({"shippo-primary": shippo, "easyship-primary": easyship})

    result = gateway.get_rates(
        SHIPMENT,
        services={"shippo-usps-ground"},
    )

    assert [r.source for r in result.rates] == ["shippo-primary"]


def test_gateway_rejects_conflicting_provider_and_service_filters() -> None:
    gateway = z.Gateway({"shippo-primary": StubAdapter("shippo")})

    with pytest.raises(z.ConfigurationError, match="conflict"):
        gateway.get_rates(
            SHIPMENT,
            providers={"easyship"},
            services={"shippo-usps-ground"},
        )


def test_gateway_rejects_a_service_from_an_unconfigured_provider() -> None:
    gateway = z.Gateway({"shippo-primary": StubAdapter("shippo")})

    with pytest.raises(z.ConfigurationError, match="unconfigured"):
        gateway.get_rates(SHIPMENT, services={"sendcloud-usps-ground"})


def test_gateway_fallback_is_explicit_and_stops_at_first_matching_source() -> None:
    first = StubAdapter("shippo", (rate("shippo", "usps", "ground", "10"),))
    second = StubAdapter("easyship", (rate("easyship", "usps", "ground", "12"),))
    gateway = z.Gateway(
        {"shippo-primary": first, "easyship-primary": second},
        fallback=("shippo-primary", "easyship-primary"),
    )

    result = gateway.get_rates(SHIPMENT)

    assert [r.source for r in result.rates] == ["shippo-primary"]
    assert [item.source for item in result.sources] == ["shippo-primary"]


def test_gateway_fallback_continues_after_a_source_error() -> None:
    first = StubAdapter("shippo", error=z.ProviderError("down", provider="shippo"))
    second = StubAdapter("easyship", (rate("easyship", "usps", "ground", "12"),))
    gateway = z.Gateway(
        {"shippo-primary": first, "easyship-primary": second},
        fallback=("shippo-primary", "easyship-primary"),
    )

    result = gateway.get_rates(SHIPMENT)

    assert [r.source for r in result.rates] == ["easyship-primary"]
    assert isinstance(result.sources[0].error, z.ProviderError)


def test_gateway_buy_uses_the_source_that_returned_the_rate() -> None:
    adapter = StubAdapter("shippo", (rate("shippo", "usps", "ground", "10"),))
    gateway = z.Gateway({"shippo-primary": adapter})
    selected = gateway.get_rates(SHIPMENT).rates[0]

    label = gateway.buy(SHIPMENT, selected)

    assert adapter.buy_calls == 1
    assert label.source == "shippo-primary"


def test_gateway_rejects_a_rate_without_source_provenance() -> None:
    adapter = StubAdapter("shippo")
    gateway = z.Gateway({"shippo-primary": adapter})
    unbound = rate("shippo", "usps", "ground", "10")

    with pytest.raises(z.ConfigurationError, match="returned by Gateway"):
        gateway.buy(SHIPMENT, unbound)


def test_gateway_rejects_an_invalid_fallback_at_configuration_time() -> None:
    with pytest.raises(z.ConfigurationError, match="unknown sources"):
        z.Gateway({"shippo-primary": StubAdapter("shippo")}, fallback=("missing",))


def test_gateway_keeps_service_key_when_fanout_combines_rates() -> None:
    first = rate("shippo", "usps", "ground", "10")
    second = rate("shippo", "usps", "ground", "12")
    combined = z.multiparcel.combine_parcel_quotes(
        [Quote(rates=(first,)), Quote(rates=(second,))],
        provider="shippo",
        via="shippo:fanoutx2",
    ).rates[0]

    assert combined.service_key == first.service_key


def test_service_map_merges_sources_and_resolves_filters_with_and_semantics() -> None:
    usps = ServiceKey.build(provider="shippo", carrier="usps", service="usps_ground")
    ups = ServiceKey.build(provider="shippo", carrier="ups", service="ups_ground")
    assert usps is not None and ups is not None
    service_map = z.ServiceMap()
    service_map.add(usps, "Ground Advantage", "shippo-primary")
    service_map.add(usps, "Ground Advantage", "shippo-secondary")
    service_map.add(ups, "Ground", "shippo-primary")

    result = service_map.resolve(providers={"shippo"}, carriers={"usps"})

    assert [item.key for item in result] == [usps]
    assert result[0].name == "Ground Advantage"
    assert result[0].sources == ("shippo-primary", "shippo-secondary")


def test_service_key_parse_round_trips_machine_key_and_variant() -> None:
    key = ServiceKey.parse("shipstation_v1-usps-usps_ground_advantage-thick_envelope")

    assert key.provider == "shipstation_v1"
    assert key.carrier == "usps"
    assert key.service == "usps_ground_advantage"
    assert key.packaging == "thick_envelope"
    assert key.slug == "shipstation_v1-usps-usps_ground_advantage-thick_envelope"


class TestNothingIsDroppedSilently:
    """A filter must explain what it removed.

    `base.py` states the rule the whole library rests on: never return an empty
    rate list without populating exclusions. A caller-supplied filter is not
    exempt. Before this, `carriers={"dhl"}` discarded a `DHL Express` rate with no
    trace, because the two normalise to different carrier tokens.
    """

    @staticmethod
    def _unaddressable() -> z.Rate:
        """A rate whose carrier shipzil cannot resolve, so it has no ServiceKey."""
        key = ServiceKey.build(
            provider="easyship", carrier="", service="Unknown Courier Saver"
        )
        assert key is None, "fixture is only meaningful while this stays unaddressable"
        return z.Rate(
            carrier="",
            service="Unknown Courier Saver",
            amount=Decimal("9"),
            currency="USD",
            provider="easyship",
            service_key=None,
        )

    def test_carrier_filter_matches_every_network_in_the_brand(self) -> None:
        source = StubAdapter(
            "shippo",
            (
                rate("shippo", "DHL Express", "express_worldwide", "30"),
                rate("shippo", "DHL eCommerce", "parcel_expedited", "12"),
                rate("shippo", "usps", "ground_advantage", "8"),
            ),
        )
        result = z.Gateway({"s": source}).get_rates(SHIPMENT, carriers={"dhl"})

        kept = {r.service_key.carrier for r in result.rates if r.service_key}
        assert kept == {"dhl_express", "dhl_ecommerce"}, (
            "a brand filter must match its networks, not just an exact token"
        )

    def test_ups_filter_does_not_match_usps(self) -> None:
        """The prefix match must not create a false positive between real carriers."""
        source = StubAdapter("shippo", (rate("shippo", "usps", "ground_advantage", "8"),))
        result = z.Gateway({"s": source}).get_rates(SHIPMENT, carriers={"ups"})

        assert result.rates == ()
        assert [e.code for e in result.excluded] == [z.ExclusionCode.FILTERED_BY_REQUEST]

    def test_filtered_rate_is_reported_not_discarded(self) -> None:
        source = StubAdapter(
            "shippo",
            (
                rate("shippo", "usps", "ground_advantage", "8"),
                rate("shippo", "fedex", "ground", "11"),
            ),
        )
        result = z.Gateway({"s": source}).get_rates(SHIPMENT, carriers={"usps"})

        assert len(result.rates) == 1
        assert len(result.excluded) == 1
        dropped = result.excluded[0]
        assert dropped.code is z.ExclusionCode.FILTERED_BY_REQUEST
        assert dropped.source == "shipzil"
        assert dropped.carrier == "fedex"
        assert "fedex" in dropped.message

    def test_unaddressable_rate_is_reported_rather_than_vanishing(self) -> None:
        source = StubAdapter("easyship", (self._unaddressable(),))
        result = z.Gateway({"s": source}).get_rates(SHIPMENT, carriers={"dhl"})

        assert result.rates == ()
        assert [e.code for e in result.excluded] == [
            z.ExclusionCode.SERVICE_NOT_ADDRESSABLE
        ]
        assert "no carrier shipzil could resolve" in result.excluded[0].message

    def test_an_unfiltered_call_invents_no_exclusions(self) -> None:
        source = StubAdapter("shippo", (rate("shippo", "usps", "ground_advantage", "8"),))
        result = z.Gateway({"s": source}).get_rates(SHIPMENT)

        assert len(result.rates) == 1
        assert result.excluded == ()

    def test_service_filter_reports_what_it_removed(self) -> None:
        source = StubAdapter(
            "shippo",
            (
                rate("shippo", "usps", "ground_advantage", "8"),
                rate("shippo", "usps", "priority", "14"),
            ),
        )
        result = z.Gateway({"s": source}).get_rates(
            SHIPMENT, services={"shippo-usps-ground_advantage"}
        )

        assert [r.service for r in result.rates] == ["ground_advantage"]
        assert len(result.excluded) == 1
        assert result.excluded[0].code is z.ExclusionCode.FILTERED_BY_REQUEST
        assert "priority" in result.excluded[0].message

    def test_explain_surfaces_the_reason_a_rate_is_missing(self) -> None:
        source = StubAdapter(
            "shippo",
            (
                rate("shippo", "usps", "ground_advantage", "8"),
                rate("shippo", "fedex", "ground", "11"),
            ),
        )
        result = z.Gateway({"s": source}).get_rates(SHIPMENT, carriers={"usps"})

        text = result.explain()
        assert "filtered_by_request" in text
        assert "[shipzil]" in text


class TestSourcesAreCalledConcurrently:
    """Fan-out latency must be the slowest source, not the sum of all of them.

    Asserted on wall-clock against a deliberate sleep. The margin is wide (three
    slow sources must finish in well under their serial total) so the test is not
    flaky on a loaded machine, but it still fails outright if the calls serialise.
    """

    SLEEP = 0.20

    class SlowAdapter(Adapter):
        def __init__(self, name: str, delay: float, rate_: z.Rate) -> None:
            self.name = name
            self.delay = delay
            self._rate = rate_
            self.thread_names: list[str] = []

        def rate_single(self, shipment: z.Shipment) -> Quote:
            import threading
            import time

            self.thread_names.append(threading.current_thread().name)
            time.sleep(self.delay)
            return Quote(rates=(self._rate,), via=f"{self.name}:rates")

        def buy(self, shipment: z.Shipment, rate: z.Rate) -> z.Label:  # pragma: no cover
            raise NotImplementedError

    def _sources(self) -> dict[str, Adapter]:
        return {
            f"s{i}": self.SlowAdapter(
                f"p{i}", self.SLEEP, rate(f"p{i}", "usps", f"svc{i}", "10")
            )
            for i in range(3)
        }

    def test_three_sources_do_not_take_three_times_as_long(self) -> None:
        import time

        sources = self._sources()
        gateway = z.Gateway(sources)

        start = time.monotonic()
        result = gateway.get_rates(SHIPMENT)
        elapsed = time.monotonic() - start

        assert len(result.rates) == 3, "every source must still be represented"
        serial = self.SLEEP * len(sources)
        assert elapsed < serial * 0.75, (
            f"{elapsed:.3f}s for 3 sources sleeping {self.SLEEP}s each looks serial "
            f"(serial would be ~{serial:.3f}s)"
        )

    def test_rates_come_back_in_configured_source_order(self) -> None:
        """Output must not depend on which provider happened to answer first."""
        fast_then_slow = {
            "slow": self.SlowAdapter("pslow", 0.15, rate("pslow", "usps", "a", "10")),
            "fast": self.SlowAdapter("pfast", 0.0, rate("pfast", "usps", "b", "11")),
        }
        result = z.Gateway(fast_then_slow).get_rates(SHIPMENT)

        assert [r.source for r in result.rates] == ["slow", "fast"], (
            "results must be assembled in configured order, not completion order"
        )
        assert [s.source for s in result.sources] == ["slow", "fast"]

    def test_a_fallback_chain_stays_sequential(self) -> None:
        """Explicit fallback means stop at the first answer, so never race."""
        first = self.SlowAdapter("p1", 0.0, rate("p1", "usps", "a", "10"))
        second = self.SlowAdapter("p2", 0.0, rate("p2", "usps", "b", "11"))
        gateway = z.Gateway({"one": first, "two": second}, fallback=("one", "two"))

        result = gateway.get_rates(SHIPMENT)

        assert [r.source for r in result.rates] == ["one"]
        assert second.thread_names == [], "a lower-ranked source must not be called"

    def test_max_workers_is_validated(self) -> None:
        with pytest.raises(z.ConfigurationError, match="at least 1"):
            z.Gateway(self._sources(), max_workers=0)

    def test_a_provider_failure_does_not_cancel_the_others(self) -> None:
        sources: dict[str, Adapter] = {
            "good": self.SlowAdapter("pg", self.SLEEP, rate("pg", "usps", "a", "10")),
            "bad": StubAdapter("pb", error=z.ProviderError("upstream exploded")),
        }
        result = z.Gateway(sources).get_rates(SHIPMENT)

        assert len(result.rates) == 1
        assert len(result.errors) == 1
        assert "upstream exploded" in str(result.errors[0])


class TestPerParcelFanOutIsConcurrent:
    """The three surfaces without native multi-parcel rate each parcel separately."""

    def test_four_parcels_do_not_cost_four_round_trips_in_series(self) -> None:
        import time

        delay = 0.15

        class SlowLeg(Adapter):
            name = "slowleg"

            def rate_single(self, shipment: z.Shipment) -> Quote:
                time.sleep(delay)
                return Quote(
                    rates=(rate("slowleg", "usps", "ground", "5"),), via="slowleg:rates"
                )

            def buy(self, s: z.Shipment, r: z.Rate) -> z.Label:  # pragma: no cover
                raise NotImplementedError

        parcel = z.Parcel(weight=z.Weight.of(1, "lb"))
        multi = z.Shipment(FROM, TO, (parcel,) * 4)

        start = time.monotonic()
        result = z.Gateway({"s": SlowLeg()}).get_rates(multi)
        elapsed = time.monotonic() - start

        assert result.rates, "fan-out must still produce a combined rate"
        assert result.rates[0].strategy is z.Strategy.FANOUT
        assert result.rates[0].parcel_count == 4
        assert elapsed < (delay * 4) * 0.75, (
            f"{elapsed:.3f}s for 4 parcels at {delay}s each looks serial"
        )
