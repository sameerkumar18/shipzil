"""Shippo.

Shippo accepts a `parcels` array, returns **HTTP 201 with `status: SUCCESS`**, and
zero rates. The reason arrives as prose in `messages[]`:

    "Carrier account shippo_usps_master doesn't support one or more shipment options"

So `native_multi_parcel` is False despite the array being accepted, and the client
fans out instead. Declaring capability from the request schema rather than from
observed behaviour would produce a library that silently ships nothing.

Two more Shippo specifics:

* `async` defaults to true. Passing `async: false` is required for a synchronous
  rate, and shipzil is synchronous throughout.
* Rate limiting arrives as a **message on a 201**, not a 429
  (`"UPS - Hard: Too Many Requests"`), so messages are always inspected.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..errors import LabelPurchaseError, ShipzilError
from ..http import request
from ..models import (
    Address,
    DutiesPaidBy,
    Exclusion,
    ExclusionCode,
    Label,
    LithiumBatteryPacking,
    Parcel,
    Quote,
    Rate,
    Shipment,
    Strategy,
)
from ..normalize import code_from_text
from .base import Adapter, Capabilities

BASE = "https://api.goshippo.com"


class ShippoCapabilities(Capabilities):
    # Verified: the array is accepted and rated as nothing. Not a capability.
    native_multi_parcel = False
    order_resource = False
    returns_currency = True
    returns_delivery_estimate = True


class ShippoAdapter(Adapter):
    name = "shippo"
    # extra.dangerous_goods{contains, lithium_batteries, biological_material},
    # extra.dry_ice{contains_dry_ice, weight}, extra.alcohol{contains_alcohol,
    # recipient_type}. No UN number, class or packing group anywhere.
    hazmat_fields = frozenset(
        {"lithium_batteries", "biological_material", "dry_ice", "contains_alcohol"}
    )
    capabilities = ShippoCapabilities()

    def __init__(self, api_token: str, *, timeout: float = 90.0):
        if not api_token:
            raise ValueError("shippo api token is required")
        self.api_token = api_token
        self.timeout = timeout
        self._headers = {"Authorization": f"ShippoToken {api_token}"}

    def is_test_mode(self) -> bool | None:
        """Determinable: Shippo prefixes test tokens with shippo_test_."""
        return self.is_test_token

    @property
    def is_test_token(self) -> bool:
        return self.api_token.startswith("shippo_test_")

    # ── rating ──────────────────────────────────────────────────────

    def rate_single(self, shipment: Shipment) -> Quote:
        parcel = shipment.parcels[0]
        gap = _dimension_gap(parcel)
        if gap is not None:
            return Quote(excluded=(gap,), via=f"{self.name}:shipments")

        _status, body = request(
            "POST",
            f"{BASE}/shipments/",
            headers=self._headers,
            json={
                "address_from": _address(shipment.from_address),
                "address_to": _address(shipment.to_address),
                "parcels": [_parcel(parcel)],
                # Synchronous by explicit request; Shippo defaults to async.
                "async": False,
                **({"extra": extra} if (extra := self._extra(shipment, parcel)) else {}),
                **(
                    {"customs_declaration": customs}
                    if (customs := self._customs(shipment))
                    else {}
                ),
            },
            timeout=self.timeout,
            provider=self.name,
            idempotent=True,
        )

        shipment_id = str(body.get("object_id") or "")
        rates = tuple(
            self._parse_rate(r, shipment_id=shipment_id) for r in (body.get("rates") or [])
        )
        messages = [_message_text(m) for m in (body.get("messages") or [])]
        messages = [m for m in messages if m]

        excluded: tuple[Exclusion, ...] = ()
        if not rates:
            excluded = tuple(self._exclusions(body.get("messages") or []))
            if not excluded:
                excluded = (
                    Exclusion(
                        code=ExclusionCode.SERVICE_UNAVAILABLE,
                        message=(
                            f"shippo returned status {body.get('status')!r} with no rates "
                            "and no messages"
                        ),
                        source="shipzil",
                    ),
                )
        return Quote(
            rates=rates,
            excluded=excluded,
            via=f"{self.name}:shipments",
            strategy=Strategy.NATIVE,
            messages=tuple(messages),
        )

    def _exclusions(self, messages: list[Any]) -> list[Exclusion]:
        """Shippo gives prose only, so every code here is inferred."""
        out: list[Exclusion] = []
        seen: set[tuple[ExclusionCode, str]] = set()
        for msg in messages:
            text = _message_text(msg)
            if not text:
                continue
            code = code_from_text(text)
            carrier = msg.get("source") if isinstance(msg, dict) else None
            key = (code, str(carrier))
            if key in seen:
                continue
            seen.add(key)
            out.append(
                Exclusion(code=code, message=text, carrier=carrier, source="shipzil")
            )
        return out

    @staticmethod
    def _extra(shipment: Shipment, parcel: Parcel) -> dict[str, Any]:
        """Shippo puts hazmat on the shipment's `extra`, not on the parcel.

        Its own note: dangerous-goods contents restrict eligibility to certain
        USPS service levels, so this changes *which rates come back*, not only
        the price. Omitting it silently produces a rate the carrier will refuse.
        """
        extra: dict[str, Any] = {}
        dg = parcel.dangerous_goods
        if dg is not None and dg.contains:
            goods: dict[str, Any] = {"contains": True}
            if dg.lithium_batteries is not LithiumBatteryPacking.NONE:
                # Shippo has one boolean; the PI966/PI967 distinction is lost
                # here, which the adapter reports via _hazmat_fidelity_gap.
                goods["lithium_batteries"] = {"contains": True}
            if dg.biological_material:
                goods["biological_material"] = {"contains": True}
            extra["dangerous_goods"] = goods
            if dg.dry_ice is not None and dg.dry_ice.contains:
                # Kilograms only, and must not exceed the parcel weight.
                extra["dry_ice"] = {
                    "contains_dry_ice": True,
                    "weight": str(dg.dry_ice.weight.to("kg")),
                }
            if dg.contains_alcohol:
                alcohol: dict[str, Any] = {"contains_alcohol": True}
                if dg.alcohol_recipient:
                    alcohol["recipient_type"] = dg.alcohol_recipient
                extra["alcohol"] = alcohol
        if parcel.insured_value is not None:
            extra["insurance"] = {
                "amount": str(parcel.insured_value),
                "currency": "USD",
            }
        return extra

    def _customs(self, shipment: Shipment) -> dict[str, Any] | None:
        """Build `customs_declaration` for a cross-border shipment.

        Not optional. Shippo rates an international shipment happily without
        one and then **refuses the purchase**: "USPS - Customs declaration is
        required for international shipments via the USPS". So omitting this
        produced rates that could never be bought.

        Shippo's `CustomsItem` list is flat and shipment-level, with no parcel
        reference, so every parcel's items are concatenated. That is lossy in
        principle — the customs form cannot say which box a line is in — but it
        is the only shape Shippo accepts, and flattening per-parcel items into a
        flat list is the safe direction. The reverse would require inventing a
        parcel assignment.
        """
        if (shipment.from_address.country or "US") == (shipment.to_address.country or "US"):
            return None
        items: list[dict[str, Any]] = [
            {
                "description": line.description,
                "quantity": line.quantity,
                # LINE TOTALS. Shippo documents net_weight as "quantity * weight
                # per item" and value_amount as "quantity * value per item";
                # per-unit figures under-declare the shipment.
                "net_weight": str(line.line_weight.to("oz")),
                "mass_unit": "oz",
                "value_amount": str(line.line_value),
                "value_currency": line.currency,
                "origin_country": line.origin_country,
                **({"hs_code": line.hs_code} if line.hs_code else {}),
                **({"sku_code": line.sku} if line.sku else {}),
            }
            for line in self.customs_lines(shipment)
        ]
        if not items:
            return None
        eei = self.render_eei(shipment)
        if not eei:
            # Above $2,500 this needs an AES filing and an ITN, which shipzil
            # cannot produce. Refusing beats filing a false exemption.
            return None
        declaration: dict[str, Any] = {
            "eel_pfc": eei,
            "contents_type": "MERCHANDISE",
            # ABANDON vs RETURN is a real cost decision; RETURN is the
            # conservative default because abandonment destroys the goods.
            "non_delivery_option": "RETURN",
            "certify": True,
            "certify_signer": shipment.from_address.name or shipment.from_address.company or "",
            "items": items,
        }
        if shipment.duties_paid_by is DutiesPaidBy.SENDER:
            declaration["incoterm"] = "DDP"
        elif shipment.duties_paid_by is DutiesPaidBy.RECIPIENT:
            declaration["incoterm"] = "DDU"
        return declaration

    def _parse_rate(self, data: dict[str, Any], *, shipment_id: str) -> Rate:
        raw = dict(data)
        raw["_shipment_id"] = shipment_id
        service = data.get("servicelevel") or {}
        days = data.get("estimated_days")
        currency = data.get("currency")
        return Rate(
            carrier=str(data.get("provider") or ""),
            service=str(service.get("name") or service.get("token") or ""),
            amount=Decimal(str(data.get("amount") or 0)),
            currency=currency.upper() if isinstance(currency, str) else None,
            delivery_days=int(days) if days is not None else None,
            guaranteed=None,  # Shippo does not express a guarantee flag
            provider=self.name,
            service_code=service.get("token"),
            strategy=Strategy.NATIVE,
            parcel_count=1,
            raw=raw,
        )

    # ── buying ──────────────────────────────────────────────────────

    def buy(self, shipment: Shipment, rate: Rate) -> Label:
        """Purchase postage.

        """
        raw = rate.raw if isinstance(rate.raw, dict) else {}
        rate_id = raw.get("object_id")
        if not rate_id:
            raise LabelPurchaseError(
                "this rate has no shippo object_id and cannot be bought", provider=self.name
            )
        _status, body = request(
            "POST",
            f"{BASE}/transactions/",
            headers=self._headers,
            json={"rate": rate_id, "label_file_type": "PDF", "async": False},
            timeout=self.timeout,
            provider=self.name,
            retries=0,
        )
        status = str(body.get("status") or "").upper()
        if status != "SUCCESS":
            notes = [_message_text(m) for m in (body.get("messages") or [])]
            raise LabelPurchaseError(
                f"shippo transaction ended {status or 'UNKNOWN'}",
                provider=self.name,
                messages=[n for n in notes if n],
            )
        return Label(
            tracking_number=str(body.get("tracking_number") or ""),
            label_url=str(body.get("label_url") or ""),
            carrier=rate.carrier,
            service=rate.service,
            amount=rate.amount,
            currency=rate.currency,
            provider=self.name,
            is_test=self.is_test_mode(),
            shipment_id=str(body.get("object_id") or ""),
            raw=body,
        )

    def void(self, label: Label) -> bool:
        """Request a refund.

        Shippo answers **HTTP 201 with `status: "ERROR"`** when a refund is
        rejected, and in test mode leaves `messages` empty — the transaction
        moves to `REFUNDREJECTED` with no stated reason. A silent `False` would
        tell the caller nothing, so an active rejection raises with whatever the
        provider did say.

        Returns True for accepted or still-processing refunds, since Shippo
        settles them asynchronously.
        """
        if not label.shipment_id:
            return False
        _status, body = request(
            "POST",
            f"{BASE}/refunds/",
            headers=self._headers,
            json={"transaction": label.shipment_id, "async": False},
            timeout=self.timeout,
            provider=self.name,
            retries=0,
        )
        status = str((body or {}).get("status") or "").upper()
        if status in {"SUCCESS", "QUEUED", "PENDING"}:
            return True
        notes = [_message_text(m) for m in ((body or {}).get("messages") or [])]
        raise ShipzilError(
            f"shippo rejected the refund (status {status or 'UNKNOWN'})"
            + ("" if notes else " and gave no reason"),
            provider=self.name,
            messages=[n for n in notes if n],
        )


def _message_text(msg: Any) -> str:
    if isinstance(msg, dict):
        source = msg.get("source")
        text = msg.get("text") or msg.get("message") or ""
        return f"{source} - {text}" if source and text else str(text or "")
    return str(msg or "")


def _address(addr: Address) -> dict[str, Any]:
    out: dict[str, Any] = {
        "street1": addr.street1,
        "city": addr.city,
        "zip": addr.postal_code,
        "country": addr.country,
    }
    if addr.state:
        out["state"] = addr.state
    if addr.street2:
        out["street2"] = addr.street2
    if addr.name:
        out["name"] = addr.name
    if addr.company:
        out["company"] = addr.company
    if addr.phone:
        out["phone"] = addr.phone
    if addr.email:
        out["email"] = addr.email
    if addr.street3:
        out["street3"] = addr.street3
    residential = addr.residential
    if residential is not None:
        # Omitted when unknown. Sending False would assert "commercial" and
        # understate the quote by the residential surcharge, ~$6 on UPS.
        out["is_residential"] = residential
    return out


def _parcel(parcel: Parcel) -> dict[str, Any]:
    weight = parcel.weight or parcel.derived_weight
    assert weight is not None  # guarded by _dimension_gap
    # Shippo wants strings, and a unit per field rather than per request.
    out: dict[str, Any] = {"weight": str(weight.to("oz")), "mass_unit": "oz"}
    if parcel.packaging is not None:
        # ParcelCreateFromTemplateRequest: template + weight only. Shippo's
        # schema requires the dimension fields to be EMPTY here, so returning
        # early is required, not merely tidy.
        out["template"] = parcel.packaging.code
        return out
    if parcel.dimensions is not None:
        length, width, height = parcel.dimensions.to("in")
        out |= {
            "length": str(length),
            "width": str(width),
            "height": str(height),
            "distance_unit": "in",
        }
    else:
        # Shippo requires dimensions; a weight alone is rejected. Rather than
        # invent a box, this is reported as a gap by _dimension_gap.
        raise AssertionError("unreachable: dimensions gap should have been caught")
    return out


def _dimension_gap(parcel: Parcel) -> Exclusion | None:
    """Shippo needs both a weight and a box; it derives neither."""
    if parcel.weight is None and parcel.derived_weight is None:
        return Exclusion(
            code=ExclusionCode.DIMENSIONS_REQUIRED,
            message=(
                "shippo needs an explicit parcel weight; it cannot derive one from items"
            ),
            source="shipzil",
        )
    if not parcel.has_dimensions:
        # has_dimensions, not `dimensions is None`: a carrier template supplies
        # the size, and Shippo's schema requires the dimension fields to be
        # ABSENT alongside one. Checking `dimensions` directly refused every
        # flat-rate parcel before the request was even built.
        return Exclusion(
            code=ExclusionCode.DIMENSIONS_REQUIRED,
            message=(
                "shippo requires parcel dimensions as well as a weight, or a carrier "
                "template via Parcel(packaging=PackagingTemplate(...))"
            ),
            source="shipzil",
        )
    return None
