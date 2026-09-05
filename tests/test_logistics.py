"""Logistics concepts: hazmat, duty liability, residential class, packaging.

Every field here was taken from the provider's own OpenAPI specification, not
its prose documentation. Prose is what produced the hallucinated endpoints in
this library's history.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from decimal import Decimal

import pytest
from transport import RecordingTransport

import shipzil as z
from shipzil._client import Client as _Client
from shipzil.providers import (
    Adapter,
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
        ],
    )
    def test_no_adapter_sends_false_for_unknown(self, adapter: object) -> None:
        """The bug: bool(None) is False, which asserts commercial for ~$6/parcel."""
        import shipzil.providers.shippo as sp
        import shipzil.providers.shipstation_v1 as s1

        unknown = z.Address(street1="a", city="b", postal_code="c")
        for mod, key in ((sp, "is_residential"), (s1, "residential")):
            out = mod._address(unknown)
            assert out.get(key) is not False, f"{mod.__name__} sent False for unknown"


class TestDutyLiability:
    """shipzil used to hardcode DDU, making the recipient liable, silently."""

    #: Each adapter's declared spelling, and what it means on the wire. Three
    #: providers had a near-identical if/elif of their own before this was
    #: centralised on `Adapter.render_incoterm`.
    STYLES = (
        (ShippoAdapter("shippo_test_x"), "DDP", "DDU"),
        (EasyshipAdapter("sand_x"), "DDP", "DDU"),
        (ShipStationV2Adapter("k"), "ddp", "ddu"),
        (ShipStationV1Adapter("k", "s"), None, None),
    )

    def test_unspecified_sends_nothing_on_every_provider(self) -> None:
        sh = z.Shipment(FROM, TO, (z.Parcel(weight=LB, dimensions=BOX),))
        assert sh.duties_paid_by is z.DutiesPaidBy.UNSPECIFIED
        for adapter, _ddp, _ddu in self.STYLES:
            assert adapter.render_incoterm(sh) is None

    def test_sender_is_ddp_and_recipient_is_ddu_in_each_spelling(self) -> None:
        """One concept, three spellings, one mapping."""
        for adapter, ddp, ddu in self.STYLES:
            assert adapter.render_incoterm(_ship(z.DutiesPaidBy.SENDER)) == ddp
            assert adapter.render_incoterm(_ship(z.DutiesPaidBy.RECIPIENT)) == ddu

    def test_providers_that_cannot_express_duties_say_so(self) -> None:
        """Silently dropping a commercial decision is the bug being prevented.

        Measured before this existed: ShipStation v1 had no duty field at all.
        """
        for adapter, ddp, _ddu in self.STYLES:
            gap = adapter.duties_gap(_ship(z.DutiesPaidBy.SENDER))
            if ddp is None:
                assert gap is not None, f"{adapter.name} drops duties silently"
                assert gap.code is z.ExclusionCode.DUTIES_UNSUPPORTED
            else:
                assert gap is None, f"{adapter.name} expresses duties, should not warn"

    def test_no_duties_gap_when_the_caller_expressed_no_preference(self) -> None:
        sh = z.Shipment(FROM, TO, (z.Parcel(weight=LB, dimensions=BOX),))
        for adapter, _ddp, _ddu in self.STYLES:
            assert adapter.duties_gap(sh) is None

    def test_incoterm_mapping_exists_in_exactly_one_place(self) -> None:
        """Adapters declare a style; none of them branch on DutiesPaidBy again."""
        import pathlib as _p

        for f in sorted(_p.Path("shipzil/providers").glob("*.py")):
            if f.name == "base.py":
                continue
            lines = f.read_text().splitlines()
            for i, line in enumerate(lines):
                if "DutiesPaidBy." not in line or line.strip().startswith("#"):
                    continue
                # The one legitimate remaining site: ShipEngine expresses duty
                # liability twice, and `advanced_options.delivered_duty_paid` is
                # a boolean rather than an incoterm string, so it cannot come
                # from `render_incoterm`.
                window = "\n".join(lines[i : i + 3])
                assert "delivered_duty_paid" in window, (
                    f"{f.name}:{i + 1} maps DutiesPaidBy by hand; declare "
                    f"incoterm_style and call render_incoterm instead: {line.strip()}"
                )

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
        dg = z.DangerousGoods(lithium_batteries=z.LithiumBatteryPacking.PACKED_WITH_EQUIPMENT)
        sh = z.Shipment(FROM, TO, (z.Parcel(weight=LB, dimensions=BOX, dangerous_goods=dg),))
        adapter = ShipStationV1Adapter("k", "s", carriers=("stamps_com",))
        adapter.transport = RecordingTransport(default=(200, []))

        quote = _Client(adapter).get_rates(sh)

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
        assert a.customs_gap(sh) is None

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
        gap = ShippoAdapter("shippo_test_x").customs_gap(bare)
        assert gap is not None
        assert gap.code is z.ExclusionCode.CUSTOMS_DECLARATION_REQUIRED
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
        transport = RecordingTransport(default=(200, []))
        a.transport = transport

        a._rates_for_carrier("stamps_com", sh)

        sent = transport.bodies[0]
        assert isinstance(sent, dict)
        assert sent["packageCode"] == "flat_rate_envelope"
        assert "dimensions" not in sent


class TestCustomsAcrossAllProviders:
    """Every adapter builds customs, in its own spelling.

    The test also asserts no private helper is orphaned, because a correct builder
    whose output is discarded still returns the right thing.
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
        return z.Shipment(  # type: ignore[arg-type]
            FROM, self.CA, (z.Parcel(weight=LB, dimensions=BOX, items=(item,)),), **kw
        )

    def test_no_private_helper_is_orphaned(self) -> None:
        """A helper nobody calls is indistinguishable from a broken feature."""
        import ast
        import pathlib as _p

        files = list(_p.Path("shipzil").rglob("*.py"))
        for path in files:
            tree = ast.parse(path.read_text())
            defined = {
                n.name
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef)
                and n.name.startswith("_")
                and not n.name.startswith("__")
            }
            used: set[str] = set()
            for n in ast.walk(tree):
                if isinstance(n, ast.Attribute):
                    used.add(n.attr)
                elif isinstance(n, ast.Name):
                    used.add(n.id)
            elsewhere = "".join(q.read_text() for q in files if q != path)
            orphans = sorted(
                d for d in defined - used if d not in elsewhere
            )
            assert not orphans, f"{path.name}: defined but never called: {orphans}"

    def test_each_provider_uses_the_basis_its_own_docs_specify(self) -> None:
        """The basis is not uniform, and assuming it was is how this broke.

        quantity 2, $15 and 6 oz each, so line totals are $30 / 12 oz. Which of
        those a provider wants is documented per provider, and it splits 2-2:

          Shippo     line total  "Total value of this item, i.e. quantity *
                                  value per item"
          v1         line total  "The value (in USD) of the line item"
          v2         PER UNIT    "The declared value of *each* item"
          Easyship   PER UNIT    "this value refers to the unit rather than
                                  the total"

        The basis is explicit instead of being inferred from a shared payload
        helper, so v2 and Easyship remain per-unit.
        """
        sh = self._intl()

        sp = ShippoAdapter("shippo_test_x")._customs(sh)
        assert sp is not None
        assert sp["items"][0]["value_amount"] == "30"
        assert sp["items"][0]["net_weight"].startswith("12")

        v1 = ShipStationV1Adapter("k", "s")._international_options(sh)
        assert v1 is not None
        assert v1["customsItems"][0]["value"] == 30.0

        prods = ShipStationV2Adapter("k")._products(sh.parcels[0], sh)
        assert prods[0]["value"]["amount"] == 15.0, "v2 wants per-unit value"
        assert prods[0]["weight"]["value"] == 6.0, "v2 wants per-unit weight"
        assert prods[0]["quantity"] == 2

    def test_easyship_sends_per_unit_value(self) -> None:
        """"this value refers to the unit rather than the total"."""
        sh = self._intl()
        parcel = EasyshipAdapter("sand_x")._parcel(sh.parcels[0])
        item = parcel["items"][0]
        assert item["declared_customs_value"] == 15.0
        assert item["quantity"] == 2

    def test_every_adapter_declares_which_basis_it_uses(self) -> None:
        """An undeclared basis is an unexamined one."""
        expected = {
            "shippo": "line_total",
            "shipstation_v1": "line_total",
            "shipstation_v2": "per_unit",
            "easyship": "per_unit",
        }
        for adapter in (
            ShippoAdapter("shippo_test_x"),
            ShipStationV1Adapter("k", "s"),
            ShipStationV2Adapter("k"),
            EasyshipAdapter("sand_x"),
        ):
            assert adapter.customs_value_basis == expected[adapter.name]

    def test_customs_lines_carry_both_bases(self) -> None:
        line = ShippoAdapter("shippo_test_x").customs_lines(self._intl())[0]
        assert (line.unit_value, line.line_value) == (Decimal("15"), Decimal("30"))
        assert float(line.unit_weight.to("oz")) == 6.0
        assert float(line.line_weight.to("oz")) == 12.0

    def test_provider_specific_field_names(self) -> None:
        """The same concept, three spellings. None is portable."""
        sh = self._intl()
        assert "hs_code" in ShippoAdapter("shippo_test_x")._customs(sh)["items"][0]
        v1 = ShipStationV1Adapter("k", "s")._international_options(sh)
        assert "harmonizedTariffCode" in v1["customsItems"][0]
        prods = ShipStationV2Adapter("k")._products(sh.parcels[0], sh)
        assert "harmonized_tariff_code" in prods[0]

    def test_eei_is_rendered_per_provider(self) -> None:
        """Same regulation, in the provider token spelling."""
        sh = self._intl()
        assert ShippoAdapter("shippo_test_x").render_eei(sh) == "NOEEI_30_37_a"

    def test_shipstation_v2_uses_lowercase_incoterms(self) -> None:
        """`delivery_duty_paid` is rejected: "Unknown TermsOfTradeCode value"."""
        a = ShipStationV2Adapter("k")
        sender = a._customs(self._intl(duties_paid_by=z.DutiesPaidBy.SENDER))
        recipient = a._customs(self._intl(duties_paid_by=z.DutiesPaidBy.RECIPIENT))
        assert sender["terms_of_trade_code"] == "ddp"
        assert recipient["terms_of_trade_code"] == "ddu"

    def test_shipstation_v2_keeps_lines_on_the_package(self) -> None:
        """It is the only provider that preserves the parcel association."""
        sh = self._intl()
        assert "customs_items" not in ShipStationV2Adapter("k")._customs(sh)
        assert ShipStationV2Adapter("k")._products(sh.parcels[0], sh)

    @pytest.mark.parametrize(
        "adapter",
        [
            ShippoAdapter("shippo_test_x"),
            ShipStationV1Adapter("k", "s"),
            ShipStationV2Adapter("k"),
            EasyshipAdapter("sand_x"),
        ],
    )
    def test_domestic_never_triggers_a_customs_gap(self, adapter: object) -> None:
        sh = z.Shipment(FROM, TO, (z.Parcel(weight=LB, dimensions=BOX),))
        assert adapter.customs_gap(sh) is None  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        "adapter",
        [
            ShippoAdapter("shippo_test_x"),
            ShipStationV1Adapter("k", "s"),
            ShipStationV2Adapter("k"),
            EasyshipAdapter("sand_x"),
        ],
    )
    def test_cross_border_without_items_is_refused_everywhere(self, adapter: object) -> None:
        bare = z.Shipment(FROM, self.CA, (z.Parcel(weight=LB, dimensions=BOX),))
        gap = adapter.customs_gap(bare)  # type: ignore[attr-defined]
        assert gap is not None
        assert gap.code is z.ExclusionCode.CUSTOMS_DECLARATION_REQUIRED

    def test_high_value_export_is_refused_everywhere(self) -> None:
        big = z.Item("gold bar", quantity=1, value=Decimal("5000"), weight=LB)
        sh = z.Shipment(FROM, self.CA, (z.Parcel(weight=LB, dimensions=BOX, items=(big,)),))
        for a in (ShippoAdapter("shippo_test_x"),):
            gap = a.customs_gap(sh)
            assert gap is not None and "AES" in gap.message

    def test_one_incomplete_item_blocks_the_whole_declaration(self) -> None:
        complete = z.Item("shirt", weight=LB, value=Decimal("20"))
        incomplete = z.Item("hat", weight=LB)
        shipment = z.Shipment(
            FROM,
            self.CA,
            (z.Parcel(weight=LB, dimensions=BOX, items=(complete, incomplete)),),
        )

        gap = ShippoAdapter("shippo_test_x").customs_gap(shipment)

        assert gap is not None
        assert gap.code is z.ExclusionCode.CUSTOMS_DECLARATION_REQUIRED
        assert "'hat'" in gap.message


