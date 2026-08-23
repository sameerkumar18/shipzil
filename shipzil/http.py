"""Minimal synchronous HTTP, standard library only.

No dependencies is a deliberate choice for a library people are asked to trust
with label purchases. Everything is synchronous: the industry works
request/response, and where a provider is genuinely async we poll rather than
hand the caller a future.

Cloudflare sits in front of at least one provider (Easyship) and rejects default
Python user-agents with a 403 that looks like an auth failure, so a real
User-Agent is always sent.
"""

from __future__ import annotations

import json as _json
import time
import urllib.error
import urllib.request
from typing import Any

from .errors import AuthenticationError, ProviderError, RateLimitError, ValidationError

USER_AGENT = "shipzil/0.1 (+https://shipzil.com)"

# Retried only for methods that are safe to repeat. Label purchases are never
# retried here: a repeat could buy postage twice, and shipzil offers no
# idempotency key to make that safe. Purchases pass retries=0.
_RETRY_STATUSES = frozenset({429, 502, 503, 504})


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

    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, _parse(resp.read())
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            parsed = _parse(raw)
            safe = idempotent or method.upper() in {"GET", "HEAD"}
            retryable = exc.code in _RETRY_STATUSES or (
                exc.code == 403 and _is_quota(_describe(parsed))
            )
            if retryable and attempt < retries and safe:
                last = exc
                time.sleep(_delay(exc, backoff, attempt))
                continue
            _raise_for_status(exc.code, parsed, provider)
        except urllib.error.URLError as exc:
            if attempt < retries:
                last = exc
                time.sleep(backoff * (2**attempt))
                continue
            raise ProviderError(f"network error: {exc.reason}", provider=provider) from exc

    raise ProviderError(f"request failed after {retries + 1} attempts: {last}", provider=provider)


def _delay(exc: urllib.error.HTTPError, backoff: float, attempt: int) -> float:
    """Honour Retry-After when the server sets it, else exponential backoff."""
    header = exc.headers.get("Retry-After") if exc.headers else None
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
    """Pull a usable message out of five different error envelope shapes."""
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
        # EasyPost: {"error": {"message": ...}} handled above; ShipStation v1:
        # {"Message": ..., "ModelState": {...}}
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
