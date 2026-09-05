"""Synchronous HTTP policy with a replaceable byte transport.

The default transport uses the standard library. Provider operations are
request/response; asynchronous label generation is polled with a bounded timeout.

Two layers, so swapping one does not lose the other:

* `Transport` moves bytes and nothing else. Substitute it to add logging, tracing,
  a proxy, connection pooling, or a recorded cassette.
* `request()` is policy — retries, backoff, and mapping provider error envelopes
  onto the shipzil exception hierarchy. It is kept out of the transport so a
  custom transport still gets correct error handling.

Cloudflare sits in front of at least one provider (Easyship) and rejects default
Python user-agents with a 403 that looks like an auth failure, so a real
User-Agent is always sent.

`UrllibTransport` does not pool connections; `urllib` has no keep-alive. It is
safe to share across threads because it holds no state. Supply a transport backed
by `requests` or `httpx` if you want pooling.
"""

from __future__ import annotations

import json as _json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from .errors import AuthenticationError, ProviderError, RateLimitError, ValidationError

__all__ = [
    "HttpRequest",
    "HttpResponse",
    "Transport",
    "UrllibTransport",
    "request",
]

USER_AGENT = "shipzil/0.1 (+https://shipzil.com)"

# Retried only for methods that are safe to repeat. Label purchases are never
# retried here: a repeat could buy postage twice, and shipzil offers no
# idempotency key to make that safe. Purchases pass retries=0.
_RETRY_STATUSES = frozenset({429, 502, 503, 504})


@dataclass(frozen=True)
class HttpRequest:
    """One outgoing request, already serialised."""

    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes | None = None
    timeout: float = 60.0


@dataclass(frozen=True)
class HttpResponse:
    """One raw response. Parsing and error mapping happen above the transport."""

    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


class Transport(Protocol):
    """Moves bytes. The seam for logging, tracing, proxies, pooling or replay.

    An implementation returns an `HttpResponse` for any HTTP status, including
    4xx and 5xx, and raises only for genuine transport failure such as DNS or a
    connection reset. Interpreting a status is `request()`'s job.
    """

    def send(self, request: HttpRequest) -> HttpResponse: ...


class UrllibTransport:
    """The default transport: `urllib` from the standard library.

    Stateless, so one instance is safe to share across threads. No connection
    pooling, because `urllib` offers none.
    """

    def send(self, request: HttpRequest) -> HttpResponse:
        req = urllib.request.Request(
            request.url,
            data=request.body,
            headers=dict(request.headers),
            method=request.method.upper(),
        )
        try:
            with urllib.request.urlopen(req, timeout=request.timeout) as resp:
                return HttpResponse(
                    status=resp.status,
                    body=resp.read(),
                    headers=dict(resp.headers.items()),
                )
        except urllib.error.HTTPError as exc:
            # An HTTP error is still a response: the body usually carries the only
            # statement of what went wrong.
            return HttpResponse(
                status=exc.code,
                body=exc.read(),
                headers=dict(exc.headers.items()) if exc.headers else {},
            )


