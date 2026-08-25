"""EasyPost.

Two resources matter. `/shipments` rates exactly one parcel — passing a
`parcels` array returns **HTTP 201 with zero rates** and buries the reason in
`messages[].type == "rate_error"`. Multi-parcel lives on `/orders`, which returns
aggregate order-level rates.

So this adapter routes by parcel count, and the caller never has to know.
Verified behaviour is in docs/API-REALITY.md.
"""

from __future__ import annotations

import base64
from decimal import Decimal
from typing import Any

from ..errors import LabelPurchaseError, ProviderError
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
from ..service_id import ServiceId
from .base import Adapter, Capabilities

BASE = "https://api.easypost.com/v2"


class EasyPostCapabilities(Capabilities):
    native_multi_parcel = False  # /shipments silently ignores a parcels array
    order_resource = True  # but /orders does it properly
    returns_currency = True
    returns_delivery_estimate = True


class EasyPostAdapter(Adapter):
    name = "easypost"
    # docs.easypost.com/docs/customs-items, verbatim: value is "Total value
    # (unit value * quantity)" and weight is "Total weight (unit weight *
    # quantity)". Line totals, and `weight` is in ounces per the same table.
    customs_value_basis = "line_total"
    # EasyPost puts duty liability on `options.incoterm`, and its enum has **no
    # DDU**: CFR CIF CIP CPT DAT DAP DDP EXW FAS FCA FOB. The docs say "Setting
    # this value to anything other than 'DDP' will pass the cost and
    # responsibility of duties on to the recipient", so DDP is the only value
    # that changes anything and recipient-pays is expressed by omission.
    incoterm_style = "ddp_only"
    # docs.easypost.com/docs/customs-infos: eel_pfc is prose, "NOEEI 30.37(a)",
    # not Shippo's enum token. The same page also confirms the $2,500 rule —
    # "If value is less than $2500: NOEEI 30.37(a)".
    eei_style = "prose"
    # docs.easypost.com/docs/shipments/options. `options.hazmat` is a large enum
    # (LITHIUM, CLASS_9_*, ORMD, LIMITED_QUANTITY, DIVISION_*, ...), plus
    # separate `dry_ice` / `dry_ice_weight` / `dry_ice_medical` and `alcohol`
    # booleans. This was `frozenset()` on the assumption that nothing was
    # supported, which made shipzil warn about detail EasyPost handles fine.
    # Only what `_options` actually emits. EasyPost's `options.hazmat` enum also
    # covers lithium and fully regulated classes (LITHIUM, CLASS_9_*, ORMD,
    # DIVISION_*), but choosing between CLASS_9_NEW_LITHIUM_INDIVIDUAL,
    # CLASS_9_NEW_LITHIUM_DEVICE, CLASS_9_UNMARKED_LITHIUM and CLASS_9_USED_LITHIUM
    # from shipzil's PI965/966/967 model is a regulatory decision, not a rename,
    # so it stays unclaimed and `hazmat_fidelity_gap` keeps reporting it. Claiming
    # a field without emitting it would turn a warning into a silent drop.
    hazmat_fields = frozenset({"dry_ice", "contains_alcohol"})
    capabilities = EasyPostCapabilities()

    def __init__(self, api_key: str, *, timeout: float = 60.0):
        if not api_key:
            raise ValueError("easypost api key is required")
        self.api_key = api_key
        self.timeout = timeout
        # EasyPost uses HTTP Basic with the key as username and no password.
        token = base64.b64encode(f"{api_key}:".encode()).decode()
        self._headers = {"Authorization": f"Basic {token}"}

    def is_test_mode(self) -> bool | None:
        """Determinable: EasyPost prefixes test keys with EZTK."""
        return self.is_test_key

    @property
    def is_test_key(self) -> bool:
        """EZTK prefixes test keys, EZAK production."""
        return self.api_key.startswith("EZTK")

    # ── rating ──────────────────────────────────────────────────────

    def rate_single(self, shipment: Shipment) -> Quote:
        parcel = shipment.parcels[0]
        missing = self._dimension_gap(parcel)
        if missing is not None:
            return Quote(excluded=(missing,), via="easypost:shipments")

        _status, body = request(
            "POST",
            f"{BASE}/shipments",
            headers=self._headers,
            json={
                "shipment": {
                    "from_address": _address(shipment.from_address),
                    "to_address": _address(shipment.to_address),
                    "parcel": _parcel(parcel),
                    **(
                        {"customs_info": ci}
                        if (ci := self._customs_info(shipment))
                        else {}
                    ),
                    **({"options": o} if (o := self._options(shipment)) else {}),
                }
            },
            timeout=self.timeout,
            provider=self.name,
            idempotent=True,
        )
        return self._quote_from_rates(
            body.get("rates") or [],
            messages=body.get("messages") or [],
            via="easypost:shipments",
            strategy=Strategy.NATIVE,
            parcel_count=1,
            container_id=body.get("id", ""),
        )

    def rate_native_multi(self, shipment: Shipment) -> Quote:
        """Multi-parcel via /orders, where rates are order-level aggregates."""
        gaps = [g for g in (self._dimension_gap(p) for p in shipment.parcels) if g is not None]
        if gaps:
            return Quote(excluded=tuple(gaps[:1]), via="easypost:orders")

        _status, body = request(
            "POST",
            f"{BASE}/orders",
            headers=self._headers,
            json={
                "order": {
                    "from_address": _address(shipment.from_address),
                    "to_address": _address(shipment.to_address),
                    "shipments": [{"parcel": _parcel(p)} for p in shipment.parcels],
                    **(
                        {"customs_info": ci}
                        if (ci := self._customs_info(shipment))
                        else {}
                    ),
                    **({"options": o} if (o := self._options(shipment)) else {}),
                }
            },
            timeout=self.timeout,
            provider=self.name,
            idempotent=True,
        )
        return self._quote_from_rates(
            body.get("rates") or [],
            messages=body.get("messages") or [],
            via="easypost:orders",
            strategy=Strategy.ORDER,
            parcel_count=len(shipment.parcels),
            container_id=body.get("id", ""),
        )

    # ── buying ──────────────────────────────────────────────────────

    def buy(self, shipment: Shipment, rate: Rate) -> Label:
        """Buy postage. Orders and shipments are different endpoints entirely.

        This is worth spelling out because the first implementation got it wrong
        and no test caught it: only the single-parcel path had ever run.

        * A **shipment** rate is bought with `POST /shipments/{id}/buy` and a
          rate id: `{"rate": {"id": "rate_..."}}`.
        * An **order** rate is bought with `POST /orders/{id}/buy` and a carrier
          and service *by name*: `{"carrier": "USPS", "service": "GroundAdvantage"}`.
          Orders have no per-rate purchase, and the response is a `shipments`
          array where each entry carries its own `postage_label`.

        Verified against EasyPost's own recorded test traffic
        (`tests/cassettes/test_order_buy.yaml` in easypost-python), not docs.
        """
        raw = rate.raw if isinstance(rate.raw, dict) else {}
        container_id = raw.get("shipment_id") or raw.get("_container_id")
        if not container_id:
            raise LabelPurchaseError(
                "this rate cannot be bought: it carries no EasyPost shipment or order id. "
                "Rates produced by fan-out across parcels must be bought per parcel.",
                provider=self.name,
            )

        if rate.strategy is Strategy.ORDER or str(container_id).startswith("ord_"):
            return self._buy_order(container_id, rate)
        return self._buy_shipment(container_id, rate)

    def _buy_shipment(self, shipment_id: str, rate: Rate) -> Label:
        rate_id = (rate.raw or {}).get("id")
        if not rate_id:
            raise LabelPurchaseError(
                "this shipment rate has no EasyPost rate id", provider=self.name
            )
        _status, body = request(
            "POST",
            f"{BASE}/shipments/{shipment_id}/buy",
            headers=self._headers,
            json={"rate": {"id": rate_id}},
            timeout=self.timeout,
            provider=self.name,
            retries=0,  # never blind-retry a purchase
        )
        return self._label(body)

    def _buy_order(self, order_id: str, rate: Rate) -> Label:
        """Orders buy by carrier and service name, and yield one label per parcel."""
        if not rate.carrier or not rate.service:
            raise LabelPurchaseError(
                "buying an EasyPost order needs the carrier and service names, and this "
                f"rate has carrier={rate.carrier!r} service={rate.service!r}",
                provider=self.name,
            )
        _status, body = request(
            "POST",
            f"{BASE}/orders/{order_id}/buy",
            headers=self._headers,
            json={"carrier": rate.carrier, "service": rate.service},
            timeout=self.timeout,
            provider=self.name,
            retries=0,  # never blind-retry a purchase
        )
        shipments = body.get("shipments") or []
        labels = tuple(self._label(s) for s in shipments if isinstance(s, dict))
        if not labels:
            raise LabelPurchaseError(
                "EasyPost order purchase returned no shipments to take labels from",
                provider=self.name,
                messages=[str(m) for m in (body.get("messages") or [])],
            )
        # The order-level result: first label's identifiers, every label attached.
        head = labels[0]
        return Label(
            tracking_number=head.tracking_number,
            label_url=head.label_url,
            carrier=rate.carrier,
            service=rate.service,
            amount=rate.amount,
            currency=rate.currency,
            provider=self.name,
            shipment_id=str(body.get("id") or order_id),
            is_test=self.is_test_mode(),
            parcel_labels=labels,
            raw=body,
        )

    def void(self, label: Label) -> bool:
        if not label.shipment_id:
            return False
        _status, body = request(
            "POST",
            f"{BASE}/shipments/{label.shipment_id}/refund",
            headers=self._headers,
            timeout=self.timeout,
            provider=self.name,
            retries=0,
        )
        refund = (body or {}).get("refund_status")
        return refund in {"submitted", "refunded"}

    # ── translation ─────────────────────────────────────────────────

    def _options(self, shipment: Shipment) -> dict[str, Any]:
        """`shipment.options`, per docs.easypost.com/docs/shipments/options.

        Duty liability lives here rather than on `customs_info`, which is why
        this adapter had none: the customs block was wired and the options block
        did not exist, so `duties_paid_by` had nowhere to go.

        `dry_ice_weight` is documented in **ounces**, unlike ShipEngine's
        kilograms, and EasyPost wants it as a string.
        """
        out: dict[str, Any] = {}
        incoterm = self.render_incoterm(shipment)
        if incoterm:
            out["incoterm"] = incoterm
        for dg in shipment.dangerous_goods:
            if dg.contains_alcohol:
                out["alcohol"] = True
            if dg.dry_ice is not None and dg.dry_ice.contains:
                out["dry_ice"] = True
                out["dry_ice_weight"] = str(float(dg.dry_ice.weight.to("oz")))
        return out

    def _customs_info(self, shipment: Shipment) -> dict[str, Any] | None:
        """EasyPost nests everything under `customs_info`.

        Field names differ from the others: `hs_tariff_number` rather than
        `hs_code`, and `origin_country`. `code` is the SKU field.

        Values and weights are **line totals** in USD and ounces, which the
        CustomsItem reference states outright: *"Total value (unit value *
        quantity)"* and *"Total weight (unit weight * quantity)"*. That puts
        EasyPost with Shippo and ShipStation v1, against ShipEngine and Easyship.

        `eel_pfc` is prose, "NOEEI 30.37(a)", where Shippo uses the token
        `NOEEI_30_37_a`, and the same page documents the $2,500 threshold shipzil
        applies. Not modelled here: `manufacturer`, `eccn`,
        `printed_commodity_identifier`, `contents_explanation`,
        `restriction_comments` and `declaration`. Also worth knowing, from the
        CustomsInfo page: **UPS caps customs items at 100.**
        """
        if not self.is_cross_border(shipment):
            return None
        lines = self.customs_lines(shipment)
        eel = self.render_eei(shipment)
        if not lines or not eel:
            return None
        return {
            "contents_type": "merchandise",
            "customs_certify": True,
            "customs_signer": (
                shipment.from_address.name or shipment.from_address.company or ""
            ),
            "eel_pfc": eel,
            # Abandonment destroys the goods, so returning is the safer default.
            "non_delivery_option": "return",
            "restriction_type": "none",
            "customs_items": [
                {
                    "description": line.description,
                    "quantity": line.quantity,
                    "value": float(line.line_value),
                    "weight": float(line.line_weight.to("oz")),
                    "origin_country": line.origin_country,
                    "currency": line.currency,
                    **({"hs_tariff_number": line.hs_code} if line.hs_code else {}),
                    **({"code": line.sku} if line.sku else {}),
                }
                for line in lines
            ],
        }

    def _dimension_gap(self, parcel: Parcel) -> Exclusion | None:
        """EasyPost cannot derive a box from items; say so rather than guessing."""
        if parcel.weight is not None or parcel.derived_weight is not None:
            return None
        return Exclusion(
            code=ExclusionCode.DIMENSIONS_REQUIRED,
            message=(
                "easypost needs an explicit parcel weight; it cannot derive one from items. "
                "Supply Parcel(weight=...) or give every Item a weight."
            ),
            source="shipzil",
        )

    def _quote_from_rates(
        self,
        rates: list[dict[str, Any]],
        *,
        messages: list[dict[str, Any]],
        via: str,
        strategy: Strategy,
        parcel_count: int,
        container_id: str,
    ) -> Quote:
        notes = [
            str(m.get("message") if isinstance(m, dict) else m)
            for m in messages
            if isinstance(m, dict | str)
        ]
        parsed = tuple(
            self._rate(r, strategy=strategy, parcel_count=parcel_count, container_id=container_id)
            for r in rates
        )
        excluded: tuple[Exclusion, ...] = ()
        if not parsed:
            excluded = tuple(_exclusions_from_messages(messages)) or (
                Exclusion(
                    code=ExclusionCode.SERVICE_UNAVAILABLE,
                    message="easypost returned no rates and gave no reason",
                    source="shipzil",
                ),
            )
        return Quote(
            rates=parsed,
            excluded=excluded,
            via=via,
            strategy=strategy,
            messages=tuple(notes),
        )

    def _rate(
        self,
        data: dict[str, Any],
        *,
        strategy: Strategy,
        parcel_count: int,
        container_id: str,
    ) -> Rate:
        raw = dict(data)
        raw["_container_id"] = container_id
        days = data.get("delivery_days") or data.get("est_delivery_days")
        return Rate(
            carrier=str(data.get("carrier") or ""),
            service=str(data.get("service") or ""),
            service_id=ServiceId.build(
                provider=self.name,
                # EasyPost reports UPS as "UPSDAP" and FedEx as "FedExDefault",
                # which are account types rather than carriers.
                carrier=str(data.get("carrier") or ""),
                service=str(data.get("service") or ""),
            ),
            amount=Decimal(str(data.get("rate") or "0")),
            currency=data.get("currency"),
            delivery_days=int(days) if days is not None else None,
            guaranteed=data.get("delivery_date_guaranteed"),
            provider=self.name,
            service_code=data.get("service"),
            strategy=strategy,
            parcel_count=parcel_count,
            raw=raw,
        )

    def _label(self, body: dict[str, Any]) -> Label:
        postage = body.get("postage_label") or {}
        selected = body.get("selected_rate") or {}
        url = postage.get("label_url") or ""
        tracking = body.get("tracking_code") or ""
        if not url or not tracking:
            raise LabelPurchaseError(
                "easypost returned no label url or tracking code",
                provider=self.name,
                messages=[str(m) for m in (body.get("messages") or [])],
            )
        return Label(
            tracking_number=tracking,
            label_url=url,
            carrier=str(selected.get("carrier") or ""),
            service=str(selected.get("service") or ""),
            amount=Decimal(str(selected.get("rate") or "0")),
            currency=selected.get("currency"),
            provider=self.name,
            is_test=self.is_test_mode(),
            shipment_id=str(body.get("id") or ""),
            raw=body,
        )


