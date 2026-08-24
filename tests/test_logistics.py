"""Logistics concepts: hazmat, duty liability, residential class, packaging.

Every field here was taken from the provider's own OpenAPI specification, not
its prose documentation. Prose is what produced the hallucinated endpoints in
this library's history.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

import shipzil as z
from shipzil.providers import (
    EasyPostAdapter,
    EasyshipAdapter,
    ShippoAdapter,
    ShipStationV1Adapter,
    ShipStationV2Adapter,
)

FROM = z.Address(
    street1="215 Clayton St", city="San Francisco", state="CA", postal_code="94117",
    name="S", company="Co", phone="4151234567", email="s@example.com",
)
TO = z.Address(
    street1="1 Rockefeller Plaza", city="New York", state="NY", postal_code="10020",
    name="R", company="Co", phone="2125551234", email="r@example.com",
)
BOX = z.Dimensions.of(10, 8, 4, "in")
LB = z.Weight.of(16, "oz")


def _addr(cls: z.AddressClass) -> z.Address:
    return z.Address(street1="a", city="b", postal_code="c", address_class=cls)


def _ship(duties: z.DutiesPaidBy) -> z.Shipment:
    return z.Shipment(FROM, TO, (z.Parcel(weight=LB, dimensions=BOX),), duties_paid_by=duties)


class TestAddressClass:
    """Residential is tri-state. Silence must never become "commercial"."""

    def test_unknown_is_not_commercial(self) -> None:
        assert z.Address(street1="a", city="b", postal_code="c").residential is None

    def test_explicit_classes_map_to_booleans(self) -> None:
        assert _addr(z.AddressClass.RESIDENTIAL).residential is True
        assert _addr(z.AddressClass.COMMERCIAL).residential is False

    def test_po_box_and_military_are_not_booleans(self) -> None:
        """A boolean cannot express these, which is why Shippo v2 moved to an enum."""
        assert _addr(z.AddressClass.PO_BOX).residential is None
        assert _addr(z.AddressClass.MILITARY).residential is None

    @pytest.mark.parametrize(
        "adapter",
        [
            ShippoAdapter("shippo_test_x"),
            ShipStationV1Adapter("k", "s"),
            EasyPostAdapter("EZTKx"),
        ],
    )
    def test_no_adapter_sends_false_for_unknown(self, adapter: object) -> None:
        """The bug: bool(None) is False, which asserts commercial for ~$6/parcel."""
        import shipzil.providers.easypost as ep
        import shipzil.providers.shippo as sp
        import shipzil.providers.shipstation_v1 as s1

        unknown = z.Address(street1="a", city="b", postal_code="c")
        for mod, key in ((sp, "is_residential"), (ep, "residential"), (s1, "residential")):
            out = mod._address(unknown)
            assert out.get(key) is not False, f"{mod.__name__} sent False for unknown"


class TestDutyLiability:
    """shipzil used to hardcode DDU, making the recipient liable, silently."""

    def test_unspecified_sends_nothing(self) -> None:
        sh = z.Shipment(FROM, TO, (z.Parcel(weight=LB, dimensions=BOX),))
        assert sh.duties_paid_by is z.DutiesPaidBy.UNSPECIFIED
        assert EasyshipAdapter("sand_x")._incoterms(sh) is None

    def test_sender_is_ddp_and_recipient_is_ddu(self) -> None:
        a = EasyshipAdapter("sand_x")
        assert a._incoterms(_ship(z.DutiesPaidBy.SENDER)) == "DDP"
        assert a._incoterms(_ship(z.DutiesPaidBy.RECIPIENT)) == "DDU"

    def test_no_incoterm_literal_is_detached_from_the_callers_choice(self) -> None:
        """An incoterm may only appear near a `duties_paid_by` decision.

        The original bug was `"incoterms": "DDU"` sitting in a payload literal,
        which silently made the recipient liable on every international
        shipment. This asserts every DDP/DDU literal is within a few lines of
        the branch that reads the caller's choice, so a detached one fails.
        """
        import pathlib

        for f in pathlib.Path("shipzil/providers").glob("*.py"):
            lines = f.read_text().splitlines()
            for i, line in enumerate(lines):
                if '"DDU"' not in line and '"DDP"' not in line:
                    continue
                if line.strip().startswith("#") or line.strip().startswith("*"):
                    continue
                window = "\n".join(lines[max(0, i - 6) : i + 2])
                assert "duties_paid_by" in window, (
                    f"{f.name}:{i + 1} incoterm literal not driven by "
                    f"duties_paid_by: {line.strip()}"
                )


class TestHazmat:
    def test_lithium_packing_instructions_are_distinct(self) -> None:
        """PI966 and PI967 carry different duties and are not interchangeable."""
        assert z.LithiumBatteryPacking.PACKED_WITH_EQUIPMENT.value == "pi966"
        assert z.LithiumBatteryPacking.CONTAINED_IN_EQUIPMENT.value == "pi967"
        assert (
            z.LithiumBatteryPacking.PACKED_WITH_EQUIPMENT
            is not z.LithiumBatteryPacking.CONTAINED_IN_EQUIPMENT
        )

    def test_easyship_maps_each_instruction_to_its_own_flag(self) -> None:
        a = EasyshipAdapter("sand_x")
        p966 = a._hazmat_item_flags(
            z.DangerousGoods(lithium_batteries=z.LithiumBatteryPacking.PACKED_WITH_EQUIPMENT)
        )
        p967 = a._hazmat_item_flags(
            z.DangerousGoods(lithium_batteries=z.LithiumBatteryPacking.CONTAINED_IN_EQUIPMENT)
        )
        assert p966 == {"contains_battery_pi966": True}
        assert p967 == {"contains_battery_pi967": True}

    def test_no_hazmat_means_no_flags(self) -> None:
        assert EasyshipAdapter("sand_x")._hazmat_item_flags(None) == {}

    def test_dry_ice_requires_a_weight(self) -> None:
        """Shippo makes it mandatory and rejects a weight above the parcel's."""
        ice = z.DryIce(weight=z.Weight.of(2, "lb"))
        assert ice.contains is True
        with pytest.raises(TypeError):
            z.DryIce()  # type: ignore[call-arg]

    def test_shippo_builds_the_nested_extra_structure(self) -> None:
        dg = z.DangerousGoods(
            lithium_batteries=z.LithiumBatteryPacking.CONTAINED_IN_EQUIPMENT,
            biological_material=True,
            contains_alcohol=True,
            alcohol_recipient="licensee",
            dry_ice=z.DryIce(weight=z.Weight.of(1, "kg")),
        )
        parcel = z.Parcel(weight=LB, dimensions=BOX, dangerous_goods=dg)
        extra = ShippoAdapter("shippo_test_x")._extra(
            z.Shipment(FROM, TO, (parcel,)), parcel
        )
        assert extra["dangerous_goods"]["contains"] is True
        assert extra["dangerous_goods"]["lithium_batteries"]["contains"] is True
        assert extra["dangerous_goods"]["biological_material"]["contains"] is True
        assert extra["dry_ice"]["contains_dry_ice"] is True
        assert extra["alcohol"] == {"contains_alcohol": True, "recipient_type": "licensee"}

    def test_shipstation_v2_sends_flags_and_emergency_contact(self) -> None:
        dg = z.DangerousGoods(
            contains_alcohol=True,
            dry_ice=z.DryIce(weight=z.Weight.of(1, "kg")),
            emergency_contact_name="Ops",
            emergency_contact_phone="4151234567",
        )
        parcel = z.Parcel(weight=LB, dimensions=BOX, dangerous_goods=dg)
        opts = ShipStationV2Adapter("k")._advanced_options(
            z.Shipment(FROM, TO, (parcel,)), parcel
        )
        assert opts["dangerous_goods"] is True
        assert opts["dangerous_goods_contact"] == {"name": "Ops", "phone": "4151234567"}
        assert opts["dry_ice"] is True
        assert opts["dry_ice_weight"]["unit"] == "kilogram"

    def test_declared_capability_matches_the_specs(self) -> None:
        """Read from each provider's OpenAPI, not assumed."""
        assert "lithium_batteries" in ShippoAdapter("shippo_test_x").hazmat_fields
        assert "biological_material" in ShippoAdapter("shippo_test_x").hazmat_fields
        assert "lithium_batteries" in EasyshipAdapter("sand_x").hazmat_fields
        # v1 has no hazmat fields at all.
        assert ShipStationV1Adapter("k", "s").hazmat_fields == frozenset()
        # Not claimed for EasyPost: its docs have not been consulted.
        assert EasyPostAdapter("EZTKx").hazmat_fields == frozenset()

    def test_dropped_detail_is_reported_not_swallowed(self) -> None:
        dg = z.DangerousGoods(
            lithium_batteries=z.LithiumBatteryPacking.CONTAINED_IN_EQUIPMENT,
            un_number="UN3481",
            hazard_class="9",
            packing_group="ii",
        )
        sh = z.Shipment(FROM, TO, (z.Parcel(weight=LB, dimensions=BOX, dangerous_goods=dg),))
        gap = ShippoAdapter("shippo_test_x").hazmat_fidelity_gap(sh)
        assert gap is not None
        assert gap.code is z.ExclusionCode.HAZMAT_DETAIL_UNSUPPORTED
        assert "regulated_detail" in gap.message
        assert gap.source == "shipzil"

    def test_v1_reports_losing_the_battery_flag_entirely(self) -> None:
        dg = z.DangerousGoods(lithium_batteries=z.LithiumBatteryPacking.PACKED_WITH_EQUIPMENT)
        sh = z.Shipment(FROM, TO, (z.Parcel(weight=LB, dimensions=BOX, dangerous_goods=dg),))
        gap = ShipStationV1Adapter("k", "s").hazmat_fidelity_gap(sh)
        assert gap is not None and "lithium_batteries" in gap.message

    def test_no_hazmat_no_gap(self) -> None:
        sh = z.Shipment(FROM, TO, (z.Parcel(weight=LB, dimensions=BOX),))
        for a in (ShippoAdapter("shippo_test_x"), ShipStationV1Adapter("k", "s")):
            assert a.hazmat_fidelity_gap(sh) is None

    def test_the_gap_reaches_the_quote_via_the_client(self) -> None:
        """Attached by Client so no adapter can forget it."""
        import shipzil.providers.shipstation_v1 as s1

        dg = z.DangerousGoods(lithium_batteries=z.LithiumBatteryPacking.PACKED_WITH_EQUIPMENT)
        sh = z.Shipment(FROM, TO, (z.Parcel(weight=LB, dimensions=BOX, dangerous_goods=dg),))
        adapter = ShipStationV1Adapter("k", "s", carriers=("stamps_com",))
        original = s1.request
        s1.request = lambda *a, **k: (200, [])  # type: ignore[assignment]
        try:
            quote = z.Client(adapter).get_rates(sh)
        finally:
            s1.request = original  # type: ignore[assignment]
        codes = [e.code for e in quote.excluded]
        assert z.ExclusionCode.HAZMAT_DETAIL_UNSUPPORTED in codes


