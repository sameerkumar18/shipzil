"""Easyship.

The odd one out, in ways that shaped the whole data model:

* **Sandbox is a different host.** `public-api-sandbox.easyship.com`. A sandbox
  key against production returns `401 invalid_token`, while `GET /account` on
  production returns a `500` — so the first error you see misdirects you.
* **Cloudflare rejects default HTTP client user-agents** with
  `403 browser_signature_banned`, which reads like an auth failure and isn't.
  `shipzil.http` always sends a real User-Agent.
* **Every item needs `category` or `hs_code`, even domestically.** No other
  provider requires customs classification for a US-to-US parcel.
* **It can pack for you.** Given items carrying their own dimensions, Easyship
  derives the box, and it is the only surface where an item-centric `Parcel`
  works. It still needs dimensions *somewhere* though: an item-only parcel whose
  items have no dimensions is rejected with
  `parcels[0].items[0].dimensions can't be blank`.
* **Multi-parcel is rejected**: three parcels returns `422 "No shipping
  solutions available based on the information provided"`, so shipzil fans out.
* Rates are international-first: import duty, tax, DDP handling and fuel
  surcharge are separate fields, and courier identity is nested under
  `courier_service` rather than a top-level name.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from ..errors import LabelPurchaseError, ValidationError
from ..http import request
from ..models import (
    Address,
    DangerousGoods,
    Exclusion,
    ExclusionCode,
    Item,
    Label,
    LithiumBatteryPacking,
    Parcel,
    Quote,
    Rate,
    Shipment,
    Strategy,
    TrackingLeg,
)
from ..normalize import code_from_text
from .base import Adapter, Capabilities

PRODUCTION_BASE = "https://public-api.easyship.com"
SANDBOX_BASE = "https://public-api-sandbox.easyship.com"
API_VERSION = "2024-09"


class EasyshipCapabilities(Capabilities):
    # Verified: 3 parcels -> HTTP 422 "No shipping solutions available".
    native_multi_parcel = False
    order_resource = False
    # Still needs dimensions, but accepts them per item instead of per box and
    # will compute the box itself. Item-only parcels whose items carry no
    # dimensions are rejected: "parcels[0].items[0].dimensions can't be blank".
    returns_currency = True
    returns_delivery_estimate = True


class EasyshipAdapter(Adapter):
    name = "easyship"
    # "Please note that this value refers to the unit rather than the total"
    # — ParcelItemCreate.declared_customs_value. Already what this adapter sent.
    customs_value_basis = "per_unit"
    # Per item: contains_battery_pi966 / pi967 / contains_liquids. Easyship is
    # the only provider that distinguishes the two IATA packing instructions.
    hazmat_fields = frozenset({"lithium_batteries", "contains_liquids"})
    capabilities = EasyshipCapabilities()

    def __init__(
        self,
        api_key: str,
        *,
        sandbox: bool | None = None,
        label_timeout: float = 60.0,
        poll_interval: float = 2.0,
        default_category: str | None = None,
        timeout: float = 90.0,
    ):
        if not api_key:
            raise ValueError("easyship api key is required")
        self.api_key = api_key
        # `sand_` prefixes sandbox keys; honour an explicit override.
        self.sandbox = api_key.startswith("sand_") if sandbox is None else sandbox
        self.label_timeout = label_timeout
        self.poll_interval = poll_interval
        self.base = SANDBOX_BASE if self.sandbox else PRODUCTION_BASE
        self.timeout = timeout
        # Applied only where an item supplies neither category nor hs_code. Left
        # unset by default so a customs declaration is never invented silently.
        self.default_category = default_category
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def _url(self, path: str) -> str:
        return f"{self.base}/{API_VERSION}{path}"

    def is_test_mode(self) -> bool | None:
        """Determinable: sandbox is a separate host, selected at construction."""
        return self.sandbox

    def item_categories(self) -> list[str]:
        """Valid category slugs for this account."""
        _status, body = request(
            "GET", self._url("/item_categories"), headers=self._headers,
            timeout=self.timeout, provider=self.name,
        )
        return [c["slug"] for c in (body.get("item_categories") or []) if c.get("slug")]

    # ── rating ──────────────────────────────────────────────────────

    def rate_single(self, shipment: Shipment) -> Quote:
        parcel = shipment.parcels[0]
        gap = self._input_gap(parcel)
        if gap is not None:
            return Quote(excluded=(gap,), via=f"{self.name}:rates")

        payload: dict[str, Any] = {
            "origin_address": _address(shipment.from_address),
            "destination_address": _address(shipment.to_address),
            "parcels": [self._parcel(parcel)],
            "shipping_settings": {"units": {"weight": "kg", "dimensions": "cm"}},
        }
        incoterms = self.render_incoterm(shipment)
        if incoterms:
            payload["incoterms"] = incoterms
        residential = shipment.to_address.residential
        if residential is not None:
            # Destination only; Easyship has no origin residential field.
            payload["set_as_residential"] = residential
        try:
            _status, body = request(
                "POST", self._url("/rates"), headers=self._headers, json=payload,
                timeout=self.timeout, provider=self.name,
                idempotent=True,
            )
        except ValidationError as exc:
            # Easyship states refusals as 422 with a real message; keep it.
            text = str(exc)
            return Quote(
                excluded=(
                    Exclusion(code=code_from_text(text), message=text, source="shipzil"),
                ),
                via=f"{self.name}:rates",
            )

        rates = tuple(self._parse_rate(r) for r in (body.get("rates") or []))
        excluded: tuple[Exclusion, ...] = ()
        if not rates:
            excluded = (
                Exclusion(
                    code=ExclusionCode.SERVICE_UNAVAILABLE,
                    message="easyship returned no rates",
                    source="shipzil",
                ),
            )
        return Quote(
            rates=rates, excluded=excluded, via=f"{self.name}:rates", strategy=Strategy.NATIVE
        )

    #: Cost components Easyship separates out. Kept in provider spelling so a
    #: caller can reconcile against an invoice line for line.
    _SURCHARGE_KEYS = (
        "fuel_surcharge",
        # Reported even when the destination is not classified residential, so
        # it doubles as an exposure signal for an unclassified address.
        # `residential_discounted_fee` is the SAME surcharge at a second price
        # point, not an additional charge, so including both would double-count.
        "residential_full_fee",
        "remote_area_surcharge",
        "oversized_surcharge",
        "additional_services_surcharge",
        "insurance_fee",
        "warehouse_handling_fee",
        "minimum_pickup_fee",
        "ddp_handling_fee",
        "import_duty_charge",
        "import_tax_charge",
        "sales_tax",
        "provincial_sales_tax",
    )

    @classmethod
    def _surcharges(cls, data: dict[str, Any]) -> tuple[tuple[str, Decimal], ...]:
        """Non-zero cost components.

        Easyship returns 25 of these and shipzil used to keep only the total, so
        a 13% gap between base carriage and total was invisible. Worth noting
        that `residential_full_fee` is reported even when it is not applied, so a
        caller can see the exposure of an unclassified address.
        """
        out = []
        for key in cls._SURCHARGE_KEYS:
            raw = data.get(key)
            if raw in (None, "", 0, 0.0):
                continue
            try:
                value = Decimal(str(raw))
            except (ArithmeticError, ValueError):
                continue
            if value:
                out.append((key, value))
        return tuple(out)

    def _parse_rate(self, data: dict[str, Any]) -> Rate:
        service = data.get("courier_service") or {}
        # Carrier identity is `umbrella_name` ("FedEx"); `name` is the service
        # ("FedEx 2Day®"). There is no nested `courier` object and no
        # `courier_name` field — guessing those yields a silently empty carrier
        # on every rate, which is how this was originally wrong.
        carrier = service.get("umbrella_name") or ""
        currency = data.get("currency")
        # min/max delivery time bracket the estimate; the slower bound is the
        # honest one to quote.
        days = data.get("max_delivery_time") or data.get("min_delivery_time")
        return Rate(
            carrier=str(carrier),
            service=str(service.get("name") or data.get("full_description") or ""),
            amount=Decimal(str(data.get("total_charge") or 0)),
            currency=currency.upper() if isinstance(currency, str) else None,
            delivery_days=int(days) if days is not None else None,
            guaranteed=None,
            provider=self.name,
            service_code=str(service.get("id") or "") or None,
            strategy=Strategy.NATIVE,
            parcel_count=1,
            base_amount=(
                Decimal(str(data["shipment_charge"]))
                if data.get("shipment_charge") is not None
                else None
            ),
            surcharges=self._surcharges(data),
            raw=data,
        )

    # ── buying ──────────────────────────────────────────────────────

    def buy(self, shipment: Shipment, rate: Rate) -> Label:
        """One call: create the shipment and buy its label synchronously.

        This took three wrong attempts, all from trusting prose over the schema:

        1. `POST /shipments/{id}/labels` — **does not exist.** The docstring also
           claimed it was synchronous and that the batch endpoint was "the
           asynchronous one and is deliberately unused". Invented both ways.
        2. `courier_selection: {selected_courier_id}` — rejected,
           "ShipmentCreate does not define properties: courier_selection".
        3. Top-level `courier_service_id` — also rejected, even though the prose
           documentation says to "assign a courier to the shipment using
           `courier_service_id`". It is real but **nested** under
           `courier_settings`.

        The actual `ShipmentCreate` schema, read from the OpenAPI definition:

        * `courier_settings.courier_service_id` selects the service.
        * `shipping_settings.buy_label` and `shipping_settings.buy_label_synchronous`
          buy the label during creation and wait for it. So there is no need for
          `POST /batch_labels` and its `not_created -> pending -> generated`
          polling at all, which is what an earlier version of this method built.
        * Only `parcels` is required.

        A second label request for a shipment already labelled is refused with
        "labels already requested", which is Easyship's structural protection
        against a duplicate purchase.
        """
        parcel = shipment.parcels[0]
        # Purchase validates addresses more strictly than rating does: rating
        # succeeds without a company name, creation rejects a blank one with
        # "origin_address.company_name can't be blank". shipzil will not
        # substitute the contact name, because a company name is a declaration
        # about who is shipping, so it says what is missing instead.
        missing = [
            label
            for label, addr in (("from_address", shipment.from_address),
                                ("to_address", shipment.to_address))
            if not (addr.company or "").strip()
        ]
        if missing:
            raise LabelPurchaseError(
                "easyship requires a company name on both addresses when buying a label, "
                f"and {' and '.join(missing)} has none. Rating does not need it, so this "
                "only surfaces at purchase. Set Address(company=...).",
                provider=self.name,
            )
        service_id = (rate.service_code or "").strip()
        payload: dict[str, Any] = {
            "origin_address": _address(shipment.from_address),
            "destination_address": _address(shipment.to_address),
            "parcels": [self._parcel(parcel)],
            "shipping_settings": {
                "units": {"weight": "kg", "dimensions": "cm"},
                "buy_label": True,
                "buy_label_synchronous": True,
            },
        }
        incoterms = self.render_incoterm(shipment)
        if incoterms:
            payload["incoterms"] = incoterms
        residential = shipment.to_address.residential
        if residential is not None:
            payload["set_as_residential"] = residential
        if service_id:
            payload["courier_settings"] = {"courier_service_id": service_id}

        _status, created = request(
            "POST", self._url("/shipments"), headers=self._headers, json=payload,
            timeout=self.timeout, provider=self.name, retries=0,
        )
        record = created.get("shipment") or created
        shipment_id = str(record.get("easyship_shipment_id") or record.get("id") or "")
        if not shipment_id:
            raise LabelPurchaseError("easyship created no shipment id", provider=self.name)

        state = str(record.get("label_state") or "")
        if state == "generated":
            return self._parse_label(created, fallback_id=shipment_id, rate=rate)
        if state == "failed":
            raise LabelPurchaseError(
                f"easyship label generation failed for {shipment_id}", provider=self.name
            )
        # buy_label_synchronous should have settled it. If the account is
        # configured to defer anyway, wait rather than return a labelless label.
        return self._await_label(shipment_id, rate)

    def _await_label(self, shipment_id: str, rate: Rate) -> Label:
        """Bounded poll, for when the synchronous flag did not settle the state."""
        deadline = time.monotonic() + self.label_timeout
        state = ""
        while time.monotonic() < deadline:
            time.sleep(self.poll_interval)
            _status, body = request(
                "GET", self._url(f"/shipments/{shipment_id}"),
                headers=self._headers, timeout=self.timeout,
                provider=self.name, idempotent=True,
            )
            record = body.get("shipment") or body
            state = str(record.get("label_state") or "")
            if state == "generated":
                return self._parse_label(body, fallback_id=shipment_id, rate=rate)
            if state == "failed":
                raise LabelPurchaseError(
                    f"easyship label generation failed for {shipment_id}",
                    provider=self.name,
                )
        raise LabelPurchaseError(
            f"easyship label for {shipment_id} was still {state or 'unknown'} after "
            f"{self.label_timeout:.0f}s. The shipment is confirmed and may still "
            f"complete; check label_state rather than buying again.",
            provider=self.name,
        )

    def _parse_label(
        self, body: dict[str, Any], *, fallback_id: str, rate: Rate
    ) -> Label:
        record = body.get("shipment") or body
        # There is no flat tracking_number. Easyship returns a `trackings` array,
        # one entry per leg, each with handler / leg_number / tracking_number and
        # a nullable local_tracking_number. Reading the non-existent flat field
        # returned an empty tracking number on every successful purchase.
        tracking = ""
        legs = record.get("trackings") or []
        for leg in sorted(
            (t for t in legs if isinstance(t, dict)),
            key=lambda t: t.get("leg_number") or 0,
        ):
            tracking = str(
                leg.get("tracking_number")
                or leg.get("local_tracking_number")
                or leg.get("alternate_tracking_number")
                or ""
            )
            if tracking:
                break
        if not tracking:
            tracking = str(record.get("tracking_number") or "")
        legs = tuple(
            TrackingLeg(
                tracking_number=str(t.get("tracking_number") or ""),
                leg_number=int(t.get("leg_number") or 1),
                handler=t.get("handler"),
                local_tracking_number=t.get("local_tracking_number"),
                alternate_tracking_number=t.get("alternate_tracking_number"),
            )
            for t in sorted(
                (x for x in (record.get("trackings") or []) if isinstance(x, dict)),
                key=lambda x: x.get("leg_number") or 0,
            )
            if t.get("tracking_number")
        )
        docs = record.get("shipping_documents") or []
        url = ""
        for doc in docs:
            if isinstance(doc, dict) and doc.get("category") in {"label", None}:
                url = doc.get("url") or ""
                if url:
                    break
        if not url:
            url = (record.get("label_url") or "")
        if not tracking and not url:
            raise LabelPurchaseError(
                f"easyship label not ready (label_state={record.get('label_state')!r})",
                provider=self.name,
            )
        return Label(
            tracking_number=str(tracking),
            label_url=str(url),
            carrier=rate.carrier,
            service=rate.service,
            amount=rate.amount,
            currency=rate.currency,
            provider=self.name,
            is_test=self.is_test_mode(),
            shipment_id=str(record.get("easyship_shipment_id") or fallback_id),
            tracking_legs=legs,
            raw=body,
        )

    def void(self, label: Label) -> bool:
        if not label.shipment_id:
            return False
        _status, body = request(
            "PATCH", self._url(f"/shipments/{label.shipment_id}/cancel"),
            headers=self._headers, json={}, timeout=self.timeout,
            provider=self.name, retries=0,
        )
        record = (body or {}).get("shipment") or body or {}
        return str(record.get("label_state") or "").lower() in {"voided", "cancelled", "canceled"}

    # ── translation ─────────────────────────────────────────────────

    def _input_gap(self, parcel: Parcel) -> Exclusion | None:
        """Everything Easyship demands that we refuse to fabricate.

        Two distinct requirements, both checked before any network call:

        * **A customs classification on every item.** There is no generic
          category — all 20 slugs are specific (`fashion`, `toys`, `documents`,
          …) — so there is nothing honest to default to. A category is a customs
          declaration, and shipzil will not make one on the caller's behalf.
        * **Dimensions somewhere.** Either a box on the parcel, or dimensions on
          every item, or a stored `sku` per item for Easyship to look up.
        """
        needs_classification = not self.default_category and (
            any(not i.category and not i.hs_code for i in parcel.items)
            # A parcel with no items still gets a placeholder item, which needs
            # a category just as much.
            or not parcel.items
        )
        if needs_classification:
            if parcel.items:
                count = sum(1 for i in parcel.items if not i.category and not i.hs_code)
                detail = f"{count} item(s) have neither"
            else:
                detail = (
                    "this parcel has no items, so shipzil would have to declare one, "
                    "and there is no generic category to declare it as"
                )
            return Exclusion(
                code=ExclusionCode.ITEM_CLASSIFICATION_REQUIRED,
                message=(
                    f"easyship requires category or hs_code on every item, even domestically; "
                    f"{detail}. Set Item(category=...) or "
                    "EasyshipAdapter(default_category='fashion') — see item_categories()"
                ),
                source="shipzil",
            )

        has_box = parcel.dimensions is not None
        items_sized = bool(parcel.items) and all(
            i.dimensions is not None or i.sku for i in parcel.items
        )
        if not has_box and not items_sized:
            return Exclusion(
                code=ExclusionCode.DIMENSIONS_REQUIRED,
                message=(
                    "easyship needs dimensions: give the Parcel dimensions, or give every "
                    "Item dimensions (it will compute the box), or a sku it can look up"
                ),
                source="shipzil",
            )
        return None

    def _parcel(self, parcel: Parcel) -> dict[str, Any]:
        out: dict[str, Any] = {}
        weight = parcel.weight or parcel.derived_weight
        if weight is not None:
            out["total_actual_weight"] = float(weight.to("kg"))
        if parcel.dimensions is not None:
            length, width, height = parcel.dimensions.to("cm")
            out["box"] = {
                "length": float(length),
                "width": float(width),
                "height": float(height),
            }
        if parcel.packaging is not None:
            # A slug carries the dimensions, so it replaces `box`.
            out["box"] = {"slug": parcel.packaging.code}
        if parcel.items:
            out["items"] = [self._item(i, parcel.dangerous_goods) for i in parcel.items]
        else:
            # Easyship requires at least one item. Describe the parcel itself
            # rather than inventing contents it does not have.
            out["items"] = [
                self._item(
                    Item(
                        description="Merchandise",
                        quantity=1,
                        weight=weight,
                        value=Decimal("10.00"),
                        category=self.default_category,
                    )
                )
            ]
        return out

    @staticmethod
    def _hazmat_item_flags(dg: DangerousGoods | None) -> dict[str, Any]:
        """Easyship carries hazmat per item, and only these three flags.

        `contains_battery_pi966` / `pi967` are the IATA packing instructions and
        are not interchangeable. Anything else in a DangerousGoods — UN number,
        hazard class, packing group, dry ice — has nowhere to go here, which the
        adapter reports rather than dropping silently.
        """
        if dg is None or not dg.contains:
            return {}
        out: dict[str, Any] = {}
        if dg.lithium_batteries is LithiumBatteryPacking.PACKED_WITH_EQUIPMENT:
            out["contains_battery_pi966"] = True
        elif dg.lithium_batteries is LithiumBatteryPacking.CONTAINED_IN_EQUIPMENT:
            out["contains_battery_pi967"] = True
        if dg.contains_liquids:
            out["contains_liquids"] = True
        return out

    def _item(self, item: Item, dg: DangerousGoods | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "description": item.description,
            "quantity": item.quantity,
            "declared_currency": item.currency,
            "declared_customs_value": float(item.value if item.value is not None else 10),
        }
        if item.weight is not None:
            out["actual_weight"] = float(item.weight.to("kg"))
        if item.dimensions is not None:
            length, width, height = item.dimensions.to("cm")
            out["dimensions"] = {
                "length": float(length),
                "width": float(width),
                "height": float(height),
            }
        if item.sku:
            out["sku"] = item.sku
        if item.hs_code:
            out["hs_code"] = item.hs_code
        category = item.category or self.default_category
        if category:
            out["category"] = category
        if item.origin_country:
            out["origin_country_alpha2"] = item.origin_country
        out.update(self._hazmat_item_flags(dg))
        return out


def _address(addr: Address) -> dict[str, Any]:
    out: dict[str, Any] = {
        "line_1": addr.street1,
        "city": addr.city,
        "postal_code": addr.postal_code,
        "country_alpha2": addr.country,
    }
    if addr.state:
        out["state"] = addr.state
    if addr.street2:
        out["line_2"] = addr.street2
    if addr.name:
        out["contact_name"] = addr.name
    if addr.company:
        out["company_name"] = addr.company
    if addr.phone:
        out["contact_phone"] = addr.phone
    if addr.email:
        out["contact_email"] = addr.email
    return out
