"""Stable, addressable keys for carrier services.

The gateway addressing scheme is `{provider}-{carrier}-{service}`. Keys remain
provider-scoped because the adapters do not establish cross-provider service
equivalence.

The same USPS service arrives from four providers under four spellings:

    shippo           USPS                             Ground Advantage
    shipstation_v2   USPS                             USPS Ground Advantage
    shipstation_v1   USPS Ground Advantage - Package  usps_ground_advantage

A canonical identity would have to assert that those are *the same service*, which
is an equivalence claim. An incorrect one silently ships something other than what
the caller asked for, so equivalence is a separate problem with a higher
correctness bar and it is not solved here. This module addresses what exists; it
does not claim what substitutes for what.

Two consequences fall out of that:

* **`carrier` is normalised, `service` is not.** Carriers are a small closed set,
  and normalising them fixes real breakage — ShipStation v1 sells USPS under the
  reseller code `stamps_com`, so without
  normalisation the "carrier" field is sometimes a sales channel. Services are an
  open-ended set per provider and normalising them is the equivalence problem.

* **The identity is structured, not a string.** A future layer needs to address the
  unqualified `{carrier}-{service}` and pick the provider itself, so the parts stay
  separate and `slug` is a rendering rather than the storage format.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ServiceInfo",
    "ServiceKey",
    "ServiceMap",
    "carrier_from_service",
    "carrier_matches",
    "normalize_carrier",
]

#: Provider spellings observed in recorded traffic under `.probe/`, mapped to a
#: normalised carrier. Derived from real responses rather than guessed, which is
#: why the list is short and specific: these are the ones that actually differ.
#:
#: `stamps_com` is the interesting one. ShipStation v1 returns it as the carrier
#: code for USPS rates because Stamps.com is the reseller, so a caller filtering on
#: "usps" would silently miss every USPS rate.
_CARRIER_ALIASES = {
    "stamps_com": "usps",      # ShipStation v1 sells USPS through Stamps.com
}

#: Carrier brands that appear as a prefix inside a carrier or service string.
#: ShipStation v1 returns `"USPS Ground Advantage - Package"` and Easyship returns
#: `"FedEx 2Day®"`, so the brand has to be recovered from the service text when the
#: provider offers no separate carrier field.
#:
#: Evidence label: **specification / brand knowledge**, not fixture-observed. The
#: captured fixtures under `tests/fixtures/` contain only USPS, UPS and FedEx, which
#: is precisely why a short list looked sufficient and silently failed every other
#: carrier. `carrier_matches` is the general safety net for brands absent here.
_CARRIER_BRANDS: tuple[str, ...] = (
    "usps",
    "ups",
    "fedex",
    "dhl express",
    "dhl ecommerce",
    "dhl paket",
    "dhl",
    "canada post",
    "purolator",
    "royal mail",
    "australia post",
    "japan post",
    "singapore post",
    "deutsche post",
    "asendia",
    "aramex",
    "sendle",
    "ontrac",
    "lasership",
    "evri",
    "hermes",
    "dpd",
    "gls",
    "tnt",
    "correos",
    "colissimo",
    "chronopost",
)

#: Longest brand first, so `dhl express` wins over `dhl` rather than depending on
#: the literal order above. Sorted here so the guarantee is enforced, not asserted.
_BRANDS_LONGEST_FIRST: tuple[str, ...] = tuple(
    sorted(_CARRIER_BRANDS, key=len, reverse=True)
)


def _ascii_fold(text: str) -> str:
    """Strip the trademark glyphs providers put in service names.

    Easyship returns `"FedEx 2Day®"` and ShipStation v2 returns `"UPS® Ground"`.
    A registered-trademark sign in an identifier is a bug waiting to happen.
    """
    text = text.replace("®", "").replace("™", "").replace("©", "")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _slugify(text: str) -> str:
    text = _ascii_fold(text).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def normalize_carrier(raw: str) -> str:
    """Normalise a provider's carrier string to a stable carrier token.

    Unknown carriers are slugified rather than rejected, because refusing an
    unrecognised carrier would make the gateway useless the moment a provider adds
    one. An unmapped carrier is still stable and still addressable; it is simply
    not guaranteed to match another provider's name for the same carrier.
    """
    slug = _slugify(raw)
    if not slug:
        return ""
    if slug in _CARRIER_ALIASES:
        return _CARRIER_ALIASES[slug]
    # Some providers append an account or product suffix: "USPS Returns",
    # "UPS® Ground". Collapse to the brand so the carrier field stays a carrier.
    for brand in _BRANDS_LONGEST_FIRST:
        token = _slugify(brand)
        if slug == token or slug.startswith(token + "_"):
            return token
    return slug


def carrier_matches(requested: str, actual: str) -> bool:
    """Whether an already-normalised `actual` carrier satisfies `requested`.

    Exact match, or `actual` is a network inside the requested brand. This is the
    general fix for a carrier the brand list does not know: `carriers={"dhl"}`
    matches `dhl_express` and `dhl_ecommerce` without shipzil having to enumerate
    every carrier that exists.

    Deliberately **not** a service equivalence claim. It says the rate is carried by
    the requested carrier, not that two services substitute for each other.
    """
    want = normalize_carrier(requested)
    if not want or not actual:
        return False
    return actual == want or actual.startswith(want + "_")


def carrier_from_service(service: str) -> str:
    """Recover a carrier from a service string that embeds it.

    Needed for ShipStation v1 (`"USPS Ground Advantage - Package"`) and Easyship
    (`"FedEx 2Day®"`), neither of which returns a usable separate carrier field on
    the rating path. Returns `""` when no known brand is present, rather than
    guessing from the first word.
    """
    folded = _ascii_fold(service).lower().strip()
    for brand in _BRANDS_LONGEST_FIRST:
        if folded == brand or folded.startswith(brand + " "):
            return _slugify(brand)
    return ""


@dataclass(frozen=True, order=True)
class ServiceKey:
    """Addressable key for one provider's one service.

    Rendered as `{provider}-{carrier}-{service}`, with packaging appended when the
    provider distinguishes it:

        shippo-usps-ground_advantage
        shipstation_v1-usps-ground_advantage-package

    That fourth component is not decoration. ShipStation v1 returns two rates
    sharing the service code `usps_ground_advantage`, differing only by packaging
    ("Package" versus "Thick Envelope") and priced differently. Without packaging
    in the key those two rates collide onto one address.
    """

    provider: str
    carrier: str
    service: str
    packaging: str | None = None

    @property
    def slug(self) -> str:
        """The wire and display form. Storage should keep the parts."""
        parts = [self.provider, self.carrier, self.service]
        if self.packaging:
            parts.append(self.packaging)
        return "-".join(p for p in parts if p)

    @property
    def unqualified(self) -> str:
        """`{carrier}-{service}`, without the provider.

        Deliberately **not** an equivalence claim. Two providers can produce the
        same `unqualified` value for services that are not interchangeable — a
        DDP-capable rate and a non-DDP one being the obvious case. This exists so a
        later layer can group candidates; deciding that a group is substitutable is
        a separate judgement that shipzil does not currently make.
        """
        return "-".join(p for p in (self.carrier, self.service) if p)

    def __str__(self) -> str:
        return self.slug

    @classmethod
    def build(
        cls,
        *,
        provider: str,
        carrier: str,
        service: str,
        packaging: str | None = None,
    ) -> ServiceKey | None:
        """Construct from a provider's raw strings, or `None` if unidentifiable.

        Returns `None` rather than a partial identity when there is no service to
        address. A half-formed address is worse than no address: it looks usable
        and is not stable.
        """
        provider_slug = _slugify(provider)
        service_slug = _slugify(service)
        if not provider_slug or not service_slug:
            return None

        carrier_token = normalize_carrier(carrier) if carrier else ""
        if not carrier_token:
            carrier_token = carrier_from_service(service)
        if not carrier_token:
            return None

        return cls(
            provider=provider_slug,
            carrier=carrier_token,
            service=service_slug,
            packaging=_slugify(packaging) if packaging else None,
        )

    @classmethod
    def parse(cls, value: str) -> ServiceKey:
        """Parse the display form: `{provider}-{carrier}-{service}[-variant]`."""
        parts = value.strip().split("-")
        if len(parts) < 3:
            raise ValueError(
                "service key must look like provider-carrier-service[-variant]"
            )
        key = cls.build(
            provider=parts[0],
            carrier=parts[1],
            service=parts[2],
            packaging="-".join(parts[3:]) or None,
        )
        if key is None:
            raise ValueError(f"invalid service key: {value!r}")
        return key


@dataclass(frozen=True)
class ServiceInfo:
    """What the Gateway has learned about one provider service."""

    key: ServiceKey
    name: str
    sources: tuple[str, ...] = ()


class ServiceMap:
    """The Gateway's observed provider/carrier/service map.

    This is intentionally a small mapping, not a global claim that a service is
    always available. Availability depends on the account, lane and shipment.
    The map records candidates returned by actual provider calls and resolves
    explicit filters against those candidates.
    """

    def __init__(self, services: Iterable[ServiceInfo] = ()) -> None:
        self._items: dict[ServiceKey, ServiceInfo] = {}
        for service in services:
            self.add(service.key, service.name, service.sources)

    def add(
        self,
        key: ServiceKey,
        name: str = "",
        sources: str | Iterable[str] = (),
    ) -> None:
        """Add or merge one observed service."""
        if isinstance(sources, str):
            sources = (sources,)
        old = self._items.get(key)
        merged = tuple(sorted(set((old.sources if old else ()) + tuple(sources))))
        self._items[key] = ServiceInfo(
            key=key,
            name=name or (old.name if old else ""),
            sources=merged,
        )

    def add_rate(self, rate: Any) -> None:
        """Learn from a rate without making a rate-specific dependency here."""
        key = getattr(rate, "service_key", None)
        if key is not None:
            self.add(key, str(getattr(rate, "service", "")), getattr(rate, "source", None) or ())

    def resolve(
        self,
        *,
        providers: Iterable[str] | None = None,
        carriers: Iterable[str] | None = None,
        services: Iterable[ServiceKey | str] | None = None,
    ) -> tuple[ServiceInfo, ...]:
        """Resolve explicit filters with AND semantics.

        This is a deterministic lookup. It does not infer equivalence, select a
        cheapest service or consult health data.
        """
        provider_set = {_slugify(p) for p in providers} if providers is not None else None
        carrier_list = list(carriers) if carriers is not None else None
        service_set = {
            s if isinstance(s, ServiceKey) else ServiceKey.parse(s) for s in services
        } if services is not None else None

        out = []
        for info in self._items.values():
            key = info.key
            if provider_set is not None and key.provider not in provider_set:
                continue
            if carrier_list is not None and not any(
                carrier_matches(requested, key.carrier) for requested in carrier_list
            ):
                continue
            if service_set is not None and key not in service_set:
                continue
            out.append(info)
        return tuple(sorted(out, key=lambda item: item.key))

    def __iter__(self) -> Iterator[ServiceInfo]:
        return iter(sorted(self._items.values(), key=lambda item: item.key))

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: object) -> bool:
        return key in self._items
