"""Tests for the safe, noninteractive controller command line."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ruida_re.api import RuidaCodec
from ruida_re.codec import swizzle
from ruida_re.controller import (
    ControllerResponse,
    ControllerTimeoutError,
    DeliveryCertainty,
    ExchangeEvent,
    SendReceipt,
)
import ruida_re.controller_cli as controller_cli
from ruida_re.transcript import Endpoint, Transcript
from ruida_re.transport import encode_datagram


class FakeUdpTransport:
    kind = "udp"
    instances: list[FakeUdpTransport] = []

    def __init__(self, host: str, **options: object) -> None:
        self.host = host
        self.options = options
        self.instances.append(self)


class FakeSerialTransport:
    kind = "serial"
    instances: list[FakeSerialTransport] = []

    def __init__(self, device: str) -> None:
        self.device = device
        self.instances.append(self)


class FakeControllerClient:
    instances: list[FakeControllerClient] = []
    open_error: BaseException | None = None
    request_error: BaseException | None = None
    job_error: BaseException | None = None
    close_error: BaseException | None = None

    def __init__(self, transport: object, **options: object) -> None:
        self.transport = transport
        self.options = options
        self.opened = False
        self.closed = False
        self.request_call: tuple[object, object] | None = None
        self.job_call: tuple[object, str] | None = None
        self.instances.append(self)

    def open(self) -> None:
        self.opened = True
        observer = self.options.get("observer")
        if observer is not None and self.transport.kind == "udp":
            request = encode_datagram(b"\xce", "request")
            reply = encode_datagram(b"\xcc", "reply")
            observer(
                ExchangeEvent(
                    direction="send",
                    phase="transmit",
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
        if self.open_error is not None:
            raise self.open_error

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error

    def request(
        self,
        program: object,
        *,
        reply_policy: object,
    ) -> ControllerResponse:
        self.request_call = (program, reply_policy)
        if self.request_error is not None:
            raise self.request_error
        logical = b"\xcc"
        reply = RuidaCodec(context="reply").decode(
            logical,
            container="logical",
        )
        return ControllerResponse(
            receipt=SendReceipt((b"request",), 1, 0, 1),
            program=reply,
            wire_chunks=(encode_datagram(logical, "reply"),),
            logical=logical,
        )

    def send_job(
        self,
        program: object,
        *,
        checksum_policy: str,
    ) -> SendReceipt:
        self.job_call = (program, checksum_policy)
        if self.job_error is not None:
            raise self.job_error
        return SendReceipt((b"job",), 1, 0, 1)


class ControllerCliTest(unittest.TestCase):
    def setUp(self) -> None:
        FakeUdpTransport.instances.clear()
        FakeSerialTransport.instances.clear()
        FakeControllerClient.instances.clear()
        FakeControllerClient.open_error = None
        FakeControllerClient.request_error = None
        FakeControllerClient.job_error = None
        FakeControllerClient.close_error = None

    def run_cli(
        self,
        arguments: list[str],
        *,
        output_stream: io.StringIO | None = None,
    ) -> tuple[int, str, str]:
        output = output_stream or io.StringIO()
        errors = io.StringIO()
        with (
            patch.object(
                controller_cli,
                "UdpTransport",
                FakeUdpTransport,
            ),
            patch.object(
                controller_cli,
                "SerialTransport",
                FakeSerialTransport,
            ),
            patch.object(
                controller_cli,
                "ControllerClient",
                FakeControllerClient,
            ),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            status = controller_cli.main(arguments)
        return status, output.getvalue(), errors.getvalue()

    def assert_no_hardware_objects(self) -> None:
        self.assertEqual(FakeUdpTransport.instances, [])
        self.assertEqual(FakeSerialTransport.instances, [])
        self.assertEqual(FakeControllerClient.instances, [])

    def valid_job(self, path: Path) -> None:
        codec = RuidaCodec(context="job")
        command = codec.command("end_of_file")
        program = codec.program([command], container="rd")
        path.write_bytes(codec.encode(program))

    def test_exactly_one_transport_is_required_before_operation(self) -> None:
        for arguments in (
            ["probe"],
            ["--udp", "controller", "probe", "--serial", "COM1"],
        ):
            with self.subTest(arguments=arguments):
                status, output, errors = self.run_cli(arguments)
                self.assertEqual(status, 2)
                self.assertEqual(output, "")
                payload = json.loads(errors)
                self.assertEqual(payload["error"]["category"], "usage")
                self.assertIn("exactly one", payload["error"]["message"])
                self.assert_no_hardware_objects()

    def test_probe_accepts_common_options_before_subcommand(self) -> None:
        status, output, errors = self.run_cli(
            [
                "--udp",
                "controller.test",
                "--magic",
                "0x77",
                "--ack-timeout",
                "0.25",
                "--chunk-size",
                "512",
                "probe",
            ]
        )
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        payload = json.loads(output)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["controller_acknowledged"])
        client = FakeControllerClient.instances[0]
        self.assertEqual(client.options["magic"], 0x77)
        self.assertEqual(client.options["acknowledge_timeout"], 0.25)
        self.assertEqual(client.options["chunk_size"], 512)
        self.assertTrue(client.opened)
        self.assertTrue(client.closed)

    def test_serial_probe_reports_no_controller_acknowledgement(self) -> None:
        status, output, errors = self.run_cli(
            ["probe", "--serial", "/dev/fake"]
        )
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        payload = json.loads(output)
        self.assertFalse(payload["controller_acknowledged"])
        self.assertEqual(payload["transport"]["kind"], "serial")
        self.assertEqual(FakeSerialTransport.instances[0].device, "/dev/fake")

    def test_request_builds_fields_and_bounded_reply_policy(self) -> None:
        status, output, errors = self.run_cli(
            [
                "request",
                "get_setting",
                "--udp",
                "controller",
                "--values",
                '{"address":5}',
                "--first-timeout",
                "0.2",
                "--idle-timeout",
                "0.03",
                "--total-timeout",
                "2",
                "--max-chunks",
                "4",
                "--max-bytes",
                "64",
                "--expected-chunks",
                "1",
                "--expected-bytes",
                "1",
            ]
        )
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        payload = json.loads(output)
        self.assertEqual(payload["request"], "get_setting")
        self.assertEqual(payload["response"]["logical"], "cc")
        program, policy = FakeControllerClient.instances[0].request_call
        self.assertEqual(program.records[0].values, {"address": 5})
        self.assertEqual(policy.first_timeout, 0.2)
        self.assertEqual(policy.idle_timeout, 0.03)
        self.assertEqual(policy.total_timeout, 2.0)
        self.assertEqual(policy.max_chunks, 4)
        self.assertEqual(policy.max_bytes, 64)
        self.assertEqual(policy.expected_chunks, 1)
        self.assertEqual(policy.expected_bytes, 1)

    def test_invalid_request_is_rejected_before_transport_creation(
        self,
    ) -> None:
        cases = (
            ("[1]", "JSON object"),
            ('{"address":1,"address":2}', "Duplicate"),
            ('{"address":NaN}', "Non-finite"),
        )
        for values, message in cases:
            with self.subTest(values=values):
                status, output, errors = self.run_cli(
                    [
                        "request",
                        "get_setting",
                        "--udp",
                        "controller",
                        "--values",
                        values,
                    ]
                )
                self.assertEqual(status, 2)
                self.assertEqual(output, "")
                self.assertIn(message, json.loads(errors)["error"]["message"])
                self.assert_no_hardware_objects()

    def test_mutating_request_is_not_exposed_by_safe_cli(self) -> None:
        status, output, errors = self.run_cli(
            ["request", "process_start", "--udp", "controller"]
        )
        self.assertEqual(status, 2)
        self.assertEqual(output, "")
        message = json.loads(errors)["error"]["message"]
        self.assertIn("read-only", message)
        self.assertIn("process_start", message)
        self.assert_no_hardware_objects()

    def test_invalid_reply_bounds_are_rejected_before_transport(self) -> None:
        status, output, errors = self.run_cli(
            [
                "request",
                "get_setting",
                "--udp",
                "controller",
                "--values",
                '{"address":1}',
                "--max-bytes",
                "1",
                "--expected-bytes",
                "2",
            ]
        )
        self.assertEqual(status, 2)
        self.assertEqual(output, "")
        self.assertIn("bound", json.loads(errors)["error"]["message"])
        self.assert_no_hardware_objects()

    def test_request_requires_an_explicit_completion_rule(self) -> None:
        for arguments, message in (
            (
                [
                    "request",
                    "get_setting",
                    "--udp",
                    "controller",
                    "--values",
                    '{"address":1}',
                ],
                "--expected",
            ),
            (
                [
                    "request",
                    "get_setting",
                    "--serial",
                    "/dev/fake",
                    "--values",
                    '{"address":1}',
                    "--expected-chunks",
                    "1",
                ],
                "--expected-bytes",
            ),
            (
                [
                    "request",
                    "get_setting",
                    "--serial",
                    "/dev/fake",
                    "--values",
                    '{"address":1}',
                    "--expected-bytes",
                    "9",
                    "--expected-chunks",
                    "1",
                ],
                "cannot use",
            ),
        ):
            with self.subTest(arguments=arguments):
                status, output, errors = self.run_cli(arguments)
                self.assertEqual(status, 2)
                self.assertEqual(output, "")
                self.assertIn(message, errors)
                self.assert_no_hardware_objects()

    def test_controller_destination_port_cannot_be_ephemeral(self) -> None:
        status, output, errors = self.run_cli(
            [
                "probe",
                "--udp",
                "controller",
                "--controller-port",
                "0",
            ]
        )
        self.assertEqual(status, 2)
        self.assertEqual(output, "")
        payload = json.loads(errors)
        self.assertEqual(payload["error"]["category"], "usage")
        self.assertIn("controller port", payload["error"]["message"])
        self.assert_no_hardware_objects()

    def test_serial_transcript_is_refused_without_guessing(self) -> None:
        status, output, errors = self.run_cli(
            [
                "probe",
                "--serial",
                "/dev/fake",
                "--transcript",
                "capture.json",
            ]
        )
        self.assertEqual(status, 2)
        self.assertEqual(output, "")
        message = json.loads(errors)["error"]["message"]
        self.assertIn("transcript", message)
        self.assertIn("--serial", message)
        self.assert_no_hardware_objects()

    def test_udp_transcript_requires_an_explicit_local_endpoint(self) -> None:
        status, output, errors = self.run_cli(
            [
                "probe",
                "--udp",
                "192.0.2.10",
                "--transcript",
                "capture.json",
            ]
        )
        self.assertEqual(status, 2)
        self.assertEqual(output, "")
        self.assertIn("--local-host", errors)
        self.assert_no_hardware_objects()

    def test_udp_transcript_is_atomic_and_preserves_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_path = Path(temporary) / "capture.json"
            status, output, errors = self.run_cli(
                [
                    "probe",
                    "--udp",
                    "192.0.2.10",
                    "--controller-port",
                    "50201",
                    "--local-host",
                    "192.0.2.20",
                    "--local-port",
                    "40201",
                    "--transcript",
                    str(output_path),
                ]
            )
            self.assertEqual(status, 0)
            self.assertEqual(errors, "")
            payload = json.loads(output)
            self.assertEqual(payload["transcript"]["datagrams"], 2)
            transcript = Transcript.from_json(
                output_path.read_text(encoding="utf-8")
            )
            request = encode_datagram(b"\xce", "request")
            reply = encode_datagram(b"\xcc", "reply")
            self.assertEqual(transcript.raw_datagrams(), (request, reply))
            self.assertEqual(
                transcript.datagrams[0].source,
                Endpoint("192.0.2.20", 40201),
            )
            self.assertEqual(
                transcript.datagrams[1].source,
                Endpoint("192.0.2.10", 50201),
            )

    def test_existing_transcript_prevents_operation_unless_forced(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_path = Path(temporary) / "capture.json"
            output_path.write_text("existing", encoding="utf-8")
            arguments = [
                "probe",
                "--udp",
                "controller",
                "--local-host",
                "host",
                "--transcript",
                str(output_path),
            ]
            status, output, errors = self.run_cli(arguments)
            self.assertEqual(status, 2)
            self.assertEqual(output, "")
            self.assertEqual(output_path.read_text(), "existing")
            self.assert_no_hardware_objects()
            status, output, errors = self.run_cli(arguments + ["--force"])
            self.assertEqual(status, 0)
            self.assertEqual(errors, "")
            Transcript.from_json(output_path.read_text(encoding="utf-8"))

    def test_send_job_requires_confirmation_and_checksum_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "job.rd"
            self.valid_job(path)
            cases = (
                ["--checksum", "preserve"],
                ["--confirm-machine-execution"],
            )
            for options in cases:
                with self.subTest(options=options):
                    status, output, errors = self.run_cli(
                        [
                            "send-job",
                            str(path),
                            "--udp",
                            "controller",
                            *options,
                        ]
                    )
                    self.assertEqual(status, 2)
                    self.assertEqual(output, "")
                    self.assertEqual(
                        json.loads(errors)["error"]["category"],
                        "usage",
                    )
                    self.assert_no_hardware_objects()

    def test_send_job_decodes_rd_and_uses_explicit_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "job.rd"
            self.valid_job(path)
            status, output, errors = self.run_cli(
                [
                    "send-job",
                    str(path),
                    "--udp",
                    "controller",
                    "--confirm-machine-execution",
                    "--checksum",
                    "recompute",
                ]
            )
            self.assertEqual(status, 0)
            self.assertEqual(errors, "")
            payload = json.loads(output)
            self.assertEqual(payload["checksum_policy"], "recompute")
            program, policy = FakeControllerClient.instances[0].job_call
            self.assertEqual(program.container, "rd")
            self.assertEqual(program.context, "job")
            self.assertEqual(program.records[0].name, "end_of_file")
            self.assertEqual(policy, "recompute")

    def test_job_decode_issues_need_an_explicit_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "opaque.rd"
            path.write_bytes(swizzle(b"\x01"))
            arguments = [
                "send-job",
                str(path),
                "--serial",
                "/dev/fake",
                "--confirm-machine-execution",
                "--checksum",
                "preserve",
            ]
            status, output, errors = self.run_cli(arguments)
            self.assertEqual(status, 2)
            self.assertEqual(output, "")
            self.assertIn("--allow-decode-issues", errors)
            self.assert_no_hardware_objects()
            status, output, errors = self.run_cli(
                arguments + ["--allow-decode-issues"]
            )
            self.assertEqual(status, 0)
            self.assertEqual(errors, "")
            self.assertTrue(json.loads(output)["decode_issues"])

    def test_empty_job_is_rejected_before_transport_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            for name, content in (
                ("empty.rd", b""),
                ("header.rd", b"RDWORKV000"),
            ):
                with self.subTest(name=name):
                    path = Path(temporary) / name
                    path.write_bytes(content)
                    status, output, errors = self.run_cli(
                        [
                            "send-job",
                            str(path),
                            "--udp",
                            "controller",
                            "--confirm-machine-execution",
                            "--checksum",
                            "preserve",
                        ]
                    )
                    self.assertEqual(status, 2)
                    self.assertEqual(output, "")
                    self.assertIn("empty", errors)
                    self.assert_no_hardware_objects()

    def test_controller_error_reports_partial_delivery_state(self) -> None:
        error = ControllerTimeoutError("reply", packet_index=0)
        error.receipt = SendReceipt((b"request",), 1, 0, 1)
        error.delivery_certainty = DeliveryCertainty.CONFIRMED
        FakeControllerClient.request_error = error
        status, output, errors = self.run_cli(
            [
                "request",
                "get_setting",
                "--udp",
                "controller",
                "--values",
                '{"address":1}',
                "--expected-chunks",
                "1",
            ]
        )
        self.assertEqual(status, 1)
        self.assertEqual(output, "")
        details = json.loads(errors)["error"]
        self.assertEqual(details["phase"], "reply")
        self.assertEqual(details["packet_index"], 0)
        self.assertEqual(details["delivery_certainty"], "confirmed")
        self.assertEqual(details["receipt"]["completed_packets"], 1)
        self.assertFalse(details["operation_completed"])

    def test_close_failure_reports_completed_machine_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "job.rd"
            self.valid_job(path)
            FakeControllerClient.close_error = OSError("close failed")
            status, output, errors = self.run_cli(
                [
                    "send-job",
                    str(path),
                    "--udp",
                    "controller",
                    "--confirm-machine-execution",
                    "--checksum",
                    "preserve",
                ]
            )
            self.assertEqual(status, 1)
            self.assertEqual(output, "")
            details = json.loads(errors)["error"]
            self.assertEqual(details["category"], "cleanup")
            self.assertTrue(details["operation_completed"])
            result = details["operation_result"]
            self.assertEqual(result["receipt"]["completed_packets"], 1)

    def test_interrupt_closes_client_and_reports_partial_delivery(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "job.rd"
            self.valid_job(path)
            error = KeyboardInterrupt()
            error.receipt = SendReceipt((b"job",), 1, 0, 0)
            error.delivery_certainty = DeliveryCertainty.UNKNOWN
            FakeControllerClient.job_error = error
            status, output, errors = self.run_cli(
                [
                    "send-job",
                    str(path),
                    "--udp",
                    "controller",
                    "--confirm-machine-execution",
                    "--checksum",
                    "preserve",
                ]
            )
            self.assertEqual(status, 130)
            self.assertEqual(output, "")
            details = json.loads(errors)["error"]
            self.assertEqual(details["category"], "interrupted")
            self.assertFalse(details["operation_completed"])
            self.assertEqual(details["receipt"]["transmissions"], 1)
            self.assertEqual(
                details["delivery_certainty"],
                "unknown",
            )
            self.assertTrue(FakeControllerClient.instances[0].closed)

    def test_stdout_failure_reports_completed_machine_operation(
        self,
    ) -> None:
        class BrokenOutput(io.StringIO):
            def write(self, value: str) -> int:
                del value
                raise BrokenPipeError("stdout closed")

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "job.rd"
            self.valid_job(path)
            status, output, errors = self.run_cli(
                [
                    "send-job",
                    str(path),
                    "--udp",
                    "controller",
                    "--confirm-machine-execution",
                    "--checksum",
                    "preserve",
                ],
                output_stream=BrokenOutput(),
            )
            self.assertEqual(status, 2)
            self.assertEqual(output, "")
            details = json.loads(errors)["error"]
            self.assertEqual(details["category"], "output")
            self.assertTrue(details["operation_completed"])
            result = details["operation_result"]
            self.assertEqual(result["receipt"]["completed_packets"], 1)

    def test_transcript_failure_reports_completed_machine_operation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary) / "job.rd"
            transcript = Path(temporary) / "capture.json"
            self.valid_job(job)
            with patch.object(
                controller_cli,
                "atomic_write_text",
                side_effect=OSError("disk full"),
            ):
                status, output, errors = self.run_cli(
                    [
                        "send-job",
                        str(job),
                        "--udp",
                        "controller",
                        "--local-host",
                        "host",
                        "--transcript",
                        str(transcript),
                        "--confirm-machine-execution",
                        "--checksum",
                        "preserve",
                    ]
                )
            self.assertEqual(status, 2)
            self.assertEqual(output, "")
            details = json.loads(errors)["error"]
            self.assertEqual(details["category"], "output")
            self.assertTrue(details["operation_completed"])
            result = details["operation_result"]
            self.assertEqual(result["receipt"]["completed_packets"], 1)

    def test_controller_failure_is_json_and_still_writes_transcript(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "failure.json"
            FakeControllerClient.open_error = OSError("offline")
            status, output, errors = self.run_cli(
                [
                    "probe",
                    "--udp",
                    "controller",
                    "--local-host",
                    "host",
                    "--transcript",
                    str(path),
                ]
            )
            self.assertEqual(status, 1)
            self.assertEqual(output, "")
            payload = json.loads(errors)
            self.assertEqual(payload["error"]["category"], "controller")
            self.assertEqual(payload["error"]["message"], "offline")
            self.assertEqual(payload["error"]["transcript"]["datagrams"], 2)
            transcript = Transcript.from_json(path.read_text(encoding="utf-8"))
            self.assertEqual(len(transcript.datagrams), 2)
            self.assertTrue(FakeControllerClient.instances[0].closed)


if __name__ == "__main__":
    unittest.main()