class TestCustomsReachesTheWire:
    """Assert customs appears in the outgoing request body, per provider.

    The unit tests above assert what `_customs()` *returns*. These tests patch the
    single `shipzil.http.request` choke point and inspect the bytes each adapter
    would actually send, which catches a builder whose output is discarded.

    The stub answers every call with `{}`, so the adapter raises while parsing.
    That is fine and deliberate: the payload is captured at call time, before
    the response matters, so the flow does not need to be mocked convincingly.
    """

    CA = z.Address(
        street1="220 Yonge St", city="Toronto", state="ON", postal_code="M5B 2H1",
        name="R", phone="4165550199", email="r@example.com", country="CA",
    )

    def _intl(self) -> z.Shipment:
        item = z.Item(
            "cotton t-shirt", quantity=2, weight=z.Weight.of(6, "oz"),
            value=Decimal("15"), hs_code="610910", origin_country="US",
        )
        return z.Shipment(
            FROM, self.CA, (z.Parcel(weight=LB, dimensions=BOX, items=(item,)),),
            duties_paid_by=z.DutiesPaidBy.SENDER,
        )

    @staticmethod
    def _blob(adapter: Adapter, call: Callable[[], object]) -> str:
        """Run `call` against a recording transport, returning the bytes sent.

        The scripted reply is an empty object, so the adapter will fail while
        parsing. That is deliberate and harmless: the payload is captured at send
        time. `ShipzilError` is tolerated for that reason; anything else is a real
        bug and propagates.
        """
        transport = RecordingTransport()
        adapter.transport = transport
        with contextlib.suppress(z.ShipzilError):
            call()
        assert transport.requests, "adapter sent no request at all"
        assert transport.bodies, "adapter sent no JSON body at all"
        return transport.blob

    def test_shippo_sends_customs_declaration_on_shipment_creation(self) -> None:
        adapter = ShippoAdapter("shippo_test_x")
        blob = self._blob(adapter, lambda: adapter.rate_single(self._intl()))
        assert "customs_declaration" in blob
        assert "hs_code" in blob
        assert "NOEEI_30_37_a" in blob

    def test_shipstation_v2_sends_customs_and_products_on_rating(self) -> None:
        adapter = ShipStationV2Adapter("k")
        blob = self._blob(adapter, lambda: adapter.rate_single(self._intl()))
        assert "customs" in blob
        assert "harmonized_tariff_code" in blob
        assert '"terms_of_trade_code": "ddp"' in blob

    def test_easyship_sends_customs_bearing_items_on_rating(self) -> None:
        adapter = EasyshipAdapter("sand_x")
        blob = self._blob(adapter, lambda: adapter.rate_single(self._intl()))
        assert "hs_code" in blob
        assert "610910" in blob
        assert "incoterms" in blob

    def test_shipstation_v1_sends_international_options_on_label_creation(self) -> None:
        """v1 is the exception: `getrates` has no customs field, `createlabel` does.

        So this is the one provider whose customs cannot be proven by rating.
        """
        adapter = ShipStationV1Adapter("k", "s")
        rate = z.Rate(
            carrier="stamps_com", service="USPS First Class International",
            amount=Decimal("24.69"), service_code="usps_first_class_mail",
            provider="shipstation_v1", raw={"_carrier_code": "stamps_com"},
        )
        blob = self._blob(adapter, lambda: adapter.buy(self._intl(), rate))
        assert "internationalOptions" in blob
        assert "customsItems" in blob
        assert "harmonizedTariffCode" in blob
        assert "610910" in blob

    def test_domestic_sends_no_customs_anywhere(self) -> None:
        """A domestic shipment must not acquire a customs block."""
        domestic = z.Shipment(FROM, TO, (z.Parcel(weight=LB, dimensions=BOX),))
        for adapter in (ShippoAdapter("shippo_test_x"),):
            blob = self._blob(adapter, lambda a=adapter: a.rate_single(domestic))
            assert "customs" not in blob, f"{adapter.name} sent customs on a domestic label"