class TestPackagingTemplate:
    """A carrier template replaces dimensions. Shippo enforces this schematically."""

    def test_a_template_satisfies_the_size_requirement(self) -> None:
        p = z.Parcel(weight=LB, packaging=z.PackagingTemplate("USPS_FlatRateEnvelope"))
        assert p.dimensions is None
        assert p.has_dimensions is True, "a template supplies the size"

    def test_shippo_omits_dimensions_entirely_with_a_template(self) -> None:
        import shipzil.providers.shippo as sp

        out = sp._parcel(
            z.Parcel(
                weight=LB, dimensions=BOX,
                packaging=z.PackagingTemplate("USPS_SmallFlatRateBox"),
            )
        )
        assert out["template"] == "USPS_SmallFlatRateBox"
        # ParcelCreateFromTemplateRequest requires these to be ABSENT.
        for k in ("length", "width", "height", "distance_unit"):
            assert k not in out, f"{k} must not be sent alongside a template"
        assert "weight" in out, "flat rate still has a weight ceiling"

    def test_shipstation_v2_uses_package_code(self) -> None:
        import shipzil.providers.shipstation_v2 as s2

        out = s2._package(z.Parcel(weight=LB, packaging=z.PackagingTemplate("flat_rate_envelope")))
        assert out["package_code"] == "flat_rate_envelope"
        assert "dimensions" not in out

    def test_easyship_uses_a_box_slug(self) -> None:
        out = EasyshipAdapter("sand_x")._parcel(
            z.Parcel(weight=LB, packaging=z.PackagingTemplate("usps_flat_rate_envelope"))
        )
        assert out["box"] == {"slug": "usps_flat_rate_envelope"}


