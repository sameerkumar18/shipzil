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
    Exclusion,
    ExclusionCode,
    Label,
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
    requires_explicit_dimensions = True
    requires_item_classification = False
    returns_currency = True
    returns_delivery_estimate = True


class ShippoAdapter(Adapter):
    name = "shippo"
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

    def buy(self, shipment: Shipment, rate: Rate, *, idempotency_key: str | None) -> Label:
        """Purchase postage.

        Shippo documents no idempotency header on /transactions, so the key
        is always None here and the only protection is retries=0.
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
    return out


def _parcel(parcel: Parcel) -> dict[str, Any]:
    weight = parcel.weight or parcel.derived_weight
    assert weight is not None  # guarded by _dimension_gap
    # Shippo wants strings, and a unit per field rather than per request.
    out: dict[str, Any] = {"weight": str(weight.to("oz")), "mass_unit": "oz"}
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
    if parcel.dimensions is None:
        return Exclusion(
            code=ExclusionCode.DIMENSIONS_REQUIRED,
            message="shippo requires parcel dimensions as well as a weight",
            source="shipzil",
        )
    return None
