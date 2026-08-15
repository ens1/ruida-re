"""Tests for serialized Ruida controller exchanges."""

from __future__ import annotations

import threading
import unittest
from dataclasses import FrozenInstanceError
from functools import partial

from ruida_re.codec import encode_u14, encode_u35, swizzle
from ruida_re.controller import (
    ControllerClient as ControllerClientClass,
    ControllerError,
    ControllerExchangeError,
    ControllerRejectedError,
    ControllerTimeoutError,
    ControllerTransportError,
    DeliveryCertainty,
    ExchangeEvent,
    HandshakeProfile,
    MachineStatus,
    ReplyLimitError,
    ReplyPolicy,
    SessionDesynchronizedError,
    SessionState,
    UnexpectedControllerReply,
    UnsupportedExchangeError,
)
from ruida_re.links import SerialLink
from ruida_re.transport import encode_datagram


ControllerClient = partial(
    ControllerClientClass,
    assume_synchronized=True,
)


class FakeTransport:
    def __init__(self, kind: str = "udp") -> None:
        self.kind = kind
        self.is_open = False
        self.responses: list[bytes | None | BaseException] = []
        self.on_send: list[list[bytes | None | BaseException]] = []
        self.sent: list[bytes] = []
        self.fail_close = False
        self.fail_send = False

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        if self.fail_close:
            raise OSError("close failed")
        self.is_open = False

    def send(self, data: bytes) -> None:
        if self.fail_send:
            raise OSError("send failed")
        self.sent.append(data)
        if self.on_send:
            self.responses.extend(self.on_send.pop(0))

    def receive(self, timeout: float) -> bytes | None:
        del timeout
        if not self.responses:
            return None
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def drain(self, limit: int = 256) -> tuple[bytes, ...]:
        del limit
        result = []
        while self.responses:
            item = self.receive(0.0)
            if item is not None:
                result.append(item)
        return tuple(result)


def control(value: int) -> bytes:
    return swizzle(bytes((value,)))


def setting_reply(address: int, value: int) -> bytes:
    logical = bytes.fromhex("da01")
    logical += encode_u14(address)
    logical += encode_u35(value)
    return swizzle(logical)