class TestValuesAreNotCollapsed:
    def test_customs_value_and_insured_value_are_separate(self) -> None:
        """Three distinct numbers: customs value, insured value, COD amount."""
        item = z.Item("thing", value=Decimal("40"))
        parcel = z.Parcel(weight=LB, dimensions=BOX, items=(item,), insured_value=Decimal("500"))
        assert item.value == Decimal("40")
        assert parcel.insured_value == Decimal("500")

    def test_shippo_sends_insurance_separately_from_customs(self) -> None:
        parcel = z.Parcel(
            weight=LB, dimensions=BOX,
            items=(z.Item("thing", value=Decimal("40")),),
            insured_value=Decimal("500"),
        )
        extra = ShippoAdapter("shippo_test_x")._extra(z.Shipment(FROM, TO, (parcel,)), parcel)
        assert extra["insurance"]["amount"] == "500"


class TestSurchargeBreakdown:
    def test_rate_keeps_named_components(self) -> None:
        r = z.Rate(
            carrier="FedEx", service="2Day", amount=Decimal("63.60"),
            base_amount=Decimal("56.12"), provider="easyship",
            surcharges=(
                ("fuel_surcharge", Decimal("3.03")),
                ("residential_full_fee", Decimal("6.15")),
            ),
        )
        assert r.surcharge_total == Decimal("9.18")
        assert dict(r.surcharges)["residential_full_fee"] == Decimal("6.15")

    def test_absent_breakdown_totals_zero_not_none(self) -> None:
        r = z.Rate(carrier="x", service="y", amount=Decimal("1"), provider="p")
        assert r.surcharges == ()
        assert r.surcharge_total == Decimal(0)