class TestFanOutPreservesTheShipment:
    """Fan-out must not quietly change the shipment it is splitting.

    `single_parcel_shipment` once named a subset of `Shipment`'s fields, so
    `duties_paid_by`, `eei_exemption` and `ship_date` were dropped on the path
    that four of six provider surfaces take for every multi-parcel shipment. The
    customs work in this module was all verified on single-parcel shipments, so
    none of it noticed.
    """

    FROM = FROM
    CA = z.Address(
        street1="220 Yonge St", city="Toronto", state="ON", postal_code="M5B 2H1",
        name="R", phone="4165550199", email="r@example.com", country="CA",
    )

    @staticmethod
    def _parcel(value: str) -> z.Parcel:
        return z.Parcel(
            weight=LB, dimensions=BOX,
            items=(
                z.Item(
                    "cotton t-shirt", quantity=2, weight=z.Weight.of(6, "oz"),
                    value=Decimal(value), hs_code="610910", origin_country="US",
                ),
            ),
        )

    def test_fan_out_preserves_every_shipment_field(self) -> None:
        """Field-driven, so a new `Shipment` field cannot reintroduce the bug."""
        import dataclasses

        shipment = z.Shipment(
            self.FROM, self.CA, (self._parcel("15"), self._parcel("15")),
            duties_paid_by=z.DutiesPaidBy.SENDER,
            eei_exemption="AES_ITN",
            reference="order-1",
        )
        leg = ShippoAdapter("shippo_test_x").single_parcel_shipment(
            shipment, shipment.parcels[0]
        )
        for field in dataclasses.fields(z.Shipment):
            if field.name == "parcels":
                continue
            assert getattr(leg, field.name) == getattr(shipment, field.name), (
                f"fan-out dropped {field.name!r}"
            )
        assert len(leg.parcels) == 1

    def test_ddp_survives_fan_out_on_the_wire(self) -> None:
        """A two-parcel DDP shipment reached Shippo with `incoterm` unset."""
        adapter = ShippoAdapter("shippo_test_x")
        transport = RecordingTransport()
        adapter.transport = transport
        client = _Client(adapter)
        shipment = z.Shipment(
            self.FROM, self.CA, (self._parcel("15"), self._parcel("15")),
            duties_paid_by=z.DutiesPaidBy.SENDER,
        )
        with contextlib.suppress(z.ShipzilError):
            client.get_rates(shipment)
        bodies = transport.bodies
        assert len(bodies) == 2, "expected one request per parcel"
        for body in bodies:
            assert isinstance(body, dict)
            declaration = body.get("customs_declaration")
            assert declaration is not None, "fan-out leg sent no customs declaration"
            assert declaration["incoterm"] == "DDP", "DDP was downgraded by fan-out"

    def test_explicit_eei_override_survives_fan_out(self) -> None:
        """Losing the override made each leg send no declaration at all.

        `customs_gap` runs against the original shipment, which still had the
        override, so rating passed while the legs on the wire carried nothing.
        """
        adapter = ShippoAdapter("shippo_test_x")
        transport = RecordingTransport()
        adapter.transport = transport
        client = _Client(adapter)
        shipment = z.Shipment(
            self.FROM, self.CA, (self._parcel("4000"), self._parcel("4000")),
            eei_exemption="AES_ITN",
        )
        with contextlib.suppress(z.ShipzilError):
            client.get_rates(shipment)
        bodies = transport.bodies
        assert len(bodies) == 2
        for body in bodies:
            assert isinstance(body, dict)
            declaration = body.get("customs_declaration")
            assert declaration is not None, "above-threshold leg sent no declaration"
            assert declaration["eel_pfc"] == "AES_ITN"

    def test_single_parcel_shipment_is_not_hand_written(self) -> None:
        """The bug was a hand-copied constructor; `replace` cannot drift."""
        import inspect

        source = inspect.getsource(ShippoAdapter.single_parcel_shipment)
        assert "replace(" in source
        assert "from_address=shipment.from_address" not in source