class ControllerClientTest(unittest.TestCase):
    def open_transport(self, kind: str = "udp") -> FakeTransport:
        transport = FakeTransport(kind)
        transport.open()
        return transport

    def test_open_drains_stale_input_then_probes(self) -> None:
        transport = FakeTransport()
        transport.responses.append(swizzle(bytes.fromhex("da01")))
        transport.on_send.append([control(0xCC)])
        events: list[ExchangeEvent] = []
        client = ControllerClientClass(transport, observer=events.append)
        client.open()
        self.assertTrue(client.is_ready)
        self.assertEqual(
            transport.sent,
            [encode_datagram(bytes.fromhex("ce"), "request")],
        )
        self.assertEqual(events[0].phase, "drain")
        self.assertEqual(events[0].context, "reply")
        self.assertIsNone(events[0].exchange_context)

    def test_open_drain_cannot_reenter_a_controller_operation(self) -> None:
        transport = FakeTransport()
        transport.responses.append(setting_reply(5, 42))
        blocked: list[BaseException] = []
        client: ControllerClientClass

        def observer(event: ExchangeEvent) -> None:
            if event.phase != "drain":
                return
            try:
                client.keep_alive()
            except BaseException as error:
                blocked.append(error)

        client = ControllerClientClass(transport, observer=observer)
        client.open(probe=False)
        self.assertTrue(client.is_ready)
        self.assertEqual(transport.sent, [])
        self.assertEqual(len(blocked), 1)
        self.assertIsInstance(blocked[0], UnsupportedExchangeError)

    def test_open_transport_is_not_assumed_synchronized(self) -> None:
        transport = self.open_transport()
        client = ControllerClientClass(transport)
        self.assertEqual(client.state, SessionState.CLOSED)
        self.assertTrue(client.is_open)
        self.assertFalse(client.is_ready)
        with self.assertRaises(ControllerError):
            client.keep_alive()
        client.open(probe=False)
        self.assertTrue(client.is_ready)

    def test_keepalive_accepts_acknowledge_or_keepalive(self) -> None:
        for response in (0xCC, 0xCE):
            with self.subTest(response=response):
                transport = self.open_transport()
                transport.on_send.append([control(response)])
                client = ControllerClient(transport)
                receipt = client.keep_alive()
                self.assertEqual(receipt.transmissions, 1)

    def test_stop_process_udp_uses_exact_wire_and_ack_receipt(self) -> None:
        transport = self.open_transport()
        transport.on_send.append([control(0xCC)])
        events: list[ExchangeEvent] = []
        client = ControllerClient(transport, observer=events.append)

        receipt = client.stop_process()

        packet = bytes.fromhex("00dbd209")
        self.assertEqual(transport.sent, [packet])
        self.assertEqual(receipt.packets, (packet,))
        self.assertEqual(receipt.transmissions, 1)
        self.assertEqual(receipt.retries, 0)
        self.assertEqual(receipt.completed_packets, 1)
        self.assertEqual(
            [event.exchange_context for event in events],
            ["request", "request"],
        )
        self.assertEqual(client.state, SessionState.READY)

    def test_stop_process_udp_retries_only_after_negative_ack(self) -> None:
        transport = self.open_transport()
        transport.on_send.extend(
            [[control(0xCF)], [control(0xCC)]]
        )
        client = ControllerClient(transport)

        receipt = client.stop_process()

        packet = bytes.fromhex("00dbd209")
        self.assertEqual(transport.sent, [packet, packet])
        self.assertEqual(receipt.packets, (packet,))
        self.assertEqual(receipt.transmissions, 2)
        self.assertEqual(receipt.retries, 1)
        self.assertEqual(receipt.completed_packets, 1)

    def test_stop_process_udp_timeout_has_unknown_delivery(self) -> None:
        transport = self.open_transport()
        client = ControllerClient(transport, acknowledge_timeout=0)

        with self.assertRaises(ControllerTimeoutError) as caught:
            client.stop_process()

        packet = bytes.fromhex("00dbd209")
        receipt = caught.exception.receipt
        self.assertEqual(transport.sent, [packet])
        self.assertEqual(receipt.packets, (packet,))
        self.assertEqual(receipt.transmissions, 1)
        self.assertEqual(receipt.retries, 0)
        self.assertEqual(receipt.completed_packets, 0)
        self.assertEqual(
            caught.exception.delivery_certainty,
            DeliveryCertainty.UNKNOWN,
        )
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_stop_process_serial_uses_exact_wire_and_write_receipt(
        self,
    ) -> None:
        transport = self.open_transport("serial")
        client = ControllerClient(transport)

        receipt = client.stop_process()

        packet = bytes.fromhex("d209")
        self.assertEqual(transport.sent, [packet])
        self.assertEqual(receipt.packets, (packet,))
        self.assertEqual(receipt.transmissions, 1)
        self.assertEqual(receipt.retries, 0)
        self.assertEqual(receipt.completed_packets, 1)
        self.assertEqual(client.state, SessionState.READY)

    def test_read_machine_status_udp_uses_exact_wire(self) -> None:
        transport = self.open_transport()
        raw_word = 0x01000043
        reply = setting_reply(0x0200, raw_word)
        transport.on_send.append([control(0xCC), reply])
        client = ControllerClient(transport)

        status = client.read_machine_status()

        self.assertEqual(transport.sent, [bytes.fromhex("0273d4898d89")])
        self.assertEqual(
            status,
            MachineStatus(
                raw_word=raw_word,
                moving=True,
                job_running=True,
                part_end=True,
                unknown_bits=0x40,
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            setattr(status, "raw_word", 0)
        self.assertEqual(client.state, SessionState.READY)

    def test_read_machine_status_serial_assembles_split_reply(self) -> None:
        transport = self.open_transport("serial")
        logical = bytes.fromhex("da0104000008000002")
        transport.on_send.append(
            [swizzle(logical[:4]), swizzle(logical[4:])]
        )
        client = ControllerClient(transport)

        status = client.read_machine_status()

        self.assertEqual(transport.sent, [bytes.fromhex("d4898d89")])
        self.assertEqual(status.raw_word, 0x01000002)
        self.assertTrue(status.moving)
        self.assertFalse(status.job_running)
        self.assertTrue(status.part_end)
        self.assertEqual(status.unknown_bits, 0)
        self.assertEqual(client.state, SessionState.READY)

    def test_read_machine_status_matches_zero_value_hardware_capture(
        self,
    ) -> None:
        transport = self.open_transport("serial")
        transport.on_send.append([bytes.fromhex("d4098d898989898989")])
        client = ControllerClient(transport)

        status = client.read_machine_status()

        self.assertEqual(
            status,
            MachineStatus(
                raw_word=0,
                moving=False,
                job_running=False,
                part_end=False,
                unknown_bits=0,
            ),
        )

    def test_read_machine_status_udp_rejects_split_datagrams(self) -> None:
        transport = self.open_transport()
        logical = bytes.fromhex("da0104000000000001")
        transport.on_send.append(
            [control(0xCC), swizzle(logical[:4]), swizzle(logical[4:])]
        )
        client = ControllerClient(transport)

        with self.assertRaises(ReplyLimitError):
            client.read_machine_status()

        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_read_machine_status_rejects_wrong_reply_address(self) -> None:
        transport = self.open_transport("serial")
        transport.on_send.append([setting_reply(0x0201, 0)])
        client = ControllerClient(transport)

        with self.assertRaises(UnexpectedControllerReply):
            client.read_machine_status()

        self.assertEqual(transport.sent, [bytes.fromhex("d4898d89")])
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_session_policies_reject_boolean_and_nonfinite_values(
        self,
    ) -> None:
        with self.assertRaises(ValueError):
            HandshakeProfile(
                acknowledge_codes=frozenset((True,))
            )
        with self.assertRaises(ValueError):
            HandshakeProfile(max_retries=True)
        for value in (True, float("inf"), float("nan")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ReplyPolicy(first_timeout=value)
        transport = self.open_transport()
        with self.assertRaises(ValueError):
            ControllerClientClass(
                transport,
                acknowledge_timeout=float("inf"),
            )

    def test_failed_probe_closes_transport_opened_by_client(self) -> None:
        transport = FakeTransport()
        client = ControllerClientClass(
            transport,
            acknowledge_timeout=0,
        )
        with self.assertRaises(ControllerTimeoutError):
            client.open()
        self.assertFalse(transport.is_open)
        self.assertEqual(client.state, SessionState.CLOSED)

    def test_failed_probe_preserves_error_when_cleanup_fails(self) -> None:
        transport = FakeTransport()
        transport.fail_close = True
        client = ControllerClientClass(
            transport,
            acknowledge_timeout=0,
        )
        with self.assertRaises(ControllerTimeoutError) as caught:
            client.open()
        error = caught.exception
        self.assertEqual(error.receipt.transmissions, 1)
        self.assertEqual(
            error.delivery_certainty,
            DeliveryCertainty.UNKNOWN,
        )
        self.assertEqual(str(error.cleanup_error), "close failed")
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_job_packets_each_require_an_acknowledgement(self) -> None:
        transport = self.open_transport()
        transport.on_send.extend(
            [[control(0xCC)], [control(0xCC)]]
        )
        client = ControllerClient(transport, chunk_size=3)
        commands = [
            client.job_codec.command("select_layer", layer=0),
            client.job_codec.command("end_of_file"),
        ]
        receipt = client.send_job_commands(commands)
        self.assertEqual(len(receipt.packets), 2)
        self.assertEqual(receipt.transmissions, 2)
        self.assertEqual(receipt.retries, 0)
        self.assertEqual(receipt.completed_packets, 2)
        self.assertEqual(transport.sent, list(receipt.packets))

    def test_empty_exchange_is_rejected_without_wire_io(self) -> None:
        transport = self.open_transport()
        client = ControllerClient(transport)
        program = client.job_codec.program([])
        with self.assertRaises(UnsupportedExchangeError):
            client.send_job(program)
        self.assertEqual(transport.sent, [])
        self.assertEqual(client.state, SessionState.READY)

    def test_retry_code_retransmits_the_exact_packet(self) -> None:
        transport = self.open_transport()
        transport.on_send.extend(
            [[control(0xCF)], [control(0xCC)]]
        )
        client = ControllerClient(transport)
        receipt = client.keep_alive()
        self.assertEqual(receipt.transmissions, 2)
        self.assertEqual(receipt.retries, 1)
        self.assertEqual(transport.sent[0], transport.sent[1])

    def test_data_before_retry_faults_without_retransmission(self) -> None:
        transport = self.open_transport()
        transport.on_send.append(
            [setting_reply(5, 111), control(0xCF)]
        )
        client = ControllerClient(transport)
        with self.assertRaises(UnexpectedControllerReply):
            client.request_command("get_setting", address=5)
        self.assertEqual(len(transport.sent), 1)
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_handshake_event_limit_bounds_liveness_input(self) -> None:
        transport = self.open_transport()
        transport.on_send.append(
            [control(0xCE), control(0xCE), control(0xCE)]
        )
        client = ControllerClient(
            transport,
            handshake_event_limit=2,
        )
        command = client.job_codec.command("end_of_file")
        with self.assertRaises(ReplyLimitError) as caught:
            client.send_job_commands([command])
        self.assertEqual(caught.exception.receipt.transmissions, 1)
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_handshake_error_disposition_is_profile_driven(self) -> None:
        profile = HandshakeProfile(
            retry_codes=frozenset((0xCD,)),
            reject_codes=frozenset((0xCF,)),
            max_retries=1,
        )
        transport = self.open_transport()
        transport.on_send.extend(
            [[control(0xCD)], [control(0xCC)]]
        )
        client = ControllerClient(
            transport,
            handshake_profile=profile,
        )
        receipt = client.keep_alive()
        self.assertEqual(receipt.retries, 1)

    def test_explicit_rejection_does_not_desynchronize(self) -> None:
        transport = self.open_transport()
        transport.on_send.append([control(0xCD)])
        client = ControllerClient(transport)
        with self.assertRaises(ControllerRejectedError) as caught:
            client.keep_alive()
        self.assertIsNotNone(caught.exception.receipt)
        self.assertEqual(
            caught.exception.delivery_certainty,
            DeliveryCertainty.REJECTED,
        )
        self.assertEqual(client.state, SessionState.READY)

    def test_rejection_after_an_accepted_prefix_faults_session(self) -> None:
        transport = self.open_transport()
        transport.on_send.extend(
            [[control(0xCC)], [control(0xCD)]]
        )
        client = ControllerClient(transport, chunk_size=3)
        commands = [
            client.job_codec.command("select_layer", layer=0),
            client.job_codec.command("end_of_file"),
        ]
        with self.assertRaises(ControllerRejectedError) as caught:
            client.send_job_commands(commands)
        self.assertEqual(caught.exception.receipt.completed_packets, 1)
        self.assertEqual(
            caught.exception.delivery_certainty,
            DeliveryCertainty.REJECTED,
        )
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)
        transport.responses.append(control(0xCC))
        with self.assertRaises(SessionDesynchronizedError):
            client.keep_alive()
        self.assertEqual(len(transport.sent), 2)

    def test_timeout_faults_session_without_resending(self) -> None:
        transport = self.open_transport()
        client = ControllerClient(transport, acknowledge_timeout=0)
        with self.assertRaises(ControllerTimeoutError) as caught:
            client.keep_alive()
        receipt = caught.exception.receipt
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.transmissions, 1)
        self.assertEqual(receipt.completed_packets, 0)
        self.assertEqual(
            caught.exception.delivery_certainty,
            DeliveryCertainty.UNKNOWN,
        )
        self.assertEqual(len(transport.sent), 1)

    def test_interrupt_faults_session_and_preserves_progress(self) -> None:
        transport = self.open_transport()
        transport.on_send.append([KeyboardInterrupt()])
        client = ControllerClient(transport)
        with self.assertRaises(KeyboardInterrupt) as caught:
            client.keep_alive()
        error = caught.exception
        self.assertEqual(error.receipt.transmissions, 1)
        self.assertEqual(error.receipt.completed_packets, 0)
        self.assertEqual(
            error.delivery_certainty,
            DeliveryCertainty.UNKNOWN,
        )
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)
        transport.responses.append(control(0xCC))
        with self.assertRaises(SessionDesynchronizedError):
            client.keep_alive()
        self.assertEqual(len(transport.sent), 1)
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)
        transport.responses.append(control(0xCC))
        with self.assertRaises(SessionDesynchronizedError):
            client.keep_alive()
        self.assertEqual(len(transport.sent), 1)

    def test_timeout_reports_packets_completed_before_failure(self) -> None:
        transport = self.open_transport()
        transport.on_send.extend([[control(0xCC)], []])
        client = ControllerClient(
            transport,
            chunk_size=3,
            acknowledge_timeout=0,
        )
        commands = [
            client.job_codec.command("select_layer", layer=0),
            client.job_codec.command("end_of_file"),
        ]
        with self.assertRaises(ControllerTimeoutError) as caught:
            client.send_job_commands(commands)
        receipt = caught.exception.receipt
        self.assertIsNotNone(receipt)
        self.assertEqual(len(receipt.packets), 2)
        self.assertEqual(receipt.transmissions, 2)
        self.assertEqual(receipt.completed_packets, 1)
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)
        self.assertEqual(len(transport.sent), 2)

    def test_reply_timeout_reports_confirmed_udp_delivery(self) -> None:
        transport = self.open_transport()
        transport.on_send.append([control(0xCC), None])
        policy = ReplyPolicy(first_timeout=0)
        client = ControllerClient(transport)
        with self.assertRaises(ControllerTimeoutError) as caught:
            client.request_command(
                "get_setting",
                address=5,
                reply_policy=policy,
            )
        receipt = caught.exception.receipt
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.completed_packets, 1)
        self.assertEqual(
            caught.exception.delivery_certainty,
            DeliveryCertainty.CONFIRMED,
        )

    def test_receive_error_reports_unknown_delivery_progress(self) -> None:
        transport = self.open_transport()
        transport.on_send.append([OSError("read failed")])
        client = ControllerClient(transport)
        with self.assertRaises(ControllerTransportError) as caught:
            client.keep_alive()
        receipt = caught.exception.receipt
        self.assertEqual(receipt.transmissions, 1)
        self.assertEqual(receipt.completed_packets, 0)
        self.assertEqual(
            caught.exception.delivery_certainty,
            DeliveryCertainty.UNKNOWN,
        )

    def test_preflight_receive_error_faults_without_transmission(self) -> None:
        transport = self.open_transport()
        transport.responses.append(OSError("preflight failed"))
        client = ControllerClient(transport)
        with self.assertRaises(ControllerTransportError) as caught:
            client.keep_alive()
        self.assertEqual(caught.exception.receipt.transmissions, 0)
        self.assertEqual(transport.sent, [])
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_in_place_recovery_is_refused_without_wire_io(self) -> None:
        transport = self.open_transport()
        client = ControllerClient(transport, acknowledge_timeout=0)
        with self.assertRaises(ControllerTimeoutError):
            client.keep_alive()
        transport.responses.append(control(0xCC))
        with self.assertRaises(UnsupportedExchangeError):
            client.recover()
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)
        self.assertEqual(len(transport.sent), 1)

    def test_request_decodes_data_after_acknowledgement(self) -> None:
        transport = self.open_transport()
        raw_reply = setting_reply(5, 42)
        transport.on_send.append([control(0xCC), raw_reply, None])
        client = ControllerClient(transport)
        response = client.request_command("get_setting", address=5)
        command = response.program.records[0]
        self.assertEqual(command.name, "setting_reply")
        self.assertEqual(command.values, {"address": 5, "value": 42})
        self.assertEqual(response.wire_chunks, (raw_reply,))

    def test_request_rejects_a_mismatched_correlation_field(self) -> None:
        transport = self.open_transport()
        transport.on_send.append(
            [control(0xCC), setting_reply(6, 42)]
        )
        client = ControllerClient(transport)
        with self.assertRaises(UnexpectedControllerReply) as caught:
            client.request_command(
                "get_setting",
                address=5,
                reply_policy=ReplyPolicy(expected_chunks=1),
            )
        self.assertEqual(caught.exception.receipt.completed_packets, 1)
        self.assertEqual(
            caught.exception.delivery_certainty,
            DeliveryCertainty.CONFIRMED,
        )
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_request_rejects_an_unrelated_known_reply(self) -> None:
        transport = self.open_transport()
        logical = bytes.fromhex(
            "da020005000000002a000000002b"
        )
        transport.on_send.append([control(0xCC), swizzle(logical)])
        client = ControllerClient(transport)
        with self.assertRaises(UnexpectedControllerReply):
            client.request_command(
                "get_setting",
                address=5,
                reply_policy=ReplyPolicy(expected_chunks=1),
            )
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_request_rejects_an_issueful_reply(self) -> None:
        transport = self.open_transport()
        transport.on_send.append(
            [control(0xCC), swizzle(bytes.fromhex("da010005"))]
        )
        client = ControllerClient(transport)
        with self.assertRaises(UnexpectedControllerReply):
            client.request_command(
                "get_setting",
                address=5,
                reply_policy=ReplyPolicy(expected_chunks=1),
            )
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_explicit_completion_rejects_immediate_excess(self) -> None:
        transport = self.open_transport()
        transport.on_send.append(
            [
                control(0xCC),
                setting_reply(5, 42),
                setting_reply(6, 43),
            ]
        )
        client = ControllerClient(transport)
        with self.assertRaises(UnexpectedControllerReply) as caught:
            client.request_command(
                "get_setting",
                address=5,
                reply_policy=ReplyPolicy(expected_chunks=1),
            )
        self.assertEqual(caught.exception.receipt.completed_packets, 1)
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_stale_input_is_rejected_before_another_send(self) -> None:
        transport = self.open_transport()
        transport.on_send.append(
            [control(0xCC), setting_reply(5, 42)]
        )
        client = ControllerClient(transport)
        client.request_command(
            "get_setting",
            address=5,
            reply_policy=ReplyPolicy(expected_chunks=1),
        )
        transport.responses.append(setting_reply(6, 43))
        with self.assertRaises(UnexpectedControllerReply):
            client.request_command(
                "get_setting",
                address=7,
                reply_policy=ReplyPolicy(expected_chunks=1),
            )
        self.assertEqual(len(transport.sent), 1)
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_data_before_ack_faults_the_default_session(self) -> None:
        transport = self.open_transport()
        raw_reply = setting_reply(5, 42)
        transport.on_send.append([raw_reply, control(0xCC), None])
        client = ControllerClient(transport)
        with self.assertRaises(UnexpectedControllerReply):
            client.request_command("get_setting", address=5)
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_data_can_imply_ack_only_in_an_explicit_profile(self) -> None:
        transport = self.open_transport()
        raw_reply = setting_reply(5, 42)
        transport.on_send.append([raw_reply, None])
        profile = HandshakeProfile(data_acknowledges=True)
        client = ControllerClient(
            transport,
            handshake_profile=profile,
        )
        response = client.request_command("get_setting", address=5)
        self.assertEqual(response.wire_chunks, (raw_reply,))

    def test_data_without_ack_faults_the_default_session(self) -> None:
        transport = self.open_transport()
        transport.on_send.append([setting_reply(5, 42), None])
        client = ControllerClient(transport, acknowledge_timeout=0)
        with self.assertRaises(UnexpectedControllerReply):
            client.request_command("get_setting", address=5)
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_generic_packet_does_not_accept_keepalive_as_ack(self) -> None:
        transport = self.open_transport()
        transport.on_send.append([control(0xCE), control(0xCC)])
        client = ControllerClient(transport)
        command = client.job_codec.command("end_of_file")
        receipt = client.send_job_commands([command])
        self.assertEqual(receipt.transmissions, 1)

    def test_control_prefix_with_operands_is_not_an_ack(self) -> None:
        transport = self.open_transport()
        transport.on_send.append(
            [swizzle(bytes.fromhex("cc01")), None]
        )
        client = ControllerClient(transport, acknowledge_timeout=0)
        with self.assertRaises(UnexpectedControllerReply):
            client.keep_alive()
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_reply_request_must_fit_in_one_packet(self) -> None:
        transport = self.open_transport()
        client = ControllerClient(transport, chunk_size=4)
        program = client.request_codec.program(
            [
                client.request_codec.command("get_setting", address=5),
                client.request_codec.command("get_setting", address=6),
            ]
        )
        with self.assertRaises(UnsupportedExchangeError):
            client.request(program)
        self.assertEqual(transport.sent, [])
        self.assertEqual(client.state, SessionState.READY)

    def test_reply_policy_enforces_chunk_bound(self) -> None:
        transport = self.open_transport()
        transport.on_send.append(
            [
                control(0xCC),
                setting_reply(5, 42),
                setting_reply(6, 43),
            ]
        )
        policy = ReplyPolicy(max_chunks=1)
        client = ControllerClient(transport)
        with self.assertRaises(ReplyLimitError) as caught:
            client.request_command(
                "get_setting",
                address=5,
                reply_policy=policy,
            )
        self.assertEqual(caught.exception.receipt.completed_packets, 1)
        self.assertEqual(
            caught.exception.delivery_certainty,
            DeliveryCertainty.CONFIRMED,
        )
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_no_reply_exchange_rejects_early_data(self) -> None:
        transport = self.open_transport()
        transport.on_send.append(
            [setting_reply(5, 42), control(0xCC)]
        )
        client = ControllerClient(transport)
        program = client.request_codec.program(
            [
                client.request_codec.command(
                    "set_setting",
                    address=5,
                    first_value=1,
                    second_value=2,
                )
            ]
        )
        with self.assertRaises(UnexpectedControllerReply) as caught:
            client.send_no_reply_request(program)
        self.assertEqual(caught.exception.receipt.completed_packets, 0)
        self.assertEqual(
            caught.exception.delivery_certainty,
            DeliveryCertainty.UNKNOWN,
        )
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_no_reply_api_rejects_a_data_request_before_send(self) -> None:
        transport = self.open_transport()
        client = ControllerClient(transport)
        program = client.request_codec.program(
            [client.request_codec.command("get_setting", address=5)]
        )
        with self.assertRaises(UnsupportedExchangeError):
            client.send_no_reply_request(program)
        self.assertEqual(transport.sent, [])
        self.assertEqual(client.state, SessionState.READY)

    def test_reply_processing_error_preserves_confirmed_delivery(self) -> None:
        transport = self.open_transport()
        transport.on_send.append(
            [control(0xCC), setting_reply(5, 42)]
        )

        def fail(_payload: bytes) -> bool:
            raise ValueError("bad completion predicate")

        policy = ReplyPolicy(complete_when=fail)
        client = ControllerClient(transport)
        with self.assertRaises(ControllerExchangeError) as caught:
            client.request_command(
                "get_setting",
                address=5,
                reply_policy=policy,
            )
        self.assertEqual(caught.exception.receipt.completed_packets, 1)
        self.assertEqual(
            caught.exception.delivery_certainty,
            DeliveryCertainty.CONFIRMED,
        )
        self.assertIsInstance(caught.exception.cause, ValueError)

    def test_no_reply_exchange_rejects_data_after_ack(self) -> None:
        transport = self.open_transport()
        transport.on_send.append(
            [control(0xCC), setting_reply(5, 42)]
        )
        client = ControllerClient(transport)
        program = client.request_codec.program(
            [
                client.request_codec.command(
                    "set_setting",
                    address=5,
                    first_value=1,
                    second_value=2,
                )
            ]
        )
        with self.assertRaises(UnexpectedControllerReply):
            client.send_no_reply_request(program)
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_serial_link_assembles_stream_reads_without_udp_framing(
        self,
    ) -> None:
        transport = self.open_transport("serial")
        logical = bytes.fromhex("da010005000000002a")
        transport.on_send.append(
            [
                swizzle(logical[:4]),
                swizzle(logical[4:]),
                None,
            ]
        )
        client = ControllerClient(transport)
        response = client.request_command(
            "get_setting",
            address=5,
            reply_policy=ReplyPolicy(expected_bytes=len(logical)),
        )
        self.assertEqual(response.logical, logical)
        self.assertEqual(len(response.wire_chunks), 2)
        self.assertEqual(
            transport.sent,
            [swizzle(bytes.fromhex("da000005"))],
        )

    def test_serial_reply_request_can_span_stream_writes(self) -> None:
        transport = self.open_transport("serial")
        logical = bytes.fromhex("da010005000000002a")
        transport.on_send.append([swizzle(logical), None])
        client = ControllerClient(transport, chunk_size=3)
        response = client.request_command(
            "get_setting",
            address=5,
            reply_policy=ReplyPolicy(expected_bytes=len(logical)),
        )
        self.assertEqual(response.logical, logical)
        self.assertEqual(len(transport.sent), 2)

    def test_serial_reply_requires_content_completion_before_send(
        self,
    ) -> None:
        transport = self.open_transport("serial")
        client = ControllerClient(transport)
        for policy in (
            None,
            ReplyPolicy(expected_bytes=9, expected_chunks=1),
        ):
            with self.subTest(policy=policy):
                with self.assertRaises(UnsupportedExchangeError):
                    client.request_command(
                        "get_setting",
                        address=5,
                        reply_policy=policy,
                    )
        self.assertEqual(transport.sent, [])
        self.assertEqual(client.state, SessionState.READY)

    def test_explicit_link_supports_a_custom_transport_kind(self) -> None:
        transport = self.open_transport("custom")
        link = SerialLink(transport)
        client = ControllerClient(transport, link=link)
        command = client.job_codec.command("end_of_file")
        client.send_job_commands([command])
        self.assertEqual(transport.sent, [swizzle(bytes.fromhex("d7"))])

    def test_observer_runs_only_after_successful_io(self) -> None:
        failed = self.open_transport()
        failed.fail_send = True
        failed_events: list[ExchangeEvent] = []
        failed_client = ControllerClient(
            failed,
            observer=failed_events.append,
        )
        with self.assertRaises(ControllerTransportError) as caught:
            failed_client.keep_alive()
        self.assertEqual(failed_events, [])
        self.assertEqual(caught.exception.receipt.transmissions, 0)
        self.assertEqual(
            caught.exception.delivery_certainty,
            DeliveryCertainty.UNKNOWN,
        )

        transport = self.open_transport()
        transport.on_send.append([control(0xCC)])
        events: list[ExchangeEvent] = []
        client = ControllerClient(transport, observer=events.append)
        client.keep_alive()
        self.assertEqual(
            [event.direction for event in events],
            ["send", "receive"],
        )
        self.assertEqual(events[0].attempt, 1)
        self.assertEqual(events[0].context, "request")
        self.assertEqual(events[0].exchange_context, "request")
        self.assertEqual(events[1].context, "reply")
        self.assertEqual(events[1].exchange_context, "request")

    def test_observer_failure_does_not_change_protocol_state(self) -> None:
        transport = self.open_transport()
        transport.on_send.append([control(0xCC)])

        def observer(event: ExchangeEvent) -> None:
            del event
            raise RuntimeError("observer failed")

        client = ControllerClient(transport, observer=observer)
        client.keep_alive()
        self.assertEqual(client.state, SessionState.READY)

    def test_observer_cannot_reenter_or_mutate_an_exchange(self) -> None:
        transport = self.open_transport()
        transport.on_send.extend(
            [[control(0xCC)], [control(0xCC)]]
        )
        blocked: list[BaseException] = []
        client: ControllerClientClass

        def observer(event: ExchangeEvent) -> None:
            if event.direction != "receive" or blocked:
                return
            actions = (
                lambda: client.send_job_commands(
                    [client.job_codec.command("end_of_file")]
                ),
                client.open,
                client.close,
            )
            for action in actions:
                try:
                    action()
                except BaseException as error:
                    blocked.append(error)

        client = ControllerClient(
            transport,
            chunk_size=3,
            observer=observer,
        )
        commands = [
            client.job_codec.command("select_layer", layer=0),
            client.job_codec.command("end_of_file"),
        ]
        receipt = client.send_job_commands(commands)
        self.assertEqual(receipt.completed_packets, 2)
        self.assertEqual(len(transport.sent), 2)
        self.assertEqual(len(blocked), 3)
        self.assertTrue(
            all(
                isinstance(error, UnsupportedExchangeError)
                for error in blocked
            )
        )

    def test_reentrant_completion_faults_after_confirmed_send(self) -> None:
        transport = self.open_transport()
        transport.on_send.append(
            [control(0xCC), setting_reply(5, 42)]
        )
        client = ControllerClient(transport)

        def reenter(_payload: bytes) -> bool:
            client.keep_alive()
            return True

        with self.assertRaises(UnsupportedExchangeError) as caught:
            client.request_command(
                "get_setting",
                address=5,
                reply_policy=ReplyPolicy(complete_when=reenter),
            )
        self.assertEqual(caught.exception.receipt.completed_packets, 1)
        self.assertEqual(
            caught.exception.delivery_certainty,
            DeliveryCertainty.CONFIRMED,
        )
        self.assertEqual(client.state, SessionState.DESYNCHRONIZED)

    def test_context_manager_preserves_body_failure_on_close_error(
        self,
    ) -> None:
        transport = self.open_transport()
        transport.on_send.extend([[control(0xCC)], []])
        transport.fail_close = True
        client = ControllerClient(
            transport,
            acknowledge_timeout=0,
        )
        with self.assertRaises(ControllerTimeoutError) as caught:
            with client:
                client.keep_alive()
        self.assertEqual(caught.exception.receipt.transmissions, 1)
        self.assertEqual(str(caught.exception.cleanup_error), "close failed")

    def test_exchange_lock_prevents_concurrent_interleaving(self) -> None:
        class BlockingTransport(FakeTransport):
            def __init__(self) -> None:
                super().__init__()
                self.open()
                self.started = threading.Event()
                self.release = threading.Event()
                self.acknowledged = 0

            def receive(self, timeout: float) -> bytes | None:
                del timeout
                if self.acknowledged >= len(self.sent):
                    return None
                self.started.set()
                if not self.release.wait(1.0):
                    return None
                self.acknowledged += 1
                return control(0xCC)

        transport = BlockingTransport()
        client = ControllerClient(transport)
        failures: list[BaseException] = []

        def send() -> None:
            try:
                client.keep_alive()
            except BaseException as error:
                failures.append(error)

        first = threading.Thread(target=send)
        second = threading.Thread(target=send)
        first.start()
        self.assertTrue(transport.started.wait(1.0))
        second.start()
        self.assertEqual(len(transport.sent), 1)
        transport.release.set()
        first.join(1.0)
        second.join(1.0)
        self.assertFalse(failures)
        self.assertEqual(len(transport.sent), 2)


if __name__ == "__main__":
    unittest.main()