def _address(addr: Address) -> dict[str, Any]:
    out: dict[str, Any] = {
        "street1": addr.street1,
        "city": addr.city,
        "zip": addr.postal_code,  # EasyPost calls it zip
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
    if addr.residential is not None:
        # Omitted when unknown; never sent as False on the caller's silence.
        out["residential"] = addr.residential
    if addr.street3:
        out["street3"] = addr.street3
    return out


def _parcel(parcel: Parcel) -> dict[str, Any]:
    weight = parcel.weight or parcel.derived_weight
    if weight is None:  # guarded by _dimension_gap before we get here
        raise ProviderError("parcel has no resolvable weight", provider="easypost")
    out: dict[str, Any] = {"weight": float(weight.to("oz"))}
    if parcel.dimensions is not None:
        length, width, height = parcel.dimensions.to("in")
        out |= {"length": float(length), "width": float(width), "height": float(height)}
    return out


def _exclusions_from_messages(messages: list[dict[str, Any]]) -> list[Exclusion]:
    """Turn EasyPost's semi-structured messages into exclusions.

    `type: "rate_error"` with prose is as structured as EasyPost gets.
    """
    out: list[Exclusion] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        text = str(msg.get("message") or "")
        lowered = text.lower()
        if "parcel are required" in lowered or "parcel is required" in lowered:
            code = ExclusionCode.MULTIPACKAGE_NOT_SUPPORTED
        elif "rate" in str(msg.get("type") or "").lower():
            code = ExclusionCode.SERVICE_UNAVAILABLE
        else:
            code = ExclusionCode.UNKNOWN
        out.append(
            Exclusion(
                code=code,
                message=text or "easypost reported a rating problem",
                carrier=msg.get("carrier"),
                source="shipzil",
            )
        )
    return out
