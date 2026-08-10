"""Adapters from wire exchange events to lossless UDP transcripts."""

from __future__ import annotations

import threading
from typing import Literal, Protocol

from .transcript import Datagram, Endpoint, Transcript


__all__ = (
    "DiagnosticEvent",
    "NonUdpPolicy",
    "TranscriptObserver",
    "UnsupportedDiagnosticEvent",
)


NonUdpPolicy = Literal["reject", "ignore"]


class DiagnosticEvent(Protocol):
    """Structural wire-event contract consumed by diagnostics adapters."""

    direction: str
    link: str
    context: str | None
    raw: bytes
    timestamp: int | float


class UnsupportedDiagnosticEvent(ValueError):
    """Raised when an event cannot be represented without guessing."""


class TranscriptObserver:
    """Record exact UDP exchange events in a :class:`Transcript`."""

    def __init__(
        self,
        *,
        host: Endpoint,
        controller: Endpoint,
        transcript: Transcript | None = None,
        magic: int = 0x88,
        non_udp: NonUdpPolicy = "reject",
    ) -> None:
        if not isinstance(host, Endpoint):
            raise ValueError("Host must be an Endpoint")
        if not isinstance(controller, Endpoint):
            raise ValueError("Controller must be an Endpoint")
        if transcript is not None and not isinstance(transcript, Transcript):
            raise ValueError("Transcript must be a Transcript")
        if type(magic) is not int or not 0 <= magic <= 0xFF:
            raise ValueError("Magic value must fit in one byte")
        if non_udp not in ("reject", "ignore"):
            raise ValueError("non_udp must be 'reject' or 'ignore'")
        self.host = host
        self.controller = controller
        self.transcript = transcript or Transcript()
        self.magic = magic
        self.non_udp = non_udp
        self._lock = threading.Lock()

    def __call__(self, event: DiagnosticEvent) -> None:
        """Record an event when used as a controller observer callback."""
        self.capture(event)

    def capture(self, event: DiagnosticEvent) -> Datagram | None:
        """Capture one event, returning its datagram or an explicit skip."""
        if event.link != "udp":
            return self._handle_non_udp(event.link)
        direction, context = self._wire_mapping(
            event.direction,
            event.context,
        )
        if not isinstance(event.raw, bytes):
            raise UnsupportedDiagnosticEvent(
                "UDP diagnostic event raw data must be bytes"
            )
        source, destination = self._endpoints(direction)
        with self._lock:
            return self.transcript.capture(
                event.raw,
                direction,
                context,
                timestamp=event.timestamp,
                source=source,
                destination=destination,
                magic=self.magic,
            )

    def _handle_non_udp(self, link: str) -> None:
        if self.non_udp == "ignore":
            return None
        raise UnsupportedDiagnosticEvent(
            f"Cannot record non-UDP diagnostic event for link {link!r}"
        )

    @staticmethod
    def _wire_mapping(
        direction: str,
        context: str | None,
    ) -> tuple[str, str]:
        if direction == "send" and context in ("job", "request"):
            return "outbound", context
        if direction == "receive" and context == "reply":
            return "inbound", context
        raise UnsupportedDiagnosticEvent(
            "Diagnostic event has no valid UDP direction/context mapping: "
            f"{direction!r}/{context!r}"
        )

    def _endpoints(self, direction: str) -> tuple[Endpoint, Endpoint]:
        if direction == "outbound":
            return self.host, self.controller
        return self.controller, self.host