class TestServiceKeyAddressing:
    """`{provider}-{carrier}-{service}` — the gateway addressing scheme.

    Provider-namespaced on purpose. Two providers offering what looks like the same
    service get two addresses, because asserting they substitute for one another is
    an equivalence claim with a much higher correctness bar, and a wrong one
    silently ships a different service than the caller asked for.
    """

    def test_carrier_aliases_from_observed_provider_strings(self) -> None:
        """Every input here appeared in recorded traffic under `.probe/`."""
        from shipzil.services import normalize_carrier

        assert normalize_carrier("USPS") == "usps"
        assert normalize_carrier("UPS") == "ups"
        # ShipStation v1 sells USPS through Stamps.com, so an unnormalised
        # carrier filter on "usps" would miss every USPS rate.
        assert normalize_carrier("stamps_com") == "usps"
        # Registered-trademark glyphs must not reach an identifier.
        assert normalize_carrier("UPS® Ground") == "ups"
        # Unknown carriers are slugified, not rejected — refusing one would break
        # the gateway the moment a provider adds a carrier.
        assert normalize_carrier("Aramex") == "aramex"

    def test_trademark_glyphs_are_stripped_including_the_awkward_one(self) -> None:
        """`™` is the one that needs explicit handling.

        `®` and `©` are dropped for free by the ASCII fold, because NFKD leaves
        them non-ASCII. `™` decomposes to the *letters* `TM`, so it survives the
        fold and would silently become part of the identifier —
        "FedEx 2Day™" addressing as `fedex_2daytm`. Only this case proves the explicit
        replacement is load-bearing.
        """
        from shipzil.services import ServiceKey as _S

        for glyph, expected in (("®", "fedex_2day"), ("™", "fedex_2day"), ("©", "fedex_2day")):
            sid = _S.build(provider="easyship", carrier="", service=f"FedEx 2Day{glyph}")
            assert sid is not None
            assert sid.service == expected, f"{glyph} leaked into the identifier"
            assert sid.slug == "easyship-fedex-fedex_2day"

    def test_carrier_recovered_when_embedded_in_the_service_name(self) -> None:
        """v1 and Easyship give no usable carrier field on the rating path."""
        from shipzil.services import carrier_from_service

        assert carrier_from_service("USPS Ground Advantage - Package") == "usps"
        assert carrier_from_service("FedEx 2Day®") == "fedex"
        # No guessing from the first word when no known carrier is present.
        assert carrier_from_service("GroundAdvantage") == ""
        assert carrier_from_service("Priority") == ""

    def test_service_component_keeps_the_provider_machine_key(self) -> None:
        """ShipStation v2's machine key includes the carrier prefix."""
        sid = z.ServiceKey.build(
            provider="shipstation_v2", carrier="usps", service="USPS Ground Advantage"
        )
        assert sid is not None
        assert sid.slug == "shipstation_v2-usps-usps_ground_advantage"

    def test_packaging_prevents_a_slug_collision(self) -> None:
        """v1 returns two differently-priced rates sharing one serviceCode.

        "USPS Ground Advantage - Package" and "- Thick Envelope" are distinct
        products. Without packaging in the key they address the same slug.
        """
        a = z.ServiceKey.build(
            provider="shipstation_v1", carrier="stamps_com",
            service="USPS Ground Advantage", packaging="Package",
        )
        b = z.ServiceKey.build(
            provider="shipstation_v1", carrier="stamps_com",
            service="USPS Ground Advantage", packaging="Thick Envelope",
        )
        assert a is not None and b is not None
        assert a.slug != b.slug
        assert a.slug == "shipstation_v1-usps-usps_ground_advantage-package"
        assert b.slug == "shipstation_v1-usps-usps_ground_advantage-thick_envelope"
        # Same underlying service, so the unqualified form agrees.
        assert a.unqualified == b.unqualified == "usps-usps_ground_advantage"

    def test_v1_adapter_splits_packaging_out_of_the_service_name(self) -> None:
        """The collision test above builds ServiceKeys by hand; this drives v1.

        v1 encodes packaging into `serviceName` and reuses one `serviceCode`, so
        the split has to happen in the adapter. Without it these two rates — at
        two different prices — address the same slug.
        """
        adapter = ShipStationV1Adapter("k", "s")
        payloads = [
            {"serviceName": "USPS Ground Advantage - Package",
             "serviceCode": "usps_ground_advantage", "shipmentCost": "8.20"},
            {"serviceName": "USPS Ground Advantage - Thick Envelope",
             "serviceCode": "usps_ground_advantage", "shipmentCost": "6.15"},
        ]
        slugs = []
        for payload in payloads:
            rate = adapter._parse_rate(payload, "stamps_com")
            assert rate.service_key is not None
            slugs.append(rate.service_key.slug)

        assert slugs[0] == "shipstation_v1-usps-usps_ground_advantage-package"
        assert slugs[1] == "shipstation_v1-usps-usps_ground_advantage-thick_envelope"
        assert len(set(slugs)) == 2, "packaging was not split; the slugs collided"
        # One serviceCode, so both reduce to the same unqualified service.
        assert len({s.rsplit("-", 1)[0] for s in slugs}) == 1

    def test_unqualified_is_not_an_equivalence_claim(self) -> None:
        """Grouping candidates is not the same as declaring them substitutable."""
        ep = z.ServiceKey.build(
            provider="shipstation_v2", carrier="USPS", service="usps_ground_advantage"
        )
        sp = z.ServiceKey.build(
            provider="shippo", carrier="USPS", service="usps_ground_advantage"
        )
        assert ep is not None and sp is not None
        # Different addresses, deliberately, despite the same provider service key.
        assert ep.slug != sp.slug
        # The unqualified form is only a grouping hint.
        assert ep.unqualified == sp.unqualified == "usps-usps_ground_advantage"

    def test_no_service_means_no_address(self) -> None:
        """A half-formed address looks usable and is not stable."""
        assert z.ServiceKey.build(provider="shippo", carrier="USPS", service="") is None
        assert z.ServiceKey.build(provider="shippo", carrier="", service="  ") is None

    def test_slug_is_a_rendering_not_the_storage(self) -> None:
        sid = z.ServiceKey(
            provider="shippo", carrier="usps", service="usps_ground_advantage"
        )
        assert str(sid) == sid.slug == "shippo-usps-usps_ground_advantage"
        # The parts stay addressable, so a later layer can use carrier+service
        # without parsing the slug back apart.
        assert (sid.provider, sid.carrier, sid.service) == (
            "shippo", "usps", "usps_ground_advantage",
        )

    def test_every_adapter_populates_service_key_from_real_payloads(self) -> None:
        """Parsed against captured responses, not constructed by hand."""
        import json
        import pathlib as _p

        probe = _p.Path(".probe")
        if not probe.exists():
            pytest.skip("no recorded payloads available")

        def rates(name: str) -> list[dict[str, object]]:
            path = probe / name
            if not path.exists():
                return []
            data = json.loads(path.read_text())
            if isinstance(data, dict) and "rates" in data:
                return data["rates"]
            if isinstance(data, dict) and "rate_response" in data:
                return data["rate_response"].get("rates", [])
            return data if isinstance(data, list) else []

        checks = [
            (
                "shippo_single.json",
                lambda d: ShippoAdapter("x")._parse_rate(d, shipment_id="s"),
                "shippo-",
            ),
            (
                "ss2_rates_single.json",
                lambda d: ShipStationV2Adapter("k")._parse_rate(
                    d, strategy=z.Strategy.NATIVE, parcel_count=1
                ),
                "shipstation_v2-",
            ),
            (
                "ss1_rates_single.json",
                lambda d: ShipStationV1Adapter("k", "s")._parse_rate(d, "stamps_com"),
                "shipstation_v1-",
            ),
        ]
        for name, parse, prefix in checks:
            payloads = rates(name)
            if not payloads:
                continue
            for payload in payloads[:5]:
                rate = parse(payload)
                assert rate.service_key is not None, f"{name}: no service_key"
                assert rate.service_key.slug.startswith(prefix)
                # Carrier must be normalised, never a reseller channel.
                assert rate.service_key.carrier != "stamps_com"
                assert "®" not in rate.service_key.slug
