"""Tests for the controller-event transcript diagnostics bridge."""

from __future__ import annotations

from dataclasses import dataclass
import unittest

from ruida_re.diagnostics import (
    TranscriptObserver,
    UnsupportedDiagnosticEvent,
)
from ruida_re.controller import ExchangeEvent
from ruida_re.transcript import Endpoint, Transcript
from ruida_re.transport import encode_datagram


@dataclass(frozen=True)
class Event:
    direction: str
    link: str
    context: str | None
    raw: bytes
    timestamp: float
    exchange_context: str | None = None


class TranscriptObserverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.host = Endpoint("192.0.2.20", 40200)
        self.controller = Endpoint("192.0.2.10", 50200)

    def observer(self, **values: object) -> TranscriptObserver:
        return TranscriptObserver(
            host=self.host,
            controller=self.controller,
            **values,
        )

    def test_outbound_event_preserves_raw_datagram_and_endpoints(
        self,
    ) -> None:
        raw = encode_datagram(bytes.fromhex("da000001"), "request")
        observer = self.observer()
        datagram = observer.capture(
            Event("send", "udp", "request", raw, 12.5)
        )
        self.assertIsNotNone(datagram)
        self.assertEqual(datagram.raw_bytes(), raw)
        self.assertEqual(datagram.direction, "outbound")
        self.assertEqual(datagram.context, "request")
        self.assertEqual(datagram.source, self.host)
        self.assertEqual(datagram.destination, self.controller)
        self.assertEqual(datagram.timestamp, 12.5)

    def test_inbound_uses_wire_context_not_exchange_context(self) -> None:
        raw = encode_datagram(b"\xcc", "reply")
        observer = self.observer()
        datagram = observer.capture(
            Event(
                "receive",
                "udp",
                "reply",
                raw,
                13.0,
                exchange_context="request",
            )
        )
        self.assertIsNotNone(datagram)
        self.assertEqual(datagram.raw_bytes(), raw)
        self.assertEqual(datagram.direction, "inbound")
        self.assertEqual(datagram.context, "reply")
        self.assertEqual(datagram.source, self.controller)
        self.assertEqual(datagram.destination, self.host)

    def test_callback_appends_to_supplied_transcript_in_wire_order(
        self,
    ) -> None:
        transcript = Transcript()
        observer = self.observer(transcript=transcript)
        request = encode_datagram(b"\xce", "request")
        reply = encode_datagram(b"\xcc", "reply")
        observer(
            ExchangeEvent(
                direction="send",
                phase="send",
                link="udp",
                context="request",
                exchange_context="request",
                raw=request,
                logical=b"\xce",
                timestamp=1.0,
            )
        )
        observer(
            ExchangeEvent(
                direction="receive",
                phase="acknowledgement",
                link="udp",
                context="reply",
                exchange_context="request",
                raw=reply,
                logical=b"\xcc",
                timestamp=2.0,
            )
        )
        self.assertIs(observer.transcript, transcript)
        self.assertEqual(transcript.raw_datagrams(), (request, reply))
        restored = Transcript.from_json(transcript.to_json())
        self.assertEqual(restored.raw_datagrams(), (request, reply))

    def test_malformed_udp_datagram_is_still_preserved_exactly(self) -> None:
        raw = bytearray(encode_datagram(b"\xce", "request"))
        raw[-1] ^= 1
        observer = self.observer()
        datagram = observer.capture(
            Event("send", "udp", "request", bytes(raw), 1.0)
        )
        self.assertIsNotNone(datagram)
        self.assertEqual(datagram.raw_bytes(), bytes(raw))
        self.assertIsNone(datagram.program)
        self.assertTrue(datagram.issues)

    def test_non_udp_event_is_rejected_by_default(self) -> None:
        observer = self.observer()
        event = Event("send", "serial", "request", b"\x01", 1.0)
        with self.assertRaisesRegex(
            UnsupportedDiagnosticEvent,
            "non-UDP",
        ):
            observer.capture(event)
        self.assertEqual(observer.transcript.datagrams, [])

    def test_non_udp_event_can_be_explicitly_ignored(self) -> None:
        observer = self.observer(non_udp="ignore")
        event = Event("send", "custom", "request", b"\x01", 1.0)
        self.assertIsNone(observer.capture(event))
        self.assertEqual(observer.transcript.datagrams, [])

    def test_invalid_wire_mapping_is_rejected_without_guessing(self) -> None:
        observer = self.observer()
        events = (
            Event("send", "udp", "reply", b"\x00", 1.0),
            Event("receive", "udp", "request", b"\x00", 1.0),
            Event("receive", "udp", None, b"\x00", 1.0),
            Event("other", "udp", "reply", b"\x00", 1.0),
        )
        for event in events:
            with self.subTest(event=event):
                with self.assertRaises(UnsupportedDiagnosticEvent):
                    observer.capture(event)
        self.assertEqual(observer.transcript.datagrams, [])

    def test_constructor_requires_explicit_valid_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "Host"):
            TranscriptObserver(
                host=None,
                controller=self.controller,
            )
        with self.assertRaisesRegex(ValueError, "Controller"):
            TranscriptObserver(
                host=self.host,
                controller=None,
            )
        with self.assertRaisesRegex(ValueError, "Magic"):
            self.observer(magic=True)
        with self.assertRaisesRegex(ValueError, "non_udp"):
            self.observer(non_udp="capture")


if __name__ == "__main__":
    unittest.main()
