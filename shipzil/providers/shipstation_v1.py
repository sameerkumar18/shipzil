"""ShipStation API v1 (legacy, `ssapi.shipstation.com`).

The bluntest of the five surfaces, and the one that forces the most compromise.

Three things make it structurally different from every other adapter here:

**It rates one carrier per call.** `carrierCode` is required on
`POST /shipments/getrates`, so there is no such thing as "rate my account."
Getting a comparable list means calling once per connected carrier and merging
the results, which is a fan-out across *carriers* rather than across parcels.
A rate request therefore costs `1 + len(carriers)` HTTP calls against a
documented 40 req/min budget.

**It returns almost nothing per rate.** Only `serviceCode`, `serviceName`,
`shipmentCost` and `otherCost`. No currency. No delivery estimate. The unified
`Rate` leaves both as None rather than assuming USD, and cost is the sum of the
two components — quoting `shipmentCost` alone understates the price.

**It cannot multi-parcel at all.** Three parcels is an HTTP 400, so multi-parcel
here is always emulated by fan-out.

One thing it does better than the others: `testLabel: true` on label creation
returns a real label response without buying postage. Since the only ShipStation
credentials that exist are production, that flag is the only way to exercise the
purchase path without spending money, and this adapter defaults to it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..errors import (
    ConfigurationError,
    LabelPurchaseError,
    ProviderError,
    RateLimitError,
    ValidationError,
)
from ..http import request
from ..models import (
    Exclusion,
    ExclusionCode,
    Label,
    Quote,
    Rate,
    Shipment,
    Strategy,
)
from ..normalize import code_from_text
from .base import Adapter, Capabilities

BASE = "https://ssapi.shipstation.com"


class ShipStationV1Capabilities(Capabilities):
    native_multi_parcel = False  # verified: 3 parcels -> HTTP 400
    order_resource = False
    returns_currency = False  # v1 sends no currency field at all
    returns_delivery_estimate = False  # nor any delivery estimate


class ShipStationV1Adapter(Adapter):
    name = "shipstation_v1"
    # "The value (in USD) of the line item" — CustomsItem model. Note USD only:
    # v1 has no currency field, so a non-USD Item is mis-declared, not converted.
    customs_value_basis = "line_total"
    # shipzil sends no duty-liability field here, so `duties_gap` reports it
    # rather than letting duties_paid_by vanish. Measured: DDP and DDU
    # produced byte-identical payloads before this was declared.
    incoterm_style = None
    # No hazmat fields found anywhere in the v1 documentation.
    hazmat_fields = frozenset()
    capabilities = ShipStationV1Capabilities()

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        carriers: tuple[str, ...] | None = None,
        test_labels: bool = True,
        confirmation: str = "none",
        timeout: float = 60.0,
    ):
        """`carriers` restricts which carrier codes get rated.

        Left as None, the adapter asks `/carriers` and rates every connected one,
        which is the useful default but also the most expensive: one extra HTTP
        call per carrier, against 40 req/min. Pass an explicit tuple to bound it.

        `test_labels` defaults to **True** because the only v1 credentials that
        exist in practice are production. Set it to False deliberately, and only
        when you intend to spend money.
        """
        if not api_key or not api_secret:
            raise ConfigurationError(
                "shipstation v1 needs both an API key and secret", provider=self.name
            )
        self.api_key = api_key
        self.api_secret = api_secret
        self.carriers = carriers
        self.test_labels = test_labels
        self.confirmation = confirmation
        self.timeout = timeout
        self._carrier_cache: tuple[str, ...] | None = None

    # ── auth ────────────────────────────────────────────────────────

    @property
    def _headers(self) -> dict[str, str]:
        import base64

        token = base64.b64encode(f"{self.api_key}:{self.api_secret}".encode()).decode()
        return {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def is_test_mode(self) -> bool | None:
        """v1 keys carry no test marker, so the credential itself is opaque.

        The `test_labels` flag is decisive though: it is what shipzil sends as
        `testLabel`, and a test response is unmistakable — `shipmentId: -1`,
        `trackingNumber: "99999999999999999999"`, `shipmentCost: 0.0`. So this
        reports the flag rather than a guess about the credential, and that is
        why `test_labels` defaults to True.
        """
        return self.test_labels

    # ── carriers ────────────────────────────────────────────────────

    def carrier_codes(self) -> tuple[str, ...]:
        """Carrier codes connected to the account, cached per adapter instance."""
        if self.carriers is not None:
            return self.carriers
        if self._carrier_cache is not None:
            return self._carrier_cache
        _status, body = request(
            "GET",
            f"{BASE}/carriers",
            headers=self._headers,
            timeout=self.timeout,
            provider=self.name,
            idempotent=True,
        )
        rows = body if isinstance(body, list) else []
        codes = tuple(
            str(r.get("code")) for r in rows if isinstance(r, dict) and r.get("code")
        )
        self._carrier_cache = codes
        return codes

    # ── rating ──────────────────────────────────────────────────────

    def rate_single(self, shipment: Shipment) -> Quote:
        parcel = shipment.parcels[0]
        if not parcel.has_dimensions:
            # See the Shippo note: a packageCode supplies the dimensions.
            return Quote(
                excluded=(
                    Exclusion(
                        code=ExclusionCode.DIMENSIONS_REQUIRED,
                        message=(
                            "shipstation v1 needs parcel dimensions, or a packageCode via "
                            "Parcel(packaging=PackagingTemplate(...)); it cannot derive a box "
                            "from items"
                        ),
                        source="shipzil",
                    ),
                ),
                via=f"{self.name}:getrates",
                strategy=Strategy.NATIVE,
            )

        codes = self.carrier_codes()
        if not codes:
            return Quote(
                excluded=(
                    Exclusion(
                        code=ExclusionCode.CARRIER_ACCOUNT_MISCONFIGURED,
                        message=(
                            "no carriers are connected to this shipstation v1 account, and v1 "
                            "requires an explicit carrierCode on every rate request"
                        ),
                        source="shipzil",
                    ),
                ),
                via=f"{self.name}:getrates",
                strategy=Strategy.NATIVE,
            )

        rates: list[Rate] = []
        excluded: list[Exclusion] = []
        for code in codes:
            try:
                rates.extend(self._rates_for_carrier(code, shipment))
            except RateLimitError as exc:
                # 40 req/min, and one request per carrier. Continuing would just
                # collect more 429s, so stop and report the carriers not reached.
                unreached = list(codes[codes.index(code) :])
                excluded.append(
                    Exclusion(
                        code=ExclusionCode.RATE_LIMITED,
                        message=(
                            f"{exc}. Stopped after rating "
                            f"{len(codes) - len(unreached)} of {len(codes)} carriers; "
                            f"not reached: {', '.join(unreached)}"
                        ),
                        carrier=code,
                        source="provider",
                    )
                )
                break
            except (ProviderError, ValidationError) as exc:
                # One carrier refusing must not discard the others' rates.
                # AuthenticationError is deliberately not caught: bad credentials
                # are not a per-carrier condition and should surface immediately.
                excluded.append(
                    Exclusion(
                        code=code_from_text(str(exc)),
                        message=str(exc),
                        carrier=code,
                        source="provider",
                    )
                )

        return Quote(
            rates=tuple(rates),
            excluded=tuple(excluded),
            via=f"{self.name}:getratesx{len(codes)}",
            strategy=Strategy.NATIVE,
        )

    def _international_options(self, shipment: Shipment) -> dict[str, Any] | None:
        """v1's `internationalOptions`, the thinnest customs surface of the five.

        Its `customsItems` carry only description, quantity, value,
        harmonizedTariffCode and countryOfOrigin. **There is no per-item weight
        and no EEI field at all**, so a US export above the $2,500 threshold
        cannot be declared through v1 even with an ITN in hand.

        Out-of-band dependency worth knowing: ShipStation overwrites supplied
        `customsItems` unless International Settings > Customs Declarations is
        set to "Leave blank (Enter Manually)" in the dashboard. Nothing in the
        API reports that setting, so shipzil cannot detect it.
        """
        if not self.is_cross_border(shipment):
            return None
        lines = self.customs_lines(shipment)
        if not lines:
            return None
        return {
            "contents": "merchandise",
            "nonDelivery": "return_to_sender",
            "customsItems": [
                {
                    "description": line.description,
                    "quantity": line.quantity,
                    "value": float(line.line_value),
                    **({"harmonizedTariffCode": line.hs_code} if line.hs_code else {}),
                    "countryOfOrigin": line.origin_country,
                }
                for line in lines
            ],
        }

    def _rates_for_carrier(self, carrier_code: str, shipment: Shipment) -> list[Rate]:
        parcel = shipment.parcels[0]
        to = shipment.to_address
        # Not asserted non-None: a packaging template satisfies rate_single's
        # size check without supplying dimensions, so this must handle both.
        payload: dict[str, Any] = {
            "carrierCode": carrier_code,
            "serviceCode": None,
            "packageCode": None,
            # v1 rates from a postal code alone; it never asks for a street.
            "fromPostalCode": shipment.from_address.postal_code,
            "toState": to.state,
            "toCountry": to.country or "US",
            "toPostalCode": to.postal_code,
            "toCity": to.city,
            "weight": {
                "value": float(parcel.weight.to("oz")) if parcel.weight else 0.0,
                "units": "ounces",
            },
            "confirmation": self.confirmation,
        }
        if parcel.dimensions is not None:
            length, width, height = parcel.dimensions.to("in")
            payload["dimensions"] = {
                "units": "inches",
                "length": float(length),
                "width": float(width),
                "height": float(height),
            }
        # Only send `residential` when it is actually known. bool(None) is
        # False, which asserts "commercial" and understates the quote.
        if to.residential is not None:
            payload["residential"] = to.residential
        if parcel.packaging is not None:
            # v1 calls this packageCode, and it replaces dimensions.
            payload["packageCode"] = parcel.packaging.code
            # A packageCode supplies the size; sending both is contradictory.
            payload.pop("dimensions", None)

        _status, body = request(
            "POST",
            f"{BASE}/shipments/getrates",
            headers=self._headers,
            json=payload,
            timeout=self.timeout,
            provider=self.name,
            idempotent=True,
        )
        rows = body if isinstance(body, list) else []
        return [
            self._parse_rate(r, carrier_code)
            for r in rows
            if isinstance(r, dict)
        ]

    def _parse_rate(self, data: dict[str, Any], carrier_code: str) -> Rate:
        """Cost is `shipmentCost + otherCost`; neither alone is the price.

        `otherCost` carries surcharges and is frequently non-zero, so quoting
        `shipmentCost` on its own silently understates what the account is
        charged.
        """
        shipment_cost = Decimal(str(data.get("shipmentCost") or 0))
        other_cost = Decimal(str(data.get("otherCost") or 0))
        return Rate(
            carrier=carrier_code,
            service=str(data.get("serviceName") or data.get("serviceCode") or ""),
            amount=shipment_cost + other_cost,
            # v1 sends no currency and no delivery estimate. Assuming USD would
            # be an invention, so both stay None.
            currency=None,
            delivery_days=None,
            guaranteed=None,
            provider=self.name,
            service_code=str(data.get("serviceCode") or "") or None,
            strategy=Strategy.NATIVE,
            parcel_count=1,
            raw={**data, "_carrier_code": carrier_code},
        )

    # ── purchase ────────────────────────────────────────────────────

    def buy(self, shipment: Shipment, rate: Rate) -> Label:
        """Create a label via `POST /shipments/createlabel`.


        When `test_labels` is True this sends `testLabel: true`, which returns a
        real label response without purchasing postage.
        """
        parcel = shipment.parcels[0]
        service_code = (rate.service_code or "").strip()
        if not service_code:
            raise LabelPurchaseError(
                "this rate has no shipstation v1 serviceCode and cannot be bought",
                provider=self.name,
            )
        carrier_code = ""
        if isinstance(rate.raw, dict):
            carrier_code = str(rate.raw.get("_carrier_code") or "")
        if not carrier_code:
            raise LabelPurchaseError(
                "this rate is missing the carrierCode v1 requires on label creation",
                provider=self.name,
            )

        payload: dict[str, Any] = {
            "carrierCode": carrier_code,
            "serviceCode": service_code,
            "packageCode": (
                parcel.packaging.code
                if parcel.packaging is not None
                else str((rate.raw or {}).get("packageCode") or "package")
            ),
            "confirmation": self.confirmation,
            "shipFrom": _address(shipment.from_address),
            "shipTo": _address(shipment.to_address),
            "weight": {
                "value": float(parcel.weight.to("oz")) if parcel.weight else 0.0,
                "units": "ounces",
            },
            "testLabel": self.test_labels,
        }
        intl = self._international_options(shipment)
        if intl:
            # v1 accepts customs only on createlabel; getrates has no such field.
            payload["internationalOptions"] = intl
        if parcel.dimensions is not None:
            length, width, height = parcel.dimensions.to("in")
            payload["dimensions"] = {
                "units": "inches",
                "length": float(length),
                "width": float(width),
                "height": float(height),
            }

        _status, body = request(
            "POST",
            f"{BASE}/shipments/createlabel",
            headers=self._headers,
            json=payload,
            timeout=self.timeout,
            provider=self.name,
            retries=0,  # never blind-retry a purchase
        )
        return self._label(body, rate)

    def _label(self, body: Any, rate: Rate) -> Label:
        """A test label is unmistakable in the response, and is marked as such.

        Confirmed live with `testLabel: true`: `shipmentId: -1`,
        `trackingNumber: "99999999999999999999"`, `shipmentCost: 0.0`. The
        amount reported is what was actually charged, which for a test label is
        zero and not the quoted rate — `rate.amount` still holds the quote.
        """
        data = body if isinstance(body, dict) else {}
        cost = data.get("shipmentCost")
        shipment_id = data.get("shipmentId")
        return Label(
            tracking_number=str(data.get("trackingNumber") or ""),
            # v1 returns the label as base64 in `labelData`, not a URL.
            label_url="",
            carrier=rate.carrier,
            service=rate.service,
            amount=Decimal(str(cost)) if cost is not None else rate.amount,
            currency=None,
            provider=self.name,
            shipment_id="" if shipment_id is None else str(shipment_id),
            is_test=self.test_labels,
            raw={
                **data,
                "_test_label": self.test_labels,
                # Keep the payload small in logs; the caller can still decode it.
                "labelData": "<base64 omitted>" if data.get("labelData") else None,
            },
        )

    def void(self, label: Label) -> bool:
        raw = label.raw if isinstance(label.raw, dict) else {}
        shipment_id = raw.get("shipmentId")
        # A test label has shipmentId -1 because no shipment record exists.
        # Sending that to voidlabel is a guaranteed pointless failure, so refuse
        # locally and say why rather than letting the provider reject it.
        if label.is_test or shipment_id == -1:
            raise LabelPurchaseError(
                "this is a test label (shipmentId -1); there is no shipment to void. "
                "Test labels are not purchased, so nothing needs refunding.",
                provider=self.name,
            )
        if shipment_id is None:
            raise LabelPurchaseError(
                "this label has no shipstation v1 shipmentId and cannot be voided",
                provider=self.name,
            )
        _status, body = request(
            "POST",
            f"{BASE}/shipments/voidlabel",
            headers=self._headers,
            json={"shipmentId": shipment_id},
            timeout=self.timeout,
            provider=self.name,
            retries=0,
        )
        data = body if isinstance(body, dict) else {}
        return bool(data.get("approved"))


def _address(addr: Any) -> dict[str, Any]:
    """v1 uses its own address key names, distinct from v2's."""
    return {
        "name": addr.name or "",
        "company": addr.company or None,
        "street1": addr.street1 or "",
        "street2": addr.street2 or None,
        "street3": addr.street3 or None,
        "city": addr.city or "",
        "state": addr.state or "",
        "postalCode": addr.postal_code or "",
        "country": addr.country or "US",
        "phone": addr.phone or None,
        # Stays None when unknown. bool(None) is False, which would assert
        # "commercial" and understate the quote by the residential surcharge.
        "residential": addr.residential,
    }
