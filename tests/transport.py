"""A recording transport for tests.

The adapters used to be tested by reassigning each provider module's `request`
global. That seam had two problems: it needed a `# type: ignore` per use, and it
swallowed every exception, so a genuine failure inside the adapter looked
identical to a captured payload.

`RecordingTransport` is the supported seam instead. It records each outgoing
request and replies from a queued script, so a test asserts on real bytes and any
unexpected error still propagates.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from shipzil.http import HttpRequest, HttpResponse


class RecordingTransport:
    """Captures requests and replays scripted responses.

    `responses` is consumed in order. When it runs out, or is empty, every further
    call answers `default`. That lets a payload test ignore the response entirely
    while a parser test scripts an exact body.
    """

    def __init__(
        self,
        responses: Sequence[HttpResponse | dict | tuple[int, dict]] = (),
        *,
        default: HttpResponse | dict | tuple[int, dict] | None = None,
    ) -> None:
        self.requests: list[HttpRequest] = []
        self._queue = [self._coerce(r) for r in responses]
        self._default = self._coerce(default if default is not None else {})

    @staticmethod
    def _coerce(value: HttpResponse | dict | tuple[int, dict]) -> HttpResponse:
        if isinstance(value, HttpResponse):
            return value
        if isinstance(value, tuple):
            status, body = value
            return HttpResponse(status=status, body=json.dumps(body).encode())
        return HttpResponse(status=200, body=json.dumps(value).encode())

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self._queue.pop(0) if self._queue else self._default

    # ── assertions ──────────────────────────────────────────────────

    @property
    def bodies(self) -> list[object]:
        """Every JSON body sent, decoded."""
        return [
            json.loads(r.body) for r in self.requests if r.body
        ]

    @property
    def blob(self) -> str:
        """Every JSON body sent, as one string, for substring assertions."""
        return json.dumps(self.bodies)

    @property
    def urls(self) -> list[str]:
        return [r.url for r in self.requests]