#: Shared default. Stateless, so sharing it costs nothing and avoids allocating
#: one per adapter call.
DEFAULT_TRANSPORT: Transport = UrllibTransport()


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json: Any = None,
    timeout: float = 60.0,
    provider: str = "",
    retries: int = 2,
    backoff: float = 0.6,
    idempotent: bool = False,
    transport: Transport | None = None,
) -> tuple[int, Any]:
    """Perform a request, returning `(status, parsed_body)`.

    Non-2xx statuses raise. A body that is not JSON is returned as text so the
    caller can still see what happened.

    `idempotent=True` marks a call as safe to repeat, which allows retrying 429s
    and gateway errors on a POST. Rate quotes qualify — Easyship's sandbox
    enforces a per-second limit that a single quote can trip. **Label purchases
    never qualify**, because a repeat may buy postage twice.
    """
    hdrs = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if json is not None:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    body = _json.dumps(json).encode() if json is not None else None
    sender = transport if transport is not None else DEFAULT_TRANSPORT
    outgoing = HttpRequest(
        method=method, url=url, headers=hdrs, body=body, timeout=timeout
    )

    last: Exception | str | None = None
    for attempt in range(retries + 1):
        try:
            response = sender.send(outgoing)
        except OSError as exc:
            # Transport-level failure: DNS, refused connection, reset, timeout.
            if attempt < retries:
                last = exc
                time.sleep(backoff * (2**attempt))
                continue
            raise ProviderError(f"network error: {exc}", provider=provider) from exc

        parsed = _parse(response.body)
        if 200 <= response.status < 300:
            return response.status, parsed

        safe = idempotent or method.upper() in {"GET", "HEAD"}
        retryable = response.status in _RETRY_STATUSES or (
            response.status == 403 and _is_quota(_describe(parsed))
        )
        if retryable and attempt < retries and safe:
            last = f"HTTP {response.status}"
            time.sleep(_delay(response, backoff, attempt))
            continue
        _raise_for_status(response.status, parsed, provider)

    raise ProviderError(f"request failed after {retries + 1} attempts: {last}", provider=provider)


def _delay(response: HttpResponse, backoff: float, attempt: int) -> float:
    """Honour Retry-After when the server sets it, else exponential backoff."""
    header = response.headers.get("Retry-After") if response.headers else None
    if header:
        try:
            return min(float(header), 30.0)
        except ValueError:
            pass
    return backoff * (2**attempt)


def _parse(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return _json.loads(raw)
    except ValueError:
        return raw.decode(errors="replace")


#: Quota exhaustion does not reliably arrive as a 429. Easyship answers a
#: spent plan allowance with **403 Forbidden** and "API usage limit exceeded",
#: which naively reads as a bad API key and sends the caller off to rotate a
#: credential that was never the problem. Match on the message instead.
_QUOTA_PHRASES = (
    "usage limit",
    "quota",
    "rate limit",
    "too many requests",
    "requests per",
    "upgrade your plan",
    "limit exceeded",
)


def _is_quota(detail: str) -> bool:
    low = detail.lower()
    return any(phrase in low for phrase in _QUOTA_PHRASES)


def _raise_for_status(status: int, parsed: Any, provider: str) -> None:
    detail = _describe(parsed)
    if status in (401, 403):
        if _is_quota(detail):
            raise RateLimitError(detail, provider=provider)
        raise AuthenticationError(detail or "credentials rejected", provider=provider)
    if status == 429:
        raise RateLimitError(detail or "rate limited", provider=provider)
    if status in (400, 422):
        raise ValidationError(detail or "request rejected", provider=provider)
    raise ProviderError(detail or f"HTTP {status}", provider=provider, status=status, raw=parsed)


def _describe(parsed: Any) -> str:
    """Extract a message from the supported provider error envelopes."""
    if parsed is None:
        return ""
    if isinstance(parsed, str):
        return parsed[:400]
    if isinstance(parsed, dict):
        # Easyship: {"error": {"message": ..., "details": [...]}}
        err = parsed.get("error")
        if isinstance(err, dict):
            parts = [str(err.get("message") or "")]
            details = err.get("details")
            if isinstance(details, list):
                parts += [str(d) for d in details]
            return "; ".join(p for p in parts if p)[:600]
        if isinstance(err, str):
            return err[:400]
        # ShipStation v1: {"Message": ..., "ModelState": {...}}
        if "Message" in parsed:
            out = str(parsed["Message"])
            state = parsed.get("ModelState")
            if isinstance(state, dict):
                out += "; " + "; ".join(f"{k}: {v}" for k, v in state.items())
            return out[:600]
        # ShipEngine / ShipStation v2: {"errors": [{"message": ...}]}
        errors = parsed.get("errors")
        if isinstance(errors, list) and errors:
            texts = [str(e.get("message") if isinstance(e, dict) else e) for e in errors]
            return "; ".join(texts)[:600]
        if "detail" in parsed:
            return str(parsed["detail"])[:400]
    return str(parsed)[:400]
