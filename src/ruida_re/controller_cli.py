"""Noninteractive command-line operations for Ruida controllers."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
import sys
from typing import Any

from .api import RuidaCodec
from .cli_io import atomic_write_text, require_distinct_paths
from .controller import (
    ControllerClient,
    ControllerResponse,
    DeliveryCertainty,
    ReplyPolicy,
    SendReceipt,
)
from .diagnostics import TranscriptObserver
from .jsonio import loads as load_json
from .program import Program, decode_path
from .registry import get_registry
from .transcript import Endpoint
from .transports import (
    DEFAULT_CONTROLLER_PORT,
    DEFAULT_LOCAL_PORT,
    SerialTransport,
    UdpTransport,
)


ENTRY_POINT = "ruida-controller = ruida_re.controller_cli:main"
_SAFE_INTERACTION_EVIDENCE = frozenset(
    ("reported", "controlled-fixture", "hardware-observed")
)
READ_ONLY_REPLY_REQUESTS = frozenset(
    spec.name
    for spec in get_registry("request")
    if spec.controller_effect == "read-only"
    and spec.reply_behavior == "data"
    and spec.reply_commands
    and spec.shape_evidence in _SAFE_INTERACTION_EVIDENCE
    and spec.semantic_evidence in _SAFE_INTERACTION_EVIDENCE
    and spec.shape_sources
    and spec.semantic_sources
)
_COMMON_DEFAULTS: dict[str, object] = {
    "udp": None,
    "serial": None,
    "magic": 0x88,
    "ack_timeout": 1.0,
    "chunk_size": 1024,
    "controller_port": None,
    "local_host": None,
    "local_port": None,
    "transcript": None,
    "force": False,
}


class _UsageError(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _UsageError(message)


@dataclass(frozen=True)
class _PreparedOperation:
    program: Program | None = None
    reply_policy: ReplyPolicy | None = None


@dataclass(frozen=True)
class _OperationOutcome:
    result: dict[str, object] | None
    error: BaseException | None
    cleanup_error: BaseException | None
    completed: bool


def _integer(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"expected an integer, got {value!r}"
        ) from error


def _magic(value: str) -> int:
    result = _integer(value)
    if not 0 <= result <= 0xFF:
        raise argparse.ArgumentTypeError("magic must fit in one byte")
    return result


def _port(value: str) -> int:
    result = _integer(value)
    if not 0 <= result <= 0xFFFF:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return result


def _controller_port_value(value: str) -> int:
    result = _port(value)
    if result == 0:
        raise argparse.ArgumentTypeError(
            "controller port must be between 1 and 65535"
        )
    return result


def _positive_integer(value: str) -> int:
    result = _integer(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return result


def _nonnegative_integer(value: str) -> int:
    result = _integer(value)
    if result < 0:
        raise argparse.ArgumentTypeError("value cannot be negative")
    return result


def _nonnegative_float(value: str) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"expected a number, got {value!r}"
        ) from error
    if not math.isfinite(result) or result < 0:
        raise argparse.ArgumentTypeError(
            "value must be finite and nonnegative"
        )
    return result


def _positive_float(value: str) -> float:
    result = _nonnegative_float(value)
    if result == 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return result


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    connection = parser.add_mutually_exclusive_group()
    connection.add_argument("--udp", metavar="HOST")
    connection.add_argument("--serial", metavar="DEVICE")
    parser.add_argument("--magic", type=_magic)
    parser.add_argument("--ack-timeout", type=_nonnegative_float)
    parser.add_argument("--chunk-size", type=_positive_integer)
    parser.add_argument("--controller-port", type=_controller_port_value)
    parser.add_argument("--local-host")
    parser.add_argument("--local-port", type=_port)
    parser.add_argument("--transcript", type=Path)
    parser.add_argument("--force", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    """Build the controller CLI argument grammar."""
    common = _Parser(
        add_help=False,
        argument_default=argparse.SUPPRESS,
    )
    _add_common_options(common)
    parser = _Parser(
        description="Perform bounded Ruida controller operations.",
        parents=[common],
    )
    commands = parser.add_subparsers(dest="operation", required=True)

    commands.add_parser(
        "probe",
        parents=[common],
        help="open, drain, and probe the selected controller link",
    )

    request = commands.add_parser(
        "request",
        parents=[common],
        help="send one safe read-only request and decode its bounded reply",
    )
    supported = ", ".join(sorted(READ_ONLY_REPLY_REQUESTS))
    request.add_argument(
        "name",
        metavar="NAME",
        help=f"read-only reply command; supported: {supported}",
    )
    request.add_argument(
        "--values",
        default="{}",
        metavar="JSON",
        help="command field values as one JSON object",
    )
    defaults = ReplyPolicy()
    request.add_argument(
        "--first-timeout",
        type=_nonnegative_float,
        default=defaults.first_timeout,
    )
    request.add_argument(
        "--idle-timeout",
        type=_nonnegative_float,
        default=defaults.idle_timeout,
    )
    request.add_argument(
        "--total-timeout",
        type=_positive_float,
        default=defaults.total_timeout,
    )
    request.add_argument(
        "--max-chunks",
        type=_positive_integer,
        default=defaults.max_chunks,
    )
    request.add_argument(
        "--max-bytes",
        type=_positive_integer,
        default=defaults.max_bytes,
    )
    request.add_argument(
        "--expected-chunks",
        type=_positive_integer,
    )
    request.add_argument(
        "--expected-bytes",
        type=_nonnegative_integer,
    )

    send_job = commands.add_parser(
        "send-job",
        parents=[common],
        help="decode and execute one .rd job on the controller",
    )
    send_job.add_argument("path", type=Path, metavar="PATH")
    send_job.add_argument(
        "--confirm-machine-execution",
        action="store_true",
        required=True,
        help="acknowledge that this command executes a physical machine",
    )
    send_job.add_argument(
        "--checksum",
        choices=("preserve", "recompute"),
        required=True,
        help="explicit file-checksum handling policy",
    )
    send_job.add_argument(
        "--allow-decode-issues",
        action="store_true",
        help="execute despite opaque or invalid decoded frames",
    )
    return parser


def _apply_common_defaults(args: argparse.Namespace) -> None:
    for name, value in _COMMON_DEFAULTS.items():
        if not hasattr(args, name):
            setattr(args, name, value)


def _validate_common(args: argparse.Namespace) -> None:
    if (args.udp is None) == (args.serial is None):
        raise _UsageError("exactly one of --udp or --serial is required")
    if args.udp == "":
        raise _UsageError("--udp HOST cannot be empty")
    if args.serial == "":
        raise _UsageError("--serial DEVICE cannot be empty")
    if args.local_host == "":
        raise _UsageError("--local-host cannot be empty")
    if args.serial is not None and args.transcript is not None:
        raise _UsageError(
            "UDP transcript output is not available with --serial"
        )
    udp_only = (
        args.controller_port is not None
        or args.local_host is not None
        or args.local_port is not None
    )
    if args.serial is not None and udp_only:
        raise _UsageError("UDP endpoint options cannot be used with --serial")
    if args.force and args.transcript is None:
        raise _UsageError("--force requires --transcript")
    if args.transcript is not None and args.local_host is None:
        raise _UsageError(
            "--transcript requires an explicit UDP --local-host"
        )
    if args.transcript is not None and args.local_port == 0:
        raise _UsageError(
            "--transcript cannot record an ephemeral --local-port"
        )


def _validate_transcript_path(args: argparse.Namespace) -> None:
    path = args.transcript
    if path is None:
        return
    if not path.parent.is_dir():
        raise FileNotFoundError(path.parent)
    if path.is_dir():
        raise IsADirectoryError(path)
    if not args.force and (path.exists() or path.is_symlink()):
        raise FileExistsError(path)
    if args.operation == "send-job":
        require_distinct_paths(args.path, path)


def _json_object(value: str) -> dict[str, object]:
    decoded = load_json(value)
    if not isinstance(decoded, dict):
        raise ValueError("Request field values must be a JSON object")
    if not all(isinstance(name, str) for name in decoded):
        raise ValueError("Request field names must be strings")
    return decoded


def _prepare_request(args: argparse.Namespace) -> _PreparedOperation:
    if args.name not in READ_ONLY_REPLY_REQUESTS:
        supported = ", ".join(sorted(READ_ONLY_REPLY_REQUESTS))
        raise ValueError(
            f"request only permits read-only, reply-producing commands: "
            f"{supported}; refusing {args.name!r}"
        )
    values = _json_object(args.values)
    codec = RuidaCodec(magic=args.magic, context="request")
    command = codec.command(args.name, **values)
    program = codec.program([command])
    policy = ReplyPolicy(
        first_timeout=args.first_timeout,
        idle_timeout=args.idle_timeout,
        total_timeout=args.total_timeout,
        max_chunks=args.max_chunks,
        max_bytes=args.max_bytes,
        expected_chunks=args.expected_chunks,
        expected_bytes=args.expected_bytes,
    )
    if not policy.has_explicit_completion:
        raise ValueError(
            "request requires --expected-chunks or --expected-bytes"
        )
    if args.serial is not None and not policy.has_stream_completion:
        raise ValueError(
            "serial request requires --expected-bytes and cannot use "
            "--expected-chunks because reads are not protocol boundaries"
        )
    return _PreparedOperation(program, policy)


def _prepare_job(args: argparse.Namespace) -> _PreparedOperation:
    if args.path.suffix.lower() != ".rd":
        raise ValueError("send-job input must have a .rd extension")
    program = decode_path(
        args.path,
        magic=args.magic,
        context="job",
        container="rd",
    )
    if program.issues and not args.allow_decode_issues:
        count = len(program.issues)
        raise ValueError(
            f"Job has {count} decode issue(s); inspect it or pass "
            "--allow-decode-issues explicitly"
        )
    logical = replace(program, container="logical", header="")
    codec = RuidaCodec(magic=args.magic, context="job")
    encoded = codec.encode(logical, checksum_policy=args.checksum)
    if not encoded:
        raise ValueError("Refusing to execute an empty .rd job")
    return _PreparedOperation(program=program)


def _prepare(args: argparse.Namespace) -> _PreparedOperation:
    _validate_common(args)
    _validate_transcript_path(args)
    if args.operation == "request":
        return _prepare_request(args)
    if args.operation == "send-job":
        return _prepare_job(args)
    return _PreparedOperation()


def _controller_port(args: argparse.Namespace) -> int:
    if args.controller_port is None:
        return DEFAULT_CONTROLLER_PORT
    return args.controller_port


def _local_port(args: argparse.Namespace) -> int:
    if args.local_port is None:
        return DEFAULT_LOCAL_PORT
    return args.local_port


def _make_observer(
    args: argparse.Namespace,
) -> TranscriptObserver | None:
    if args.transcript is None:
        return None
    return TranscriptObserver(
        host=Endpoint(args.local_host, _local_port(args)),
        controller=Endpoint(args.udp, _controller_port(args)),
        magic=args.magic,
    )


def _make_transport(args: argparse.Namespace) -> Any:
    if args.udp is not None:
        return UdpTransport(
            args.udp,
            controller_port=_controller_port(args),
            local_host=args.local_host,
            local_port=_local_port(args),
        )
    return SerialTransport(args.serial)


def _receipt(receipt: SendReceipt) -> dict[str, int]:
    return {
        "packets": len(receipt.packets),
        "packet_bytes": sum(len(packet) for packet in receipt.packets),
        "transmissions": receipt.transmissions,
        "retries": receipt.retries,
        "completed_packets": receipt.completed_packets,
    }


def _transport_result(args: argparse.Namespace) -> dict[str, object]:
    if args.udp is not None:
        return {
            "kind": "udp",
            "host": args.udp,
            "controller_port": _controller_port(args),
            "local_host": args.local_host,
            "local_port": _local_port(args),
        }
    return {"kind": "serial", "device": args.serial}


def _request_result(response: ControllerResponse) -> dict[str, object]:
    return {
        "receipt": _receipt(response.receipt),
        "logical": response.logical.hex(),
        "wire_chunks": [chunk.hex() for chunk in response.wire_chunks],
        "program": response.program.to_dict(),
    }


def _operate(
    args: argparse.Namespace,
    prepared: _PreparedOperation,
    observer: TranscriptObserver | None,
) -> _OperationOutcome:
    result: dict[str, object] = {
        "ok": True,
        "operation": args.operation,
        "transport": _transport_result(args),
    }
    operation_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    completed = False
    client: ControllerClient | None = None
    try:
        transport = _make_transport(args)
        client = ControllerClient(
            transport,
            magic=args.magic,
            chunk_size=args.chunk_size,
            acknowledge_timeout=args.ack_timeout,
            observer=observer,
        )
        client.open()
        if args.operation == "probe":
            result["controller_acknowledged"] = args.udp is not None
            completed = True
        elif args.operation == "request":
            if prepared.program is None or prepared.reply_policy is None:
                raise AssertionError("Request was not prepared")
            response = client.request(
                prepared.program,
                reply_policy=prepared.reply_policy,
            )
            completed = True
            result["request"] = args.name
            result["response"] = _request_result(response)
        else:
            if prepared.program is None:
                raise AssertionError("Job was not prepared")
            receipt = client.send_job(
                prepared.program,
                checksum_policy=args.checksum,
            )
            completed = True
            result["path"] = str(args.path)
            result["checksum_policy"] = args.checksum
            result["decode_issues"] = list(prepared.program.issues)
            result["receipt"] = _receipt(receipt)
    except BaseException as error:
        operation_error = error
    if client is not None:
        try:
            client.close()
        except BaseException as error:
            cleanup_error = error
    return _OperationOutcome(
        result if completed else None,
        operation_error,
        cleanup_error,
        completed,
    )


def _write_json(stream: Any, value: dict[str, object]) -> None:
    json.dump(
        value,
        stream,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    stream.write("\n")


def _error(
    category: str,
    error: BaseException,
    *,
    details: dict[str, object] | None = None,
) -> None:
    error_data: dict[str, object] = {
        "category": category,
        "message": str(error),
        "type": type(error).__name__,
    }
    if details:
        error_data.update(details)
    payload: dict[str, object] = {
        "ok": False,
        "error": error_data,
    }
    _write_json(sys.stderr, payload)


def _write_transcript(
    args: argparse.Namespace,
    observer: TranscriptObserver | None,
) -> dict[str, object] | None:
    if args.transcript is None or observer is None:
        return None
    atomic_write_text(
        args.transcript,
        observer.transcript.to_json(),
        force=args.force,
    )
    return {
        "path": str(args.transcript),
        "datagrams": len(observer.transcript.datagrams),
    }


def _controller_error_details(
    error: BaseException,
) -> dict[str, object]:
    details: dict[str, object] = {}
    receipt = getattr(error, "receipt", None)
    if isinstance(receipt, SendReceipt):
        details["receipt"] = _receipt(receipt)
    certainty = getattr(error, "delivery_certainty", None)
    if isinstance(certainty, DeliveryCertainty):
        details["delivery_certainty"] = certainty.value
    for name in ("phase", "packet_index", "code"):
        value = getattr(error, name, None)
        if value is not None:
            details[name] = value
    logical = getattr(error, "logical", None)
    if isinstance(logical, bytes):
        details["logical"] = logical.hex()
    cause = getattr(error, "cause", None)
    if isinstance(cause, BaseException):
        details["cause"] = str(cause)
    cleanup_error = getattr(error, "cleanup_error", None)
    if isinstance(cleanup_error, BaseException):
        details["cleanup_error"] = str(cleanup_error)
    return details


def _failure_category(default: str, error: BaseException) -> str:
    if isinstance(error, KeyboardInterrupt):
        return "interrupted"
    return default


def _failure_status(default: int, error: BaseException) -> int:
    if isinstance(error, KeyboardInterrupt):
        return 130
    return default


def main(argv: Sequence[str] | None = None) -> int:
    """Run one controller operation and return a process exit status."""
    try:
        args = build_parser().parse_args(argv)
        _apply_common_defaults(args)
        prepared = _prepare(args)
        observer = _make_observer(args)
    except _UsageError as error:
        _error("usage", error)
        return 2
    except (KeyError, OSError, TypeError, ValueError) as error:
        _error("input", error)
        return 2
    except KeyboardInterrupt as error:
        _error(
            "interrupted",
            error,
            details={"operation_completed": False},
        )
        return 130

    outcome: _OperationOutcome
    try:
        outcome = _operate(args, prepared, observer)
    except BaseException as error:
        outcome = _OperationOutcome(None, error, None, False)

    transcript_result: dict[str, object] | None = None
    transcript_error: BaseException | None = None
    try:
        transcript_result = _write_transcript(args, observer)
    except BaseException as error:
        transcript_error = error

    if outcome.error is not None:
        details = _controller_error_details(outcome.error)
        details["operation_completed"] = outcome.completed
        if outcome.completed and outcome.result is not None:
            details["operation_result"] = outcome.result
        if outcome.cleanup_error is not None:
            details["cleanup_error"] = str(outcome.cleanup_error)
        if transcript_result is not None:
            details["transcript"] = transcript_result
        if transcript_error is not None:
            details["transcript_error"] = str(transcript_error)
        category = _failure_category("controller", outcome.error)
        _error(category, outcome.error, details=details)
        return _failure_status(1, outcome.error)
    if outcome.cleanup_error is not None:
        details = {
            "operation_completed": outcome.completed,
            "operation_result": outcome.result,
        }
        if transcript_result is not None:
            details["transcript"] = transcript_result
        if transcript_error is not None:
            details["transcript_error"] = str(transcript_error)
        category = _failure_category("cleanup", outcome.cleanup_error)
        _error(category, outcome.cleanup_error, details=details)
        return _failure_status(1, outcome.cleanup_error)
    if transcript_error is not None:
        category = _failure_category("output", transcript_error)
        _error(
            category,
            transcript_error,
            details={
                "operation_completed": outcome.completed,
                "operation_result": outcome.result,
            },
        )
        return _failure_status(2, transcript_error)
    if outcome.result is None:
        raise AssertionError("Controller operation produced no result")
    if transcript_result is not None:
        outcome.result["transcript"] = transcript_result
    try:
        _write_json(sys.stdout, outcome.result)
    except BaseException as error:
        _error(
            _failure_category("output", error),
            error,
            details={
                "operation_completed": True,
                "operation_result": outcome.result,
            },
        )
        return _failure_status(2, error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