class TestTrackingLegs:
    def test_a_shipment_can_carry_several_numbers(self) -> None:
        legs = (
            z.TrackingLeg(tracking_number="A1", leg_number=1, handler="fedex"),
            z.TrackingLeg(tracking_number="B2", leg_number=2, handler="aramex"),
        )
        lbl = z.Label(
            tracking_number="A1", label_url="u", carrier="c", service="s",
            amount=Decimal("1"), provider="p", tracking_legs=legs,
        )
        assert lbl.tracking_number == legs[0].tracking_number
        assert len(lbl.tracking_legs) == 2
        assert lbl.tracking_legs[1].handler == "aramex"


class TestCustomsAndItems:
    """Items live under the Parcel, and that is the correct foundation.

    Verified from the specs, not assumed:

    * Easyship  `parcels[].items[]`                       — per parcel
    * ShipEngine `packages[].products[]`                  — per package, and the
      shipment-level `customs_items` is marked **deprecated**: "Please provide
      this information under `products` inside `packages`"
    * Shippo    `customs_declaration.items[]`             — flat, shipment level,
      with no parcel reference at all

    Two of three modern shapes are per parcel, and the one that is not is the
    deprecated one. Nesting items under Parcel is therefore lossless in the
    direction that matters: a flat list can always be produced from per-parcel
    items, but per-parcel items cannot be recovered from a flat list without
    inventing an assignment.
    """

    CA = z.Address(
        street1="220 Yonge St", city="Toronto", state="ON", postal_code="M5B 2H1",
        name="R", phone="4165550199", email="r@example.com", country="CA",
    )

    def _intl(self, **kw: object) -> z.Shipment:
        item = z.Item(
            "cotton t-shirt", quantity=2, weight=z.Weight.of(6, "oz"),
            value=Decimal("15"), hs_code="610910", origin_country="US",
        )
        parcel = z.Parcel(weight=LB, dimensions=BOX, items=(item,))
        return z.Shipment(FROM, self.CA, (parcel,), **kw)  # type: ignore[arg-type]

    def test_items_are_nested_under_the_parcel(self) -> None:
        import dataclasses as dc

        assert "items" in {f.name for f in dc.fields(z.Parcel)}
        assert "items" not in {f.name for f in dc.fields(z.Shipment)}

    def test_declared_value_sums_quantity_across_parcels(self) -> None:
        a = z.Item("a", quantity=2, value=Decimal("15"))
        b = z.Item("b", quantity=1, value=Decimal("40"))
        sh = z.Shipment(
            FROM, self.CA,
            (z.Parcel(weight=LB, items=(a,)), z.Parcel(weight=LB, items=(b,))),
        )
        assert sh.declared_value == Decimal("70")

    def test_domestic_needs_no_customs(self) -> None:
        sh = z.Shipment(FROM, TO, (z.Parcel(weight=LB, dimensions=BOX),))
        a = ShippoAdapter("shippo_test_x")
        assert a._customs(sh) is None
        assert a._customs_gap(sh) is None

    def test_cross_border_builds_a_declaration_from_parcel_items(self) -> None:
        d = ShippoAdapter("shippo_test_x")._customs(self._intl())
        assert d is not None
        assert d["items"][0]["hs_code"] == "610910"
        assert d["items"][0]["quantity"] == 2
        assert d["items"][0]["origin_country"] == "US"
        assert d["non_delivery_option"] == "RETURN", "abandonment destroys the goods"
        assert d["certify"] is True

    def test_cross_border_without_declarable_items_is_refused(self) -> None:
        """Rating succeeds without customs and the purchase then fails, so the
        refusal has to come at rating time."""
        bare = z.Shipment(FROM, self.CA, (z.Parcel(weight=LB, dimensions=BOX),))
        gap = ShippoAdapter("shippo_test_x")._customs_gap(bare)
        assert gap is not None
        assert "cross-border" in gap.message

    def test_eei_exemption_is_derived_only_below_the_threshold(self) -> None:
        """NOEEI 30.37(a) covers shipments of $2,500 or less per Schedule B."""
        assert self._intl().derived_eei_exemption == "NOEEI_30_37_a"
        big = z.Item("gold bar", quantity=1, value=Decimal("5000"), weight=LB)
        high = z.Shipment(FROM, self.CA, (z.Parcel(weight=LB, dimensions=BOX, items=(big,)),))
        # Above the threshold this needs an AES filing and an ITN.
        assert high.derived_eei_exemption is None

    def test_an_explicit_exemption_always_wins(self) -> None:
        big = z.Item("gold bar", quantity=1, value=Decimal("5000"), weight=LB)
        sh = z.Shipment(
            FROM, self.CA, (z.Parcel(weight=LB, dimensions=BOX, items=(big,)),),
            eei_exemption="AES_ITN",
        )
        assert sh.derived_eei_exemption == "AES_ITN"
        assert ShippoAdapter("shippo_test_x")._customs(sh) is not None

    def test_high_value_without_an_itn_produces_no_declaration(self) -> None:
        big = z.Item("gold bar", quantity=1, value=Decimal("5000"), weight=LB)
        sh = z.Shipment(FROM, self.CA, (z.Parcel(weight=LB, dimensions=BOX, items=(big,)),))
        assert ShippoAdapter("shippo_test_x")._customs(sh) is None, (
            "filing a false exemption is worse than refusing"
        )

    def test_duty_liability_reaches_the_declaration(self) -> None:
        a = ShippoAdapter("shippo_test_x")
        assert a._customs(self._intl(duties_paid_by=z.DutiesPaidBy.SENDER))["incoterm"] == "DDP"
        assert a._customs(self._intl(duties_paid_by=z.DutiesPaidBy.RECIPIENT))["incoterm"] == "DDU"
        assert "incoterm" not in a._customs(self._intl())


