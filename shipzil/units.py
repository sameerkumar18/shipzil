"""Unit handling.

Five providers want five conventions: EasyPost takes ounces and inches inline,
Shippo takes strings with `mass_unit`/`distance_unit`, ShipStation v1 wants
`"ounces"`/`"inches"`, v2 wants `"ounce"`/`"inch"`, and Easyship declares
`kg`/`cm` once per request.

Rather than pick a favourite, values keep the unit they were given and convert
only at the adapter boundary. Conversions go through integer-friendly Decimal
arithmetic so a weight does not drift when it crosses two providers during
failover.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

WeightUnit = Literal["oz", "lb", "g", "kg"]
LengthUnit = Literal["in", "cm", "mm"]

# Everything is defined relative to one canonical base per dimension:
# grams for mass, millimetres for length. Exact where possible.
_TO_GRAMS: dict[str, Decimal] = {
    "g": Decimal(1),
    "kg": Decimal(1000),
    "oz": Decimal("28.349523125"),  # exact by definition
    "lb": Decimal("453.59237"),  # exact by definition
}

_TO_MM: dict[str, Decimal] = {
    "mm": Decimal(1),
    "cm": Decimal(10),
    "in": Decimal("25.4"),  # exact by definition
}


def _q(value: Decimal, places: int) -> Decimal:
    """Round half-up to `places`, the convention carriers use for billing."""
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class Weight:
    """A mass with its unit. Compare and convert, never guess."""

    value: Decimal
    unit: WeightUnit = "oz"

    def __post_init__(self) -> None:
        if self.unit not in _TO_GRAMS:
            raise ValueError(f"unknown weight unit {self.unit!r}; expected one of {sorted(_TO_GRAMS)}")
        if self.value <= 0:
            raise ValueError(f"weight must be positive, got {self.value}")

    @classmethod
    def of(cls, value: float | str | Decimal, unit: WeightUnit = "oz") -> Weight:
        return cls(Decimal(str(value)), unit)

    @property
    def grams(self) -> Decimal:
        return self.value * _TO_GRAMS[self.unit]

    def to(self, unit: WeightUnit, places: int = 4) -> Decimal:
        """Value expressed in `unit`."""
        return _q(self.grams / _TO_GRAMS[unit], places)

    def __add__(self, other: Weight) -> Weight:
        """Sum in this weight's unit — needed when aggregating fanned-out parcels."""
        return Weight(_q(self.to(self.unit) + other.to(self.unit), 4), self.unit)


@dataclass(frozen=True, slots=True)
class Dimensions:
    """Box dimensions with their unit. Order is length, width, height."""

    length: Decimal
    width: Decimal
    height: Decimal
    unit: LengthUnit = "in"

    def __post_init__(self) -> None:
        if self.unit not in _TO_MM:
            raise ValueError(f"unknown length unit {self.unit!r}; expected one of {sorted(_TO_MM)}")
        for name in ("length", "width", "height"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive, got {getattr(self, name)}")

    @classmethod
    def of(
        cls,
        length: float | str | Decimal,
        width: float | str | Decimal,
        height: float | str | Decimal,
        unit: LengthUnit = "in",
    ) -> Dimensions:
        return cls(Decimal(str(length)), Decimal(str(width)), Decimal(str(height)), unit)

    def to(self, unit: LengthUnit, places: int = 2) -> tuple[Decimal, Decimal, Decimal]:
        factor = _TO_MM[self.unit] / _TO_MM[unit]
        return (
            _q(self.length * factor, places),
            _q(self.width * factor, places),
            _q(self.height * factor, places),
        )
