"""ShipStation API v2 (ShipEngine-derived).

The best-behaved surface of the five. `packages[]` rates several parcels in one
call, and when a carrier cannot manage it the response says so per carrier with a
machine-readable code:

    {"error_code": "multipackage_not_supported",
     "message": "carrier 30718 does not support multipackage...",
     "carrier_code": "usps", "carrier_name": "USPS"}

Partial success with attributed reasons. That vocabulary is what
`shipzil.normalize` maps every other provider onto.

Credentials for this surface are production-only in practice, so this adapter is
exercised live for rating and never for purchasing.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..errors import LabelPurchaseError
from ..http import request
from ..models import (
    Address,
    DutiesPaidBy,
    Exclusion,
    ExclusionCode,
    Label,
    Parcel,
    Quote,
    Rate,
    Shipment,
    Strategy,
)
from ..normalize import code_from_provider_code, code_from_text
from .base import Adapter, Capabilities

BASE = "https://api.shipstation.com/v2"


class ShipStationV2Capabilities(Capabilities):
    native_multi_parcel = True  # verified: 3 packages -> 7 real rates
    order_resource = False
    returns_currency = True
    returns_delivery_estimate = True


class ShipStationV2Adapter(Adapter):
    name = "shipstation_v2"
    # advanced_options.{dangerous_goods, dangerous_goods_contact, dry_ice,
    # dry_ice_weight, contains_alcohol}. The full IATA declaration exists on
    # packages[].products[].dangerous_goods[] (UN number, class, packing group,
    # transport mode, radioactive) but needs an itemised products array shipzil
    # does not build yet, so regulated_detail is deliberately NOT claimed.
    hazmat_fields = frozenset({"dry_ice", "contains_alcohol"})
    capabilities = ShipStationV2Capabilities()

    def __init__(
        self,
        api_key: str,
        *,
        carrier_ids: list[str] | None = None,
        timeout: float = 60.0,
    ):
        if not api_key:
            raise ValueError("shipstation v2 api key is required")
        self._headers = {"API-Key": api_key}
        self.timeout = timeout
        self._carrier_ids = carrier_ids

    # ── carriers ────────────────────────────────────────────────────

    def carrier_ids(self) -> list[str]:
        """Carrier ids on the account, fetched once.

        v2 rates against an explicit carrier list; without it the account default
        applies, which differs between tenants and makes results irreproducible.
        """
        if self._carrier_ids is None:
            _status, body = request(
                "GET", f"{BASE}/carriers", headers=self._headers,
                timeout=self.timeout, provider=self.name, idempotent=True,
            )
            self._carrier_ids = [
                c["carrier_id"] for c in (body.get("carriers") or []) if c.get("carrier_id")
            ]
        return self._carrier_ids

    # ── rating ──────────────────────────────────────────────────────

    def is_test_mode(self) -> bool | None:
        """Not determinable: v2 API keys carry no test/live marker."""
        return None

    def rate_single(self, shipment: Shipment) -> Quote:
        return self._rate(shipment)

    def rate_native_multi(self, shipment: Shipment) -> Quote:
        """Same endpoint — v2 simply takes more packages."""
        return self._rate(shipment)

    def _rate(self, shipment: Shipment) -> Quote:
        gap = _dimension_gap(shipment.parcels)
        if gap is not None:
            return Quote(excluded=(gap,), via=f"{self.name}:rates")

        _status, body = request(
            "POST",
            f"{BASE}/rates",
            headers=self._headers,
            json={
                "rate_options": {"carrier_ids": self.carrier_ids()},
                "shipment": {
                    "ship_from": _address(shipment.from_address),
                    "ship_to": _address(shipment.to_address),
                    "packages": [
                        {
                            **_package(p),
                            **(
                                {"products": prods}
                                if (prods := self._products(p, shipment))
                                else {}
                            ),
                        }
                        for p in shipment.parcels
                    ],
                    **(
                        {"advanced_options": opts}
                        if (opts := self._advanced_options(shipment, shipment.parcels[0]))
                        else {}
                    ),
                    **({"ship_date": shipment.ship_date} if shipment.ship_date else {}),
                    **(
                        {"customs": customs}
                        if (customs := self._customs(shipment))
                        else {}
                    ),
                },
            },
            timeout=self.timeout,
            provider=self.name,
            idempotent=True,
        )

        response = body.get("rate_response") or {}
        parcel_count = len(shipment.parcels)
        strategy = Strategy.NATIVE

        rates = tuple(
            self._parse_rate(r, strategy=strategy, parcel_count=parcel_count)
            for r in (response.get("rates") or [])
        )
        excluded = tuple(self._parse_errors(response))

        if not rates and not excluded:
            excluded = (
                Exclusion(
                    code=ExclusionCode.SERVICE_UNAVAILABLE,
                    message="shipstation returned no rates and reported no errors",
                    source="shipzil",
                ),
            )

        messages = tuple(
            str(m) for r in (response.get("rates") or []) for m in (r.get("warning_messages") or [])
        )
        return Quote(
            rates=rates, excluded=excluded, via=f"{self.name}:rates",
            strategy=strategy, messages=messages,
        )

    def _parse_errors(self, response: dict[str, Any]) -> list[Exclusion]:
        """v2's structured errors, plus any `invalid_rates` it rejected."""
        out: list[Exclusion] = []
        for err in response.get("errors") or []:
            if not isinstance(err, dict):
                continue
            message = str(err.get("message") or "")
            code = code_from_provider_code(err.get("error_code")) or code_from_text(message)
            # The provider gave a code, so this is fact rather than inference.
            source = "provider" if err.get("error_code") else "shipzil"
            out.append(
                Exclusion(
                    code=code,
                    message=message or "shipstation rejected this shipment",
                    carrier=err.get("carrier_code") or err.get("carrier_name"),
                    source=source,  # type: ignore[arg-type]
                )
            )
        for bad in response.get("invalid_rates") or []:
            if not isinstance(bad, dict):
                continue
            notes = "; ".join(str(m) for m in (bad.get("error_messages") or []))
            out.append(
                Exclusion(
                    code=code_from_text(notes),
                    message=notes or "shipstation marked this rate invalid",
                    carrier=bad.get("carrier_code"),
                    service=bad.get("service_type"),
                    source="shipzil",
                )
            )
        return out

    def _customs(self, shipment: Shipment) -> dict[str, Any] | None:
        """ShipEngine's shipment-level customs block.

        `customs_items` is **deprecated** here — the spec says "Please provide
        this information under `products` inside `packages`" — so the line detail
        goes on each package via `_products`, and only the shipment-wide fields
        live in this object.
        """
        if not self.is_cross_border(shipment):
            return None
        if not self.customs_lines(shipment):
            return None
        out: dict[str, Any] = {
            "contents": "merchandise",
            "non_delivery": "return_to_sender",
        }
        # Lowercase three-letter incoterms, from the spec's enum:
        # exw fca cpt cip dpu dap ddp fas fob cfr cif ddu daf deq des.
        # "delivery_duty_paid" is rejected with "Unknown TermsOfTradeCode value".
        if shipment.duties_paid_by is DutiesPaidBy.SENDER:
            out["terms_of_trade_code"] = "ddp"
        elif shipment.duties_paid_by is DutiesPaidBy.RECIPIENT:
            out["terms_of_trade_code"] = "ddu"
        return out

    @staticmethod
    def _products(parcel: Parcel, shipment: Shipment) -> list[dict[str, Any]]:
        """Per-package customs lines, which is where ShipEngine wants them.

        This is the one provider that keeps the parcel association, so it walks
        the parcel's own items rather than the flattened shipment-wide list.
        """
        out: list[dict[str, Any]] = []
        for item in parcel.items:
            if item.line_value is None or item.line_weight is None:
                continue
            entry: dict[str, Any] = {
                "description": item.description,
                "quantity": item.quantity,
                # Line totals, matching every other provider's convention.
                "value": {
                    "currency": (item.currency or "USD").lower(),
                    "amount": float(item.line_value),
                },
                "weight": {"value": float(item.line_weight.to("oz")), "unit": "ounce"},
                "country_of_origin": (
                    item.origin_country or shipment.from_address.country or "US"
                ),
            }
            if item.hs_code:
                entry["harmonized_tariff_code"] = item.hs_code
            if item.sku:
                entry["sku"] = item.sku
            out.append(entry)
        return out

    @staticmethod
    def _advanced_options(shipment: Shipment, parcel: Parcel) -> dict[str, Any]:
        """ShipEngine keeps hazmat booleans on the shipment and the full
        IATA declaration on `packages[].products[].dangerous_goods[]`.

        shipzil sends the shipment-level flags plus the emergency contact, which
        several carriers require whenever hazmat is present. The per-product
        declaration needs an itemised `products` array that shipzil does not
        model yet, so a fully regulated shipment is reported as a fidelity gap
        rather than sent half-declared.
        """
        opts: dict[str, Any] = {}
        dg = parcel.dangerous_goods
        if dg is not None and dg.contains:
            opts["dangerous_goods"] = True
            if dg.emergency_contact_name or dg.emergency_contact_phone:
                opts["dangerous_goods_contact"] = {
                    "name": dg.emergency_contact_name or "",
                    "phone": dg.emergency_contact_phone or "",
                }
            if dg.contains_alcohol:
                opts["contains_alcohol"] = True
            if dg.dry_ice is not None and dg.dry_ice.contains:
                opts["dry_ice"] = True
                # ShipEngine accepts four units; kilograms is unambiguous.
                opts["dry_ice_weight"] = {
                    "value": float(dg.dry_ice.weight.to("kg")),
                    "unit": "kilogram",
                }
        if shipment.duties_paid_by is DutiesPaidBy.SENDER:
            opts["delivered_duty_paid"] = True
        return opts

    def _parse_rate(
        self, data: dict[str, Any], *, strategy: Strategy, parcel_count: int
    ) -> Rate:
        shipping = data.get("shipping_amount") or {}
        other = data.get("other_amount") or {}
        # Base cost is shipping plus mandatory other charges, mirroring how v1
        # splits shipmentCost/otherCost. Insurance and confirmation are opt-in
        # add-ons and stay out of the comparable figure.
        amount = Decimal(str(shipping.get("amount") or 0)) + Decimal(str(other.get("amount") or 0))
        currency = shipping.get("currency")
        return Rate(
            carrier=str(data.get("carrier_friendly_name") or data.get("carrier_code") or ""),
            service=str(data.get("service_type") or ""),
            amount=amount,
            currency=currency.upper() if isinstance(currency, str) else None,
            delivery_days=data.get("delivery_days"),
            guaranteed=data.get("guaranteed_service"),
            provider=self.name,
            service_code=data.get("service_code"),
            strategy=strategy,
            parcel_count=parcel_count,
            raw=data,
        )

    # ── buying ──────────────────────────────────────────────────────

    def buy(self, shipment: Shipment, rate: Rate) -> Label:
        """Purchase from a previously returned `rate_id`.

        Never exercised against live credentials in this repo's test suite: the
        only ShipStation keys available are production.

        ShipStation v2 documents exactly two headers (API-Key, Content-Type),
        so the key is always None here and the only protection is retries=0.
        """
        raw = rate.raw if isinstance(rate.raw, dict) else {}
        rate_id = raw.get("rate_id")
        if not rate_id:
            raise LabelPurchaseError(
                "this rate has no shipstation rate_id and cannot be bought",
                provider=self.name,
            )
        _status, body = request(
            "POST",
            f"{BASE}/labels/rates/{rate_id}",
            headers=self._headers,
            json={"validate_address": "no_validation"},
            timeout=self.timeout,
            provider=self.name,
            retries=0,
        )
        return self._parse_label(body)

    def void(self, label: Label) -> bool:
        if not label.shipment_id:
            return False
        _status, body = request(
            "PUT", f"{BASE}/labels/{label.shipment_id}/void",
            headers=self._headers, timeout=self.timeout, provider=self.name, retries=0,
        )
        return bool((body or {}).get("approved"))

    def _parse_label(self, body: dict[str, Any]) -> Label:
        cost = body.get("shipment_cost") or {}
        download = body.get("label_download") or {}
        url = download.get("pdf") or download.get("href") or ""
        tracking = body.get("tracking_number") or ""
        if not url or not tracking:
            raise LabelPurchaseError(
                "shipstation returned no label url or tracking number", provider=self.name
            )
        currency = cost.get("currency")
        return Label(
            tracking_number=str(tracking),
            label_url=str(url),
            carrier=str(body.get("carrier_code") or ""),
            service=str(body.get("service_code") or ""),
            amount=Decimal(str(cost.get("amount") or 0)),
            currency=currency.upper() if isinstance(currency, str) else None,
            provider=self.name,
            is_test=self.is_test_mode(),
            shipment_id=str(body.get("label_id") or ""),
            raw=body,
        )


