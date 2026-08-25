"""Stable, addressable identity for a carrier service.

The gateway addressing scheme is `{provider}-{carrier}-{service}`, and the choice
of *provider-namespaced* rather than canonical is deliberate.

The same USPS service arrives from five providers under five spellings:

    easypost         USPS                             GroundAdvantage
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
  and normalising them fixes real breakage — EasyPost calls UPS `UPSDAP`, and
  ShipStation v1 sells USPS under the reseller code `stamps_com`, so without
  normalisation the "carrier" field is sometimes a sales channel. Services are an
  open-ended set per provider and normalising them is the equivalence problem.

* **The identity is structured, not a string.** A future layer needs to address the
  unqualified `{carrier}-{service}` and pick the provider itself, so the parts stay
  separate and `slug` is a rendering rather than the storage format.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

__all__ = ["ServiceId", "normalize_carrier"]

#: Provider spellings observed in recorded traffic under `.probe/`, mapped to a
#: normalised carrier. Derived from real responses rather than guessed, which is
#: why the list is short and specific: these are the ones that actually differ.
#:
#: `stamps_com` is the interesting one. ShipStation v1 returns it as the carrier
#: code for USPS rates because Stamps.com is the reseller, so a caller filtering on
#: "usps" would silently miss every USPS rate.
_CARRIER_ALIASES = {
    "upsdap": "ups",           # EasyPost's UPS "daily rates" account type
    "ups_walleted": "ups",     # EasyPost wallet-billed UPS
    "fedexdefault": "fedex",   # EasyPost
    "fedexsmartpost": "fedex",
    "stamps_com": "usps",      # ShipStation v1 sells USPS through Stamps.com
    "stamps": "usps",
    "endicia": "usps",         # another USPS reseller channel
    "globalpost": "usps",
    "dhlecommerce": "dhl_ecommerce",
    "dhlexpress": "dhl_express",
    "canadapost": "canada_post",
    "royalmail": "royal_mail",
    "australiapost": "australia_post",
}

#: Carrier names that appear as a prefix inside a service string. ShipStation v1
#: returns `"USPS Ground Advantage - Package"` and Easyship returns
#: `"FedEx 2Day®"`, so the carrier has to be recovered from the service text when
#: the provider offers no separate field. Longest first, so `dhl express` wins over
#: `dhl`.
_CARRIER_PREFIXES = (
    "dhl ecommerce",
    "dhl express",
    "canada post",
    "australia post",
    "royal mail",
    "usps",
    "ups",
    "fedex",
    "dhl",
    "ontrac",
    "lasership",
    "purolator",
    "sendle",
    "gso",
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
    # Some providers append an account or product suffix: "UPSDAP", "USPS Returns".
    for prefix in _CARRIER_PREFIXES:
        token = _slugify(prefix)
        if slug == token or slug.startswith(token + "_"):
            return token
    return slug


def carrier_from_service(service: str) -> str:
    """Recover a carrier from a service string that embeds it.

    Needed for ShipStation v1 (`"USPS Ground Advantage - Package"`) and Easyship
    (`"FedEx 2Day®"`), neither of which returns a usable separate carrier field on
    the rating path. Returns `""` when no known carrier is present, rather than
    guessing from the first word.
    """
    folded = _ascii_fold(service).lower().strip()
    for prefix in _CARRIER_PREFIXES:
        if folded == prefix or folded.startswith(prefix + " "):
            return _slugify(prefix)
    return ""


@dataclass(frozen=True)
class ServiceId:
    """Addressable identity for one provider's one service.

    Rendered as `{provider}-{carrier}-{service}`, with packaging appended when the
    provider distinguishes it:

        easypost-usps-groundadvantage
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
    ) -> ServiceId | None:
        """Construct from a provider's raw strings, or `None` if unidentifiable.

        Returns `None` rather than a partial identity when there is no service to
        address. A half-formed address is worse than no address: it looks usable
        and is not stable.
        """
        service_slug = _slugify(service)
        if not service_slug:
            return None

        carrier_token = normalize_carrier(carrier) if carrier else ""
        if not carrier_token:
            carrier_token = carrier_from_service(service)

        # Where the carrier is embedded in the service text, strip it so the
        # address does not read `usps-usps_ground_advantage`.
        if carrier_token and service_slug.startswith(carrier_token + "_"):
            service_slug = service_slug[len(carrier_token) + 1 :]

        return cls(
            provider=_slugify(provider),
            carrier=carrier_token,
            service=service_slug,
            packaging=_slugify(packaging) if packaging else None,
        )
