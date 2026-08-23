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

from decimal import Decimal
from typing import Any

from ..errors import LabelPurchaseError, ValidationError
from ..http import request
from ..models import (
    Address,
    Exclusion,
    ExclusionCode,
    Item,
    Label,
    Parcel,
    Quote,
    Rate,
    Shipment,
    Strategy,
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
    capabilities = EasyshipCapabilities()

    def __init__(
        self,
        api_key: str,
        *,
        sandbox: bool | None = None,
        default_category: str | None = None,
        timeout: float = 90.0,
    ):
        if not api_key:
            raise ValueError("easyship api key is required")
        self.api_key = api_key
        # `sand_` prefixes sandbox keys; honour an explicit override.
        self.sandbox = api_key.startswith("sand_") if sandbox is None else sandbox
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

        payload = {
            "origin_address": _address(shipment.from_address),
            "destination_address": _address(shipment.to_address),
            "parcels": [self._parcel(parcel)],
            "shipping_settings": {"units": {"weight": "kg", "dimensions": "cm"}},
            "incoterms": "DDU",
        }
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
            raw=data,
        )

    # ── buying ──────────────────────────────────────────────────────

    def buy(self, shipment: Shipment, rate: Rate) -> Label:
        """Create the shipment, then request its label synchronously.

        Easyship separates the two: `POST /shipments` then
        `POST /shipments/{id}/labels`, which the docs describe as retrieving the
        label synchronously. The batch endpoint is the asynchronous one and is
        deliberately unused.

        Easyship is
        structurally protected instead: a second label request for the same
        shipment id is refused with "labels already requested".
        """
        parcel = shipment.parcels[0]
        service_id = (rate.service_code or "").strip()
        payload: dict[str, Any] = {
            "origin_address": _address(shipment.from_address),
            "destination_address": _address(shipment.to_address),
            "parcels": [self._parcel(parcel)],
            "shipping_settings": {"units": {"weight": "kg", "dimensions": "cm"}},
            "incoterms": "DDU",
        }
        if service_id:
            payload["courier_selection"] = {"selected_courier_id": service_id}

        _status, created = request(
            "POST", self._url("/shipments"), headers=self._headers, json=payload,
            timeout=self.timeout, provider=self.name, retries=0,
        )
        record = created.get("shipment") or created
        shipment_id = str(record.get("easyship_shipment_id") or record.get("id") or "")
        if not shipment_id:
            raise LabelPurchaseError(
                "easyship created no shipment id", provider=self.name
            )

        _status, labelled = request(
            "POST", self._url(f"/shipments/{shipment_id}/labels"),
            headers=self._headers, json={}, timeout=self.timeout,
            provider=self.name, retries=0,
        )
        return self._parse_label(labelled, fallback_id=shipment_id, rate=rate)

    def _parse_label(
        self, body: dict[str, Any], *, fallback_id: str, rate: Rate
    ) -> Label:
        record = body.get("shipment") or body
        tracking = record.get("tracking_number") or ""
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
        if parcel.items:
            out["items"] = [self._item(i) for i in parcel.items]
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

    def _item(self, item: Item) -> dict[str, Any]:
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