def _address(addr: Address) -> dict[str, Any]:
    out: dict[str, Any] = {
        "address_line1": addr.street1,
        "city_locality": addr.city,
        "postal_code": addr.postal_code,
        "country_code": addr.country,
    }
    if addr.state:
        out["state_province"] = addr.state
    if addr.street2:
        out["address_line2"] = addr.street2
    if addr.name:
        out["name"] = addr.name
    if addr.company:
        out["company_name"] = addr.company
    if addr.phone:
        out["phone"] = addr.phone
    if addr.residential is not None:
        out["address_residential_indicator"] = "yes" if addr.residential else "no"
    else:
        # Explicit "unknown" rather than omission: ShipEngine has that value and
        # defaults to it, so saying so is more honest than staying silent.
        out["address_residential_indicator"] = "unknown"
    return out


def _package(parcel: Parcel) -> dict[str, Any]:
    weight = parcel.weight or parcel.derived_weight
    assert weight is not None  # guarded by _dimension_gap
    out: dict[str, Any] = {"weight": {"value": float(weight.to("oz")), "unit": "ounce"}}
    if parcel.insured_value is not None:
        out["insured_value"] = {"currency": "usd", "amount": float(parcel.insured_value)}
    if parcel.packaging is not None:
        # A carrier package code supplies the dimensions, so they are omitted.
        out["package_code"] = parcel.packaging.code
        return out
    if parcel.dimensions is not None:
        length, width, height = parcel.dimensions.to("in")
        out["dimensions"] = {
            "unit": "inch",
            "length": float(length),
            "width": float(width),
            "height": float(height),
        }
    return out


def _dimension_gap(parcels: tuple[Parcel, ...]) -> Exclusion | None:
    for index, parcel in enumerate(parcels):
        if parcel.weight is None and parcel.derived_weight is None:
            return Exclusion(
                code=ExclusionCode.DIMENSIONS_REQUIRED,
                message=(
                    f"shipstation needs an explicit weight for parcel {index + 1}; it cannot "
                    "derive one from items"
                ),
                source="shipzil",
            )
    return None
