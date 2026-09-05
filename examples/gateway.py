"""Rate a shipment through the Gateway without buying postage.

Run with:

    SHIPPO_TEST_TOKEN=shippo_test_... uv run python examples/gateway.py

Pass `--buy` only when you intentionally want a test label.
"""

from __future__ import annotations

import os
import sys

import shipzil as z
from shipzil.providers import ShippoAdapter


def main() -> int:
    token = os.environ.get("SHIPPO_TEST_TOKEN")
    if not token:
        print("set SHIPPO_TEST_TOKEN before running this example", file=sys.stderr)
        return 2

    sender = z.Address(
        street1="215 Clayton St",
        city="San Francisco",
        state="CA",
        postal_code="94117",
        country="US",
        name="Example Sender",
        phone="4155550100",
        email="sender@example.com",
    )
    recipient = z.Address(
        street1="1600 Pennsylvania Ave NW",
        city="Washington",
        state="DC",
        postal_code="20500",
        country="US",
        name="Example Recipient",
        phone="2025550100",
        email="recipient@example.com",
    )
    shipment = z.Shipment(
        sender,
        recipient,
        (
            z.Parcel(
                weight=z.Weight.of(16, "oz"),
                dimensions=z.Dimensions.of(10, 8, 4, "in"),
            ),
        ),
    )

    gateway = z.Gateway({"shippo-test": ShippoAdapter(token)}, dry_run="--buy" not in sys.argv)
    quote = gateway.get_rates(shipment, carriers={"usps"})
    print(quote.explain())
    for rate in quote.rates:
        print(f"  {rate.source}: {rate.service_key} {rate.amount} {rate.currency}")

    if "--buy" in sys.argv and quote.rates:
        label = gateway.buy(shipment, quote.rates[0])
        print(f"test label: {label.tracking_number} {label.label_url}")
    return 0 if quote.rates else 1


if __name__ == "__main__":
    raise SystemExit(main())
