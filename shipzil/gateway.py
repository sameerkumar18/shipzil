"""The multi-provider Gateway.

`Client` stays bound to one adapter. `Gateway` is the thin layer above it: it
knows which configured sources to call, filters candidates by explicit merchant
input, and keeps the source that produced each rate. It does not choose a best
rate, consult health data or infer service equivalence.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from decimal import Decimal

from ._client import Client
from .errors import ConfigurationError, ShipzilError
from .models import Exclusion, ExclusionCode, Label, Rate, Shipment
from .providers import REGISTRY
from .providers.base import Adapter
from .services import ServiceKey, ServiceMap, carrier_matches

__all__ = ["Gateway", "GatewayQuote", "SourceResult"]


@dataclass(frozen=True)
class SourceResult:
    """One configured source's outcome inside an aggregate Gateway call.

    Flat on purpose: `result.rates` rather than `result.quote.rates`. A source
    either answered or failed, and `error` says which.
    """

    source: str
    provider: str
    rates: tuple[Rate, ...] = ()
    excluded: tuple[Exclusion, ...] = ()
    messages: tuple[str, ...] = ()
    #: The provider surface that answered, e.g. "shippo:shipments". For debugging.
    via: str = ""
    error: ShipzilError | None = None

    @property
    def ok(self) -> bool:
        """Whether this source answered without raising."""
        return self.error is None


@dataclass(frozen=True)
class GatewayQuote:
    """Rates and diagnostics from one or more configured sources.

    Sequence operations apply to `rates`:

        if quote:                      # any rates at all?
        for rate in quote: ...         # iterate the rates
        len(quote)                     # how many
        quote.cheapest                 # None when there are no rates
        print(quote.explain())         # why is something missing?
    """

    rates: tuple[Rate, ...] = ()
    sources: tuple[SourceResult, ...] = ()
    services: ServiceMap = field(default_factory=ServiceMap)
    #: Everything that did not come back as a rate, and why. Aggregates each
    #: source's own exclusions plus anything the caller's filters removed.
    excluded: tuple[Exclusion, ...] = ()

    @property
    def errors(self) -> tuple[ShipzilError, ...]:
        """Provider failures that did not prevent other sources from answering."""
        return tuple(result.error for result in self.sources if result.error is not None)

    @property
    def messages(self) -> tuple[str, ...]:
        """Provider messages retained from every source that answered."""
        return tuple(message for result in self.sources for message in result.messages)

    @property
    def cheapest(self) -> Rate | None:
        """Lowest amount when all rates share one known currency, otherwise None."""
        currencies = {rate.currency for rate in self.rates}
        if len(currencies) != 1 or None in currencies:
            return None
        return min(self.rates, key=lambda r: r.amount) if self.rates else None

    @property
    def fastest(self) -> Rate | None:
        """Fewest delivery days among rates that report one, or None."""
        timed = [r for r in self.rates if r.delivery_days is not None]
        return min(timed, key=lambda r: r.delivery_days or 0) if timed else None

    def __bool__(self) -> bool:
        return bool(self.rates)

    def __len__(self) -> int:
        return len(self.rates)

    def __iter__(self) -> Iterator[Rate]:
        return iter(self.rates)

    def __getitem__(self, index: int) -> Rate:
        return self.rates[index]

    def explain(self) -> str:
        """Return a human-readable summary of rates, failures and exclusions.

        Exclusions are grouped by reason and carrier. The individual `Exclusion`
        objects stay one-per-rate in `excluded`; this only condenses the display,
        because a routine carrier filter can legitimately exclude dozens of rates.
        """
        lines = [f"{len(self.rates)} rate(s) from {len(self.sources)} source(s)"]
        for result in self.sources:
            if result.error is not None:
                lines.append(
                    f"  {result.source} ({result.provider}) failed: {result.error}"
                )
            else:
                lines.append(
                    f"  {result.source}: {len(result.rates)} rate(s) via "
                    f"{result.via or 'unknown'}"
                )

        grouped: dict[tuple[str, str, str], list[Exclusion]] = {}
        for exc in self.excluded:
            grouped.setdefault(
                (exc.code.value, exc.carrier or "provider", exc.source), []
            ).append(exc)

        for (code, who, source), group in grouped.items():
            tag = "" if source == "provider" else " [shipzil]"
            if len(group) == 1:
                lines.append(f"  excluded {who}: {code}{tag} — {group[0].message}")
            else:
                lines.append(
                    f"  excluded {who}: {code}{tag} — {len(group)} rate(s), "
                    f"e.g. {group[0].message}"
                )
        return "\n".join(lines)


class Gateway:
    """Call several configured provider sources without making decisions for you.

    The short form takes a credential per provider, which is all most callers need:

        gateway = shipzil.Gateway(shippo="shippo_test_...", easyship="...")

    The source name defaults to the provider name. Pass a mapping instead when you
    want to name sources yourself, run two accounts on one provider, or configure an
    adapter (timeout, transport):

        gateway = shipzil.Gateway({
            "shippo-us": ShippoAdapter(us_token),
            "shippo-eu": ShippoAdapter(eu_token),
        })

    With no `fallback`, every eligible source is called concurrently and the rates
    are combined. With `fallback=(...)`, sources are tried in that explicit order and
    the first source with a matching rate wins. That is caller-authored policy, not
    routing intelligence. A purchase never falls back automatically.
    """

    #: Source name → adapter, as resolved from either form of the constructor.
    sources: dict[str, Adapter]
    #: Explicit source order, or None to query every source concurrently.
    fallback: tuple[str, ...] | None
    #: Refuse to buy a rate above this. Checked before any network call.
    max_spend: Decimal | float | str | None
    #: Return a synthetic label instead of contacting a purchase endpoint.
    dry_run: bool
    #: Upper bound on concurrent source calls. None means one worker per source.
    max_workers: int | None

    def __init__(
        self,
        sources: Mapping[str, Adapter] | None = None,
        *,
        fallback: Sequence[str] | None = None,
        max_spend: Decimal | float | str | None = None,
        dry_run: bool = False,
        max_workers: int | None = None,
        **credentials: str | Sequence[str],
    ) -> None:
        resolved = self._resolve_sources(sources, credentials)
        if not resolved:
            raise ConfigurationError(
                "Gateway needs at least one provider source, e.g. "
                'Gateway(shippo="shippo_test_...")'
            )
        self.sources = resolved
        if any(not name.strip() for name in self.sources):
            raise ConfigurationError("Gateway source names cannot be empty")
        self.fallback = tuple(fallback) if fallback is not None else None
        self.max_spend = max_spend
        self.dry_run = dry_run
        if max_workers is not None and max_workers < 1:
            raise ConfigurationError("max_workers must be at least 1")
        self.max_workers = max_workers
        self._validate_fallback()

    @staticmethod
    def _resolve_sources(
        sources: Mapping[str, Adapter] | None,
        credentials: Mapping[str, str | Sequence[str]],
    ) -> dict[str, Adapter]:
        """Accept either explicit adapters or a credential per provider, not both.

        A provider needing more than one secret takes a sequence, because
        ShipStation v1 authenticates with a key **and** a secret:

            Gateway(shipstation_v1=("key", "secret"))
        """
        if sources is not None and credentials:
            raise ConfigurationError(
                "pass either a sources mapping or provider credentials, not both"
            )
        if sources is not None:
            return dict(sources)

        unknown = sorted(set(credentials) - set(REGISTRY))
        if unknown:
            raise ConfigurationError(
                f"unknown provider(s): {', '.join(unknown)}. "
                f"Available: {', '.join(sorted(REGISTRY))}"
            )

        built: dict[str, Adapter] = {}
        for name, credential in credentials.items():
            args = (credential,) if isinstance(credential, str) else tuple(credential)
            try:
                built[name] = REGISTRY[name](*args)
            except TypeError as exc:
                raise ConfigurationError(
                    f"{name} could not be built from the credential given ({exc}). "
                    f"Providers needing more than one secret take a sequence, e.g. "
                    f'Gateway({name}=("key", "secret")). Construct the adapter '
                    f"directly if you need other options."
                ) from exc
        return built

    def _validate_fallback(self) -> None:
        if self.fallback is None:
            return
        missing = [name for name in self.fallback if name not in self.sources]
        if missing:
            raise ConfigurationError(
                f"Gateway fallback contains unknown sources: {', '.join(missing)}"
            )
        if len(set(self.fallback)) != len(self.fallback):
            raise ConfigurationError("Gateway fallback sources must be unique")

    def _eligible_sources(self, providers: Iterable[str] | None) -> list[str]:
        if providers is None:
            allowed = None
        else:
            allowed = {provider.strip().lower() for provider in providers}

        names = list(self.fallback) if self.fallback is not None else list(self.sources)
        out = []
        for name in names:
            adapter = self.sources[name]
            if (
                allowed is not None
                and name.lower() not in allowed
                and adapter.name.lower() not in allowed
            ):
                continue
            out.append(name)
        if not out:
            raise ConfigurationError("provider selection does not match any configured source")
        return out

    def _validate_services(
        self,
        services: frozenset[ServiceKey] | None,
        providers: Iterable[str] | None,
    ) -> None:
        if services is None:
            return
        configured = {adapter.name.lower() for adapter in self.sources.values()}
        missing = sorted({key.provider for key in services} - configured)
        if missing:
            raise ConfigurationError(
                "service selection names unconfigured providers: " + ", ".join(missing)
            )
        if providers is not None:
            allowed = {provider.strip().lower() for provider in providers}
            conflict = sorted({key.provider for key in services} - allowed)
            if conflict:
                raise ConfigurationError(
                    "service and provider selections conflict: " + ", ".join(conflict)
                )

    @staticmethod
    def _keys(services: Iterable[ServiceKey | str] | None) -> frozenset[ServiceKey] | None:
        if services is None:
            return None
        out: set[ServiceKey] = set()
        for service in services:
            out.add(service if isinstance(service, ServiceKey) else ServiceKey.parse(service))
        return frozenset(out)

    @staticmethod
    def _filter_rates(
        rates: Iterable[Rate],
        *,
        carriers: Iterable[str] | None,
        services: frozenset[ServiceKey] | None,
    ) -> tuple[tuple[Rate, ...], tuple[Exclusion, ...]]:
        """Apply explicit filters with AND semantics, reporting every drop.

        Returns `(kept, excluded)`. Nothing is ever removed silently: a rate the
        caller's own filter excluded, and a rate shipzil could not address at all,
        both come back as an `Exclusion`. An unexplained absence of rates is the
        failure this library exists to prevent, and a filter is not exempt from it.
        """
        rates = tuple(rates)
        if carriers is None and services is None:
            return rates, ()

        carrier_list = list(carriers) if carriers is not None else None
        kept: list[Rate] = []
        dropped: list[Exclusion] = []

        for rate in rates:
            key = rate.service_key
            if key is None:
                dropped.append(
                    Exclusion(
                        code=ExclusionCode.SERVICE_NOT_ADDRESSABLE,
                        message=(
                            f"{rate.provider} returned {rate.carrier!r}/{rate.service!r} "
                            "with no carrier shipzil could resolve, so it cannot be "
                            "matched against your filter and was not returned."
                        ),
                        carrier=rate.carrier or None,
                        service=rate.service or None,
                        source="shipzil",
                    )
                )
                continue

            if carrier_list is not None and not any(
                carrier_matches(requested, key.carrier) for requested in carrier_list
            ):
                dropped.append(
                    Exclusion(
                        code=ExclusionCode.FILTERED_BY_REQUEST,
                        message=(
                            f"{key.slug} carried by {key.carrier!r}, which is not in "
                            f"carriers={sorted(carrier_list)!r}."
                        ),
                        carrier=key.carrier,
                        service=rate.service or None,
                        source="shipzil",
                    )
                )
                continue

            if services is not None and key not in services:
                dropped.append(
                    Exclusion(
                        code=ExclusionCode.FILTERED_BY_REQUEST,
                        message=(
                            f"{key.slug} is not in the requested services "
                            f"{sorted(k.slug for k in services)!r}."
                        ),
                        carrier=key.carrier,
                        service=rate.service or None,
                        source="shipzil",
                    )
                )
                continue

            kept.append(rate)

        return tuple(kept), tuple(dropped)

    def _rate_one_source(
        self,
        source_name: str,
        shipment: Shipment,
        carriers: Iterable[str] | None,
        keys: frozenset[ServiceKey] | None,
    ) -> tuple[SourceResult, tuple[Exclusion, ...]]:
        """Rate one source. Never raises `ShipzilError`; returns it as a result.

        A provider failure is data, not control flow: the other sources still have
        answers. Anything that is not a `ShipzilError` propagates, because that is a
        bug in shipzil and must not be laundered into "this source was unavailable".
        """
        adapter = self.sources[source_name]
        client = Client(
            adapter,
            max_spend=self.max_spend,
            dry_run=self.dry_run,
            max_workers=self.max_workers,
        )
        try:
            quote = client.get_rates(shipment)
        except ShipzilError as error:
            return SourceResult(source=source_name, provider=adapter.name, error=error), ()

        annotated = tuple(replace(rate, source=source_name) for rate in quote.rates)
        filtered, dropped = self._filter_rates(annotated, carriers=carriers, services=keys)
        result = SourceResult(
            source=source_name,
            provider=adapter.name,
            rates=filtered,
            excluded=(*quote.excluded, *dropped),
            messages=quote.messages,
            via=quote.via,
        )
        return result, (*quote.excluded, *dropped)

    def get_rates(
        self,
        shipment: Shipment,
        *,
        providers: Iterable[str] | None = None,
        carriers: Iterable[str] | None = None,
        services: Iterable[ServiceKey | str] | None = None,
    ) -> GatewayQuote:
        """Rate through all eligible sources, or the configured fallback order.

        With no `fallback`, sources are called **concurrently** and the results are
        assembled in configured-source order, so output does not depend on which
        provider answered first.

        With a `fallback`, calls stay strictly sequential: the whole point is to
        stop at the first source that answers, and racing them would defeat that
        and spend quota on providers the caller ranked lower.
        """
        keys = self._keys(services)
        self._validate_services(keys, providers)
        eligible = self._eligible_sources(providers)

        if self.fallback is not None:
            return self._rate_sequentially(eligible, shipment, carriers, keys)
        return self._rate_concurrently(eligible, shipment, carriers, keys)

    def _rate_sequentially(
        self,
        eligible: list[str],
        shipment: Shipment,
        carriers: Iterable[str] | None,
        keys: frozenset[ServiceKey] | None,
    ) -> GatewayQuote:
        results: list[SourceResult] = []
        excluded: list[Exclusion] = []
        for source_name in eligible:
            result, dropped = self._rate_one_source(source_name, shipment, carriers, keys)
            results.append(result)
            excluded.extend(dropped)
            if result.rates:
                break
        return self._assemble(results, excluded)

    def _rate_concurrently(
        self,
        eligible: list[str],
        shipment: Shipment,
        carriers: Iterable[str] | None,
        keys: frozenset[ServiceKey] | None,
    ) -> GatewayQuote:
        if len(eligible) == 1:
            result, dropped = self._rate_one_source(eligible[0], shipment, carriers, keys)
            return self._assemble([result], list(dropped))

        workers = self.max_workers or len(eligible)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="shipzil") as pool:
            # Keyed by source rather than collected by completion order, so the
            # result is reproducible run to run.
            futures = {
                name: pool.submit(self._rate_one_source, name, shipment, carriers, keys)
                for name in eligible
            }
            pairs = [futures[name].result() for name in eligible]

        results = [result for result, _ in pairs]
        excluded = [exc for _, dropped in pairs for exc in dropped]
        return self._assemble(results, excluded)

    @staticmethod
    def _assemble(
        results: list[SourceResult], excluded: list[Exclusion]
    ) -> GatewayQuote:
        rates: list[Rate] = []
        service_map = ServiceMap()
        for result in results:
            for rate in result.rates:
                rates.append(rate)
                service_map.add_rate(rate)
        return GatewayQuote(tuple(rates), tuple(results), service_map, tuple(excluded))

    def buy(self, shipment: Shipment, rate: Rate) -> Label:
        """Buy exactly the source-specific rate supplied by the caller."""
        if not rate.source:
            raise ConfigurationError("Gateway.buy needs a rate returned by Gateway.get_rates")
        adapter = self.sources.get(rate.source)
        if adapter is None:
            raise ConfigurationError(f"rate came from unknown Gateway source {rate.source!r}")
        if rate.provider != adapter.name:
            raise ConfigurationError(
                f"rate provider {rate.provider!r} does not match source "
                f"{rate.source!r} ({adapter.name!r})"
            )
        label = Client(adapter, max_spend=self.max_spend, dry_run=self.dry_run).buy(
            shipment, rate
        )
        return replace(label, source=rate.source)

    def void(self, label: Label) -> bool:
        """Void through the source that bought the label."""
        if not label.source or label.source not in self.sources:
            raise ConfigurationError("label has no known Gateway source")
        adapter = self.sources[label.source]
        return Client(adapter, dry_run=self.dry_run).void(label)
