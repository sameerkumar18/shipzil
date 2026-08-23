"""Normalising provider prose into a single exclusion vocabulary.

ShipStation v2 is the only surface that reports "this carrier cannot do that" in
structured, per-carrier form, so its `error_code` values are the vocabulary. The
other four providers say the same things in prose, and this module maps them on.

Every inference made here is marked `source="shipzil"` when it becomes an
`Exclusion`, so a guess never reads as a fact.
"""

from __future__ import annotations

import re

from .models import ExclusionCode

__all__ = ["code_from_provider_code", "code_from_text"]

# ShipStation v2 / ShipEngine codes, used verbatim where the provider gives them.
_PROVIDER_CODES: dict[str, ExclusionCode] = {
    "multipackage_not_supported": ExclusionCode.MULTIPACKAGE_NOT_SUPPORTED,
    "service_not_supported": ExclusionCode.SERVICE_UNAVAILABLE,
    "carrier_not_supported": ExclusionCode.SERVICE_UNAVAILABLE,
    "invalid_address": ExclusionCode.ADDRESS_UNSUPPORTED,
    "rate_limit_exceeded": ExclusionCode.RATE_LIMITED,
    "invalid_field_value": ExclusionCode.SERVICE_UNAVAILABLE,
    "field_value_required": ExclusionCode.DIMENSIONS_REQUIRED,
}

# Ordered most-specific first: the first pattern that matches wins, so a message
# mentioning both "multipackage" and "not supported" lands on the former.
_PATTERNS: list[tuple[re.Pattern[str], ExclusionCode]] = [
    (re.compile(r"multi[\s_-]?package|multi[\s_-]?parcel", re.IGNORECASE),
     ExclusionCode.MULTIPACKAGE_NOT_SUPPORTED),
    # Shippo's phrasing for a master account that cannot carry the request.
    (re.compile(r"doesn'?t support one or more shipment options", re.IGNORECASE),
     ExclusionCode.CARRIER_ACCOUNT_MISCONFIGURED),
    (re.compile(r"too many requests|rate limit|throttl", re.IGNORECASE),
     ExclusionCode.RATE_LIMITED),
    (re.compile(r"hs[\s_-]?code|category can'?t be blank|customs", re.IGNORECASE),
     ExclusionCode.ITEM_CLASSIFICATION_REQUIRED),
    (re.compile(r"\b(weight|dimension|length|width|height|parcel)\b.{0,40}\b"
                r"(required|can'?t be blank|missing)\b", re.IGNORECASE),
     ExclusionCode.DIMENSIONS_REQUIRED),
    (re.compile(r"\brequired for rating\b", re.IGNORECASE),
     ExclusionCode.DIMENSIONS_REQUIRED),
    (re.compile(r"address|postal|zip|country.{0,20}not (supported|serviced)", re.IGNORECASE),
     ExclusionCode.ADDRESS_UNSUPPORTED),
    (re.compile(r"no shipping solutions|no rates? (are )?available|no service", re.IGNORECASE),
     ExclusionCode.SERVICE_UNAVAILABLE),
    (re.compile(r"account|credential|not (enabled|configured|activated)", re.IGNORECASE),
     ExclusionCode.CARRIER_ACCOUNT_MISCONFIGURED),
    (re.compile(r"doesn'?t support|not supported|unsupported|unable to rate", re.IGNORECASE),
     ExclusionCode.SERVICE_UNAVAILABLE),
]


def code_from_provider_code(raw: str | None) -> ExclusionCode | None:
    """Map a provider's own error code, when it has one."""
    if not raw:
        return None
    return _PROVIDER_CODES.get(raw.strip().lower())


def code_from_text(text: str | None) -> ExclusionCode:
    """Infer a code from prose. Returns UNKNOWN rather than pretending."""
    if not text:
        return ExclusionCode.UNKNOWN
    for pattern, code in _PATTERNS:
        if pattern.search(text):
            return code
    return ExclusionCode.UNKNOWN