class TestFlatRateEndToEnd:
    """The pre-flight check, not just the payload builder.

    `Parcel.has_dimensions` existed, was unit-tested, and no adapter read it, so
    every flat-rate parcel was refused before a request was built. The unit test
    called `_parcel()` directly and so never saw it.
    """

    def test_shippo_pre_flight_accepts_a_template(self) -> None:
        import shipzil.providers.shippo as sp

        p = z.Parcel(weight=LB, packaging=z.PackagingTemplate("USPS_FlatRateEnvelope"))
        assert sp._dimension_gap(p) is None, "a template satisfies the size requirement"

    def test_shippo_pre_flight_still_rejects_a_bare_weight(self) -> None:
        import shipzil.providers.shippo as sp

        gap = sp._dimension_gap(z.Parcel(weight=LB))
        assert gap is not None and gap.code is z.ExclusionCode.DIMENSIONS_REQUIRED

    def test_v1_sends_package_code_without_dimensions(self) -> None:
        a = ShipStationV1Adapter("k", "s", carriers=("stamps_com",))
        sh = z.Shipment(
            FROM, TO, (z.Parcel(weight=LB, packaging=z.PackagingTemplate("flat_rate_envelope")),)
        )
        captured: dict[str, object] = {}
        import shipzil.providers.shipstation_v1 as s1

        original = s1.request
        s1.request = lambda *a, **k: (captured.update(k.get("json") or {}), (200, []))[1]  # type: ignore[assignment]
        try:
            a._rates_for_carrier("stamps_com", sh)
        finally:
            s1.request = original  # type: ignore[assignment]
        assert captured["packageCode"] == "flat_rate_envelope"
        assert "dimensions" not in captured
