"""Serialized Ruida controller exchanges over pluggable wire links."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
import math
import threading
import time
from typing import Literal

from .api import RuidaCodec
from .links import (
    InboundUnit,
    OutboundPacket,
    RuidaLink,
    link_for_transport,
)
from .program import KnownCommand, Program, Record
from .specs import CommandSpec
from .transports import ControllerTransport, DrainLimitError


ACKNOWLEDGE = 0xCC
ERROR = 0xCD
KEEP_ALIVE = 0xCE
NEGATIVE_ACKNOWLEDGE = 0xCF
DEFAULT_CHUNK_SIZE = 1024
MACHINE_STATUS_ADDRESS = 0x0200
MACHINE_STATUS_REPLY_BYTES = 9
MACHINE_STATUS_MOVING = 0x01000000
MACHINE_STATUS_PART_END = 0x00000002
MACHINE_STATUS_JOB_RUNNING = 0x00000001
MACHINE_STATUS_KNOWN_MASK = (
    MACHINE_STATUS_MOVING
    | MACHINE_STATUS_PART_END
    | MACHINE_STATUS_JOB_RUNNING
)


class SessionState(str, Enum):
    """Correlation state for a controller session."""

    CLOSED = "closed"
    READY = "ready"
    DESYNCHRONIZED = "desynchronized"


class DeliveryCertainty(str, Enum):
    """What the link proves about the packet active at failure."""

    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class ControllerError(RuntimeError):
    """Base class for controller session failures."""

    def __init__(self, *args: object) -> None:
        super().__init__(*args)
        self.receipt: SendReceipt | None = None
        self.delivery_certainty: DeliveryCertainty | None = None
        self.cleanup_error: BaseException | None = None


class ControllerTimeoutError(ControllerError):
    """Raised when the controller omits an expected response."""

    def __init__(self, phase: str, packet_index: int | None = None):
        location = ""
        if packet_index is not None:
            location = f" for packet {packet_index}"
        super().__init__(f"Controller timed out during {phase}{location}")
        self.phase = phase
        self.packet_index = packet_index
        self.receipt: SendReceipt | None = None
        self.delivery_certainty = DeliveryCertainty.UNKNOWN


class ControllerRejectedError(ControllerError):
    """Raised when the controller explicitly rejects a packet."""

    def __init__(self, code: int, packet_index: int):
        super().__init__(
            f"Controller rejected packet {packet_index} with 0x{code:02x}"
        )
        self.code = code
        self.packet_index = packet_index
        self.receipt: SendReceipt | None = None
        self.delivery_certainty = DeliveryCertainty.REJECTED


class ControllerTransportError(ControllerError):
    """Raised when raw I/O fails during a serialized exchange."""

    def __init__(
        self,
        message: str,
        receipt: SendReceipt,
        cause: OSError,
    ) -> None:
        super().__init__(message)
        self.receipt = receipt
        self.delivery_certainty = DeliveryCertainty.UNKNOWN
        self.cause = cause


class ControllerExchangeError(ControllerError):
    """Raised when local reply processing fails after transmission."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(f"Controller exchange processing failed: {cause}")
        self.cause = cause


class UnexpectedControllerReply(ControllerError):
    """Raised when a wire event violates the active exchange."""

    def __init__(self, message: str, logical: bytes = b""):
        super().__init__(message)
        self.logical = logical


class SessionDesynchronizedError(ControllerError):
    """Raised when delivery correlation must be explicitly recovered."""

    def __init__(self, reason: str):
        super().__init__(
            "Controller session is desynchronized; close it and establish "
            f"a new session after the link is quiescent. Cause: {reason}"
        )
        self.reason = reason


class UnsupportedExchangeError(ControllerError):
    """Raised for an exchange whose reply cannot be correlated safely."""


class ReplyLimitError(ControllerError):
    """Raised when a reply exceeds its declared resource bounds."""


CompletionPredicate = Callable[[bytes], bool]


@dataclass(frozen=True)
class HandshakeProfile:
    """Declarative controller handshake and retry behavior."""

    acknowledge_codes: frozenset[int] = frozenset((ACKNOWLEDGE,))
    keep_alive_codes: frozenset[int] = frozenset(
        (ACKNOWLEDGE, KEEP_ALIVE)
    )
    retry_codes: frozenset[int] = frozenset(
        (NEGATIVE_ACKNOWLEDGE,)
    )
    reject_codes: frozenset[int] = frozenset((ERROR,))
    liveness_codes: frozenset[int] = frozenset((KEEP_ALIVE,))
    max_retries: int = 3
    data_acknowledges: bool = False

    def __post_init__(self) -> None:
        groups = (
            self.acknowledge_codes,
            self.keep_alive_codes,
            self.retry_codes,
            self.reject_codes,
            self.liveness_codes,
        )
        if any(
            type(code) is not int or code < 0 or code > 0xFF
            for group in groups
            for code in group
        ):
            raise ValueError("Handshake codes must fit in one byte")
        if self.retry_codes & self.reject_codes:
            raise ValueError("Retry and reject codes cannot overlap")
        accepted = self.acknowledge_codes | self.keep_alive_codes
        rejected = self.retry_codes | self.reject_codes
        if accepted & rejected:
            raise ValueError("Accepted and rejected controls cannot overlap")
        if self.liveness_codes & rejected:
            raise ValueError("Liveness and rejected controls cannot overlap")
        if type(self.max_retries) is not int or self.max_retries < 0:
            raise ValueError("Retry count cannot be negative")
        if type(self.data_acknowledges) is not bool:
            raise ValueError("data_acknowledges must be a boolean")

    @property
    def control_codes(self) -> frozenset[int]:
        """Return every exact one-byte control recognized by this profile."""
        return frozenset().union(
            self.acknowledge_codes,
            self.keep_alive_codes,
            self.retry_codes,
            self.reject_codes,
            self.liveness_codes,
        )


@dataclass(frozen=True)
class ReplyPolicy:
    """Timeout, completion, and resource bounds for one reply."""

    first_timeout: float = 1.0
    idle_timeout: float = 0.05
    total_timeout: float = 5.0
    max_chunks: int = 256
    max_bytes: int = 1_048_576
    expected_chunks: int | None = None
    expected_bytes: int | None = None
    complete_when: CompletionPredicate | None = None

    def __post_init__(self) -> None:
        timeouts = (
            self.first_timeout,
            self.idle_timeout,
            self.total_timeout,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            for value in timeouts
        ):
            raise ValueError(
                "Reply timeouts must be finite and nonnegative"
            )
        if self.total_timeout == 0:
            raise ValueError("Total reply timeout must be positive")
        if type(self.max_chunks) is not int or self.max_chunks <= 0:
            raise ValueError("Reply bounds must be positive")
        if type(self.max_bytes) is not int or self.max_bytes <= 0:
            raise ValueError("Reply bounds must be positive")
        if self.expected_chunks is not None:
            if (
                type(self.expected_chunks) is not int
                or self.expected_chunks <= 0
            ):
                raise ValueError("Expected reply chunks must be positive")
            if self.expected_chunks > self.max_chunks:
                raise ValueError("Expected chunks exceed the reply bound")
        if self.expected_bytes is not None:
            if (
                type(self.expected_bytes) is not int
                or self.expected_bytes < 0
            ):
                raise ValueError("Expected reply bytes cannot be negative")
            if self.expected_bytes > self.max_bytes:
                raise ValueError("Expected bytes exceed the reply bound")
        if self.complete_when is not None:
            if not callable(self.complete_when):
                raise ValueError("Reply completion predicate must be callable")

    @property
    def has_explicit_completion(self) -> bool:
        return any(
            value is not None
            for value in (
                self.expected_chunks,
                self.expected_bytes,
                self.complete_when,
            )
        )

    @property
    def has_stream_completion(self) -> bool:
        """Return whether completion is independent of read chunking."""
        return (
            self.expected_chunks is None
            and (
                self.expected_bytes is not None
                or self.complete_when is not None
            )
        )

    def is_complete(self, payload: bytes, chunks: int) -> bool:
        checks = []
        if self.expected_chunks is not None:
            checks.append(chunks == self.expected_chunks)
        if self.expected_bytes is not None:
            checks.append(len(payload) == self.expected_bytes)
        if self.complete_when is not None:
            checks.append(self.complete_when(payload))
        return bool(checks) and all(checks)


@dataclass(frozen=True)
class SendReceipt:
    """Wire-level result of a completed send operation."""

    packets: tuple[bytes, ...]
    transmissions: int
    retries: int
    completed_packets: int


@dataclass(frozen=True)
class ControllerResponse:
    """Decoded reply plus its raw and logical stream representations."""

    receipt: SendReceipt
    program: Program
    wire_chunks: tuple[bytes, ...]
    logical: bytes


@dataclass(frozen=True)
class MachineStatus:
    """Reported controller status bits without inferred machine state."""

    raw_word: int
    moving: bool
    job_running: bool
    part_end: bool
    unknown_bits: int


EventDirection = Literal["send", "receive"]


@dataclass(frozen=True)
class ExchangeEvent:
    """One successful wire I/O event emitted by a controller session."""

    direction: EventDirection
    phase: str
    link: str
    context: str | None
    exchange_context: str | None
    raw: bytes
    logical: bytes
    timestamp: float
    packet_index: int | None = None
    attempt: int | None = None


ExchangeObserver = Callable[[ExchangeEvent], None]


@dataclass
class _ExchangeProgress:
    packets: tuple[OutboundPacket, ...]
    transmissions: int = 0
    retries: int = 0
    completed_packets: int = 0

    def receipt(self) -> SendReceipt:
        return SendReceipt(
            packets=tuple(packet.raw for packet in self.packets),
            transmissions=self.transmissions,
            retries=self.retries,
            completed_packets=self.completed_packets,
        )


@dataclass(frozen=True)
class _InboundEvent:
    unit: InboundUnit
    code: int | None = None

    @property
    def is_control(self) -> bool:
        return self.code is not None


class ControllerClient:
    """Serialized Ruida exchange state machine over a selected link."""

    def __init__(
        self,
        transport: ControllerTransport,
        *,
        magic: int = 0x88,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        acknowledge_timeout: float = 1.0,
        reply_timeout: float = 1.0,
        inter_reply_timeout: float = 0.05,
        reply_total_timeout: float = 5.0,
        negative_acknowledge_retries: int | None = None,
        handshake_profile: HandshakeProfile | None = None,
        reply_policy: ReplyPolicy | None = None,
        drain_limit: int = 256,
        handshake_event_limit: int = 256,
        observer: ExchangeObserver | None = None,
        link: RuidaLink | None = None,
        assume_synchronized: bool = False,
    ) -> None:
        if type(magic) is not int or not 0 <= magic <= 0xFF:
            raise ValueError("Magic value must fit in one byte")
        if type(chunk_size) is not int or chunk_size <= 0:
            raise ValueError("Chunk size must be positive")
        if (
            isinstance(acknowledge_timeout, bool)
            or not isinstance(acknowledge_timeout, (int, float))
            or not math.isfinite(acknowledge_timeout)
            or acknowledge_timeout < 0
        ):
            raise ValueError(
                "Acknowledgement timeout must be finite and nonnegative"
            )
        if type(drain_limit) is not int or drain_limit <= 0:
            raise ValueError("Drain limit must be positive")
        if (
            type(handshake_event_limit) is not int
            or handshake_event_limit <= 0
        ):
            raise ValueError("Handshake event limit must be positive")
        if type(assume_synchronized) is not bool:
            raise ValueError("assume_synchronized must be a boolean")
        profile = handshake_profile or HandshakeProfile()
        if negative_acknowledge_retries is not None:
            if (
                type(negative_acknowledge_retries) is not int
                or negative_acknowledge_retries < 0
            ):
                raise ValueError("Retry count cannot be negative")
            profile = replace(
                profile,
                max_retries=negative_acknowledge_retries,
            )
        if reply_policy is None:
            reply_policy = ReplyPolicy(
                first_timeout=reply_timeout,
                idle_timeout=inter_reply_timeout,
                total_timeout=reply_total_timeout,
            )
        selected_link = link or link_for_transport(
            transport,
            magic=magic,
        )
        if selected_link.transport is not transport:
            raise ValueError("The link must wrap the supplied transport")
        if selected_link.magic != magic:
            raise ValueError("The link and controller magic values must match")
        if assume_synchronized and not selected_link.is_open:
            raise ValueError(
                "Cannot assume synchronization on a closed transport"
            )
        self.transport = transport
        self.link = selected_link
        self.magic = magic
        self.chunk_size = chunk_size
        self.acknowledge_timeout = acknowledge_timeout
        self.handshake_profile = profile
        self.reply_policy = reply_policy
        self.drain_limit = drain_limit
        self.handshake_event_limit = handshake_event_limit
        self.observer = observer
        self.state = SessionState.CLOSED
        if assume_synchronized:
            self.state = SessionState.READY
        self._fault_reason: str | None = None
        self._lock = threading.RLock()
        self._inbound: deque[_InboundEvent] = deque()
        self._reply_buffer: deque[_InboundEvent] = deque()
        self._operation_active = False
        self.job_codec = RuidaCodec(magic=magic, context="job")
        self.request_codec = RuidaCodec(magic=magic, context="request")
        self.reply_codec = RuidaCodec(magic=magic, context="reply")

    @property
    def is_open(self) -> bool:
        return self.link.is_open

    @property
    def is_ready(self) -> bool:
        return self.state is SessionState.READY and self.link.is_open

    @property
    def fault_reason(self) -> str | None:
        return self._fault_reason

    def open(self, *, probe: bool = True) -> None:
        """Open, clear stale input, and optionally probe the controller."""
        with self._lock:
            self._begin_operation()
            try:
                self._open_locked(probe)
            finally:
                self._end_operation()

    def _open_locked(self, probe: bool) -> None:
        if self.state is SessionState.DESYNCHRONIZED:
            raise SessionDesynchronizedError(
                self._fault_reason or "unknown correlation failure"
            )
        opened_here = not self.link.is_open
        try:
            self.link.open()
            self._drain_locked()
            self._set_ready()
            if probe and self.link.acknowledgement_required:
                self._keep_alive_locked()
        except BaseException as error:
            cleanup_error: BaseException | None = None
            if opened_here:
                try:
                    self.link.close()
                except BaseException as close_error:
                    cleanup_error = close_error
                if self.link.is_open:
                    self._set_desynchronized(str(error))
                else:
                    self._set_closed()
            elif self.link.is_open:
                self._set_desynchronized(str(error))
            if cleanup_error is not None:
                error.cleanup_error = cleanup_error
            raise

    def recover(self, *, probe: bool = True) -> SendReceipt | None:
        """Refuse unsafe in-place recovery on an unsequenced protocol."""
        del probe
        with self._lock:
            self._require_inactive_operation()
            raise UnsupportedExchangeError(
                "In-place recovery cannot distinguish a late response from "
                "the current exchange; close and establish a new session "
                "after the link is quiescent"
            )

    def close(self) -> None:
        with self._lock:
            self._begin_operation()
            try:
                try:
                    self.link.close()
                except BaseException as error:
                    if self.link.is_open:
                        self._set_desynchronized(str(error))
                    else:
                        self._set_closed()
                    raise
                else:
                    self._set_closed()
            finally:
                self._end_operation()

    def keep_alive(self) -> SendReceipt:
        """Send a keepalive using its profile-specific accepted controls."""
        with self._lock:
            self._begin_operation()
            try:
                return self._keep_alive_locked()
            finally:
                self._end_operation()

    def stop_process(self) -> SendReceipt:
        """Send the reported software-stop request as one exchange.

        The receipt describes link delivery, not halted execution.
        """
        command = self.request_codec.command("process_stop")
        program = self.request_codec.program([command])
        return self.send_no_reply_request(program)

    def read_machine_status(self) -> MachineStatus:
        """Read the experimental machine-status word at address 0x0200.

        Flag meanings are implementation-reported and are not a proof of
        idle state or execution completion.
        """
        expected_chunks = None
        if self.link.receive_boundaries == "datagram":
            expected_chunks = 1
        policy = replace(
            self.reply_policy,
            expected_chunks=expected_chunks,
            expected_bytes=MACHINE_STATUS_REPLY_BYTES,
            complete_when=None,
        )
        response = self.request_command(
            "get_setting",
            address=MACHINE_STATUS_ADDRESS,
            reply_policy=policy,
        )
        record = response.program.records[0]
        assert isinstance(record, KnownCommand)
        raw_word = record.values["value"]
        assert isinstance(raw_word, int)
        return MachineStatus(
            raw_word=raw_word,
            moving=bool(raw_word & MACHINE_STATUS_MOVING),
            job_running=bool(raw_word & MACHINE_STATUS_JOB_RUNNING),
            part_end=bool(raw_word & MACHINE_STATUS_PART_END),
            unknown_bits=raw_word & ~MACHINE_STATUS_KNOWN_MASK,
        )

    def send_job(
        self,
        program: Program,
        *,
        checksum_policy: str = "recompute",
    ) -> SendReceipt:
        """Send a job Program as one serialized packet exchange."""
        receipt, _ = self._exchange(
            self.job_codec,
            program,
            checksum_policy=checksum_policy,
            reply_policy=None,
            keep_alive=False,
        )
        return receipt

    def send_job_commands(
        self,
        records: Sequence[Record],
        *,
        checksum_policy: str = "recompute",
    ) -> SendReceipt:
        """Build and send job records without Program boilerplate."""
        return self.send_job(
            self.job_codec.program(records),
            checksum_policy=checksum_policy,
        )

    def send_no_reply_request(self, program: Program) -> SendReceipt:
        """Send a request whose protocol contract declares no reply data."""
        self._require_request_behavior(program, "none")
        receipt, _ = self._exchange(
            self.request_codec,
            program,
            checksum_policy="preserve",
            reply_policy=None,
            keep_alive=False,
        )
        return receipt

    def request(
        self,
        program: Program,
        *,
        reply_policy: ReplyPolicy | None = None,
    ) -> ControllerResponse:
        """Send one safely correlatable request and decode its reply stream."""
        request, spec = self._declared_reply_request(program)
        with self._lock:
            self._begin_operation()
            try:
                return self._request_locked(
                    program,
                    request,
                    spec,
                    reply_policy or self.reply_policy,
                )
            finally:
                self._end_operation()

    def _request_locked(
        self,
        program: Program,
        request: KnownCommand,
        spec: CommandSpec,
        reply_policy: ReplyPolicy,
    ) -> ControllerResponse:
        receipt, units = self._exchange_once_locked(
            self.request_codec,
            program,
            checksum_policy="preserve",
            reply_policy=reply_policy,
            keep_alive=False,
        )
        logical = b"".join(unit.logical for unit in units)
        try:
            decoded = self.reply_codec.decode(
                logical,
                container="logical",
            )
            self._validate_declared_reply(request, spec, decoded)
        except ControllerError as error:
            self._fault_completed_exchange(error, receipt)
            raise
        except Exception as cause:
            error = ControllerExchangeError(cause)
            self._fault_completed_exchange(error, receipt)
            raise error from cause
        except BaseException as error:
            self._fault_completed_exchange(error, receipt)
            raise
        return ControllerResponse(
            receipt=receipt,
            program=decoded,
            wire_chunks=tuple(unit.raw for unit in units),
            logical=logical,
        )

    def request_command(
        self,
        name: str,
        *,
        reply_policy: ReplyPolicy | None = None,
        **values: object,
    ) -> ControllerResponse:
        """Construct, send, and decode one request command."""
        command = self.request_codec.command(name, **values)
        program = self.request_codec.program([command])
        return self.request(program, reply_policy=reply_policy)

    def _keep_alive_locked(self) -> SendReceipt:
        command = self.request_codec.command("keep_alive_request")
        program = self.request_codec.program([command])
        receipt, _ = self._exchange_once_locked(
            self.request_codec,
            program,
            checksum_policy="preserve",
            reply_policy=None,
            keep_alive=True,
        )
        return receipt

    def _exchange(
        self,
        codec: RuidaCodec,
        program: Program,
        *,
        checksum_policy: str,
        reply_policy: ReplyPolicy | None,
        keep_alive: bool,
    ) -> tuple[SendReceipt, tuple[InboundUnit, ...]]:
        with self._lock:
            self._begin_operation()
            try:
                return self._exchange_once_locked(
                    codec,
                    program,
                    checksum_policy=checksum_policy,
                    reply_policy=reply_policy,
                    keep_alive=keep_alive,
                )
            finally:
                self._end_operation()

    def _exchange_once_locked(
        self,
        codec: RuidaCodec,
        program: Program,
        *,
        checksum_policy: str,
        reply_policy: ReplyPolicy | None,
        keep_alive: bool,
    ) -> tuple[SendReceipt, tuple[InboundUnit, ...]]:
        self._require_ready()
        packets = self._encode_packets(
            codec,
            program,
            checksum_policy,
        )
        if not packets:
            raise UnsupportedExchangeError(
                "Cannot perform an empty controller exchange"
            )
        if (
            reply_policy is not None
            and self.link.receive_boundaries == "stream"
            and not reply_policy.has_stream_completion
        ):
            raise UnsupportedExchangeError(
                "Stream replies require an expected byte count or "
                "completion predicate and cannot use a chunk count"
            )
        unsafe_reply_split = (
            reply_policy is not None
            and self.link.acknowledgement_required
            and len(packets) != 1
        )
        if unsafe_reply_split:
            raise UnsupportedExchangeError(
                "Reply-producing requests must fit in one wire packet"
            )
        self._require_empty_buffers()
        try:
            return self._perform_exchange(
                packets,
                context=codec.context,
                reply_policy=reply_policy,
                keep_alive=keep_alive,
            )
        except ControllerRejectedError as error:
            receipt = error.receipt
            accepted_prefix = (
                receipt is not None and receipt.completed_packets > 0
            )
            if accepted_prefix or self._inbound or self._reply_buffer:
                self._set_desynchronized(
                    "Controller rejection followed an accepted packet "
                    "prefix or buffered reply data"
                )
            raise
        except BaseException as error:
            if self.link.is_open:
                self._set_desynchronized(str(error))
            else:
                self._set_closed()
            raise

    def _require_request_behavior(
        self,
        program: Program,
        behavior: str,
    ) -> tuple[KnownCommand, ...]:
        if not program.records:
            raise UnsupportedExchangeError(
                "A controller request must contain at least one command"
            )
        commands = []
        for record in program.records:
            if not isinstance(record, KnownCommand):
                raise UnsupportedExchangeError(
                    "Controller requests require structured commands"
                )
            spec = self.request_codec._registry.name(record.name)
            if spec is None or spec.reply_behavior != behavior:
                raise UnsupportedExchangeError(
                    f"Request {record.name} does not declare reply "
                    f"behavior {behavior}"
                )
            commands.append(record)
        return tuple(commands)

    def _declared_reply_request(
        self,
        program: Program,
    ) -> tuple[KnownCommand, CommandSpec]:
        commands = self._require_request_behavior(program, "data")
        if len(commands) != 1:
            raise UnsupportedExchangeError(
                "A reply-producing exchange requires exactly one request"
            )
        request = commands[0]
        spec = self.request_codec._registry.name(request.name)
        if spec is None or not spec.reply_commands:
            raise UnsupportedExchangeError(
                f"Request {request.name} has no declared reply contract"
            )
        return request, spec

    @staticmethod
    def _validate_declared_reply(
        request: KnownCommand,
        spec: CommandSpec,
        reply: Program,
    ) -> None:
        if reply.issues:
            raise UnexpectedControllerReply(
                "Controller reply did not decode without issues"
            )
        if len(reply.records) != 1:
            raise UnexpectedControllerReply(
                "Controller reply did not contain exactly one command"
            )
        record = reply.records[0]
        if not isinstance(record, KnownCommand):
            raise UnexpectedControllerReply(
                "Controller reply was not a known command"
            )
        if record.name not in spec.reply_commands:
            raise UnexpectedControllerReply(
                f"Controller returned {record.name}, expected one of "
                f"{', '.join(spec.reply_commands)}"
            )
        for request_field, reply_field in spec.reply_field_matches:
            expected = request.values.get(request_field)
            actual = record.values.get(reply_field)
            if expected != actual:
                raise UnexpectedControllerReply(
                    f"Controller reply field {reply_field} did not match "
                    f"request field {request_field}"
                )

    def _fault_completed_exchange(
        self,
        error: BaseException,
        receipt: SendReceipt,
    ) -> None:
        setattr(error, "receipt", receipt)
        certainty = DeliveryCertainty.UNKNOWN
        if (
            receipt.completed_packets == len(receipt.packets)
            and self.link.acknowledgement_required
        ):
            certainty = DeliveryCertainty.CONFIRMED
        current = getattr(error, "delivery_certainty", None)
        if current is not DeliveryCertainty.REJECTED:
            setattr(error, "delivery_certainty", certainty)
        if self.link.is_open:
            self._set_desynchronized(str(error))
        else:
            self._set_closed()

    def _perform_exchange(
        self,
        packets: tuple[OutboundPacket, ...],
        *,
        context: str,
        reply_policy: ReplyPolicy | None,
        keep_alive: bool,
    ) -> tuple[SendReceipt, tuple[InboundUnit, ...]]:
        progress = _ExchangeProgress(packets)
        try:
            for index, packet in enumerate(packets):
                self._transmit(
                    packet,
                    packet_index=index,
                    context=context,
                    keep_alive=keep_alive,
                    reply_policy=reply_policy,
                    progress=progress,
                )
                if reply_policy is None and self._reply_buffer:
                    event = self._reply_buffer[0]
                    raise UnexpectedControllerReply(
                        "Reply data arrived during a no-reply exchange",
                        event.unit.logical,
                    )
                if reply_policy is None:
                    self._require_quiet_link(context)
            receipt = progress.receipt()
            if reply_policy is None:
                return receipt, ()
            units = self._collect_reply(reply_policy, context)
        except ControllerError as error:
            self._attach_progress(error, progress)
            raise
        except OSError as error:
            wrapped = ControllerTransportError(
                "Controller transport failed during an exchange",
                progress.receipt(),
                error,
            )
            self._attach_progress(wrapped, progress)
            raise wrapped from error
        except Exception as error:
            wrapped = ControllerExchangeError(error)
            self._attach_progress(wrapped, progress)
            raise wrapped from error
        except BaseException as error:
            self._attach_progress(error, progress)
            raise
        return progress.receipt(), units

    def _attach_progress(
        self,
        error: BaseException,
        progress: _ExchangeProgress,
    ) -> None:
        setattr(error, "receipt", progress.receipt())
        certainty = DeliveryCertainty.UNKNOWN
        if (
            progress.packets
            and progress.completed_packets == len(progress.packets)
            and self.link.acknowledgement_required
        ):
            certainty = DeliveryCertainty.CONFIRMED
        current = getattr(error, "delivery_certainty", None)
        if current is not DeliveryCertainty.REJECTED:
            setattr(error, "delivery_certainty", certainty)

    def _encode_packets(
        self,
        codec: RuidaCodec,
        program: Program,
        checksum_policy: str,
    ) -> tuple[OutboundPacket, ...]:
        logical_program = replace(program, container="logical", header="")
        logical = codec.encode(
            logical_program,
            checksum_policy=checksum_policy,
        )
        return self.link.packetize(logical, self.chunk_size)

    def _transmit(
        self,
        packet: OutboundPacket,
        *,
        packet_index: int,
        context: str,
        keep_alive: bool,
        reply_policy: ReplyPolicy | None,
        progress: _ExchangeProgress,
    ) -> None:
        retries = 0
        attempt = 1
        while True:
            self.link.send(packet)
            progress.transmissions += 1
            self._notify(
                direction="send",
                phase="transmit",
                context=context,
                unit=packet,
                packet_index=packet_index,
                attempt=attempt,
            )
            if not self.link.acknowledgement_required:
                progress.completed_packets += 1
                return
            retry_code = self._wait_for_handshake(
                packet_index,
                context=context,
                keep_alive=keep_alive,
                reply_policy=reply_policy,
            )
            if retry_code is None:
                progress.completed_packets += 1
                return
            if retries >= self.handshake_profile.max_retries:
                raise ControllerRejectedError(retry_code, packet_index)
            retries += 1
            progress.retries += 1
            attempt += 1

    def _wait_for_handshake(
        self,
        packet_index: int,
        *,
        context: str,
        keep_alive: bool,
        reply_policy: ReplyPolicy | None,
    ) -> int | None:
        deadline = time.monotonic() + self.acknowledge_timeout
        accepted = (
            self.handshake_profile.keep_alive_codes
            if keep_alive
            else self.handshake_profile.acknowledge_codes
        )
        first_read = True
        inbound_events = 0
        while True:
            if not first_read and time.monotonic() >= deadline:
                raise ControllerTimeoutError(
                    "acknowledgement",
                    packet_index,
                )
            remaining = max(0.0, deadline - time.monotonic())
            event = self._next_event(
                remaining,
                phase="acknowledgement",
                context=context,
            )
            first_read = False
            if event is None:
                raise ControllerTimeoutError(
                    "acknowledgement",
                    packet_index,
                )
            inbound_events += 1
            if inbound_events > self.handshake_event_limit:
                raise ReplyLimitError(
                    "Handshake exceeded its inbound event limit"
                )
            if not event.is_control:
                if not self.handshake_profile.data_acknowledges:
                    raise UnexpectedControllerReply(
                        "Reply data arrived before acknowledgement",
                        event.unit.logical,
                    )
                self._reply_buffer.append(event)
                if reply_policy is not None:
                    units = [
                        buffered.unit
                        for buffered in self._reply_buffer
                    ]
                    self._validate_reply_progress(reply_policy, units)
                return None
            code = event.code
            if code in accepted:
                return None
            if code in self.handshake_profile.retry_codes:
                return code
            if code in self.handshake_profile.reject_codes:
                raise ControllerRejectedError(code, packet_index)
            if code in self.handshake_profile.liveness_codes:
                continue
            raise UnexpectedControllerReply(
                f"Unexpected handshake byte 0x{code:02x}",
                event.unit.logical,
            )

    def _require_quiet_link(self, context: str) -> None:
        event = self._next_event(
            0.0,
            phase="settle",
            context=context,
        )
        if event is None:
            return
        raise UnexpectedControllerReply(
            "Unexpected input followed a no-reply exchange",
            event.unit.logical,
        )

    def _collect_reply(
        self,
        policy: ReplyPolicy,
        context: str,
    ) -> tuple[InboundUnit, ...]:
        deadline = time.monotonic() + policy.total_timeout
        units: list[InboundUnit] = []
        while self._reply_buffer:
            units.append(self._reply_buffer.popleft().unit)
        self._validate_reply_progress(policy, units)
        if self._reply_complete(policy, units):
            return self._finish_explicit_reply(units, context)
        while True:
            if time.monotonic() >= deadline:
                phase = "reply completion" if units else "reply"
                raise ControllerTimeoutError(phase)
            timeout = policy.idle_timeout if units else policy.first_timeout
            timeout = min(timeout, max(0.0, deadline - time.monotonic()))
            event = self._next_event(
                timeout,
                phase="reply",
                context=context,
            )
            if event is None:
                if units and not policy.has_explicit_completion:
                    return tuple(units)
                phase = "reply completion" if units else "reply"
                raise ControllerTimeoutError(phase)
            if event.is_control:
                if event.code in self.handshake_profile.liveness_codes:
                    continue
                raise UnexpectedControllerReply(
                    "Unexpected control while collecting reply data",
                    event.unit.logical,
                )
            units.append(event.unit)
            self._validate_reply_progress(policy, units)
            if self._reply_complete(policy, units):
                return self._finish_explicit_reply(units, context)

    def _finish_explicit_reply(
        self,
        units: Sequence[InboundUnit],
        context: str,
    ) -> tuple[InboundUnit, ...]:
        excess = self._next_event(
            0.0,
            phase="reply-settle",
            context=context,
        )
        if excess is not None:
            raise UnexpectedControllerReply(
                "Controller data exceeded the explicit reply completion",
                excess.unit.logical,
            )
        return tuple(units)

    def _validate_reply_progress(
        self,
        policy: ReplyPolicy,
        units: Sequence[InboundUnit],
    ) -> None:
        if len(units) > policy.max_chunks:
            raise ReplyLimitError("Reply exceeded its wire-chunk limit")
        size = sum(len(unit.logical) for unit in units)
        if size > policy.max_bytes:
            raise ReplyLimitError("Reply exceeded its decoded-byte limit")
        if (
            policy.expected_chunks is not None
            and len(units) > policy.expected_chunks
        ):
            raise ReplyLimitError("Reply exceeded its expected chunk count")
        if policy.expected_bytes is not None:
            if size > policy.expected_bytes:
                raise ReplyLimitError("Reply exceeded its expected byte count")

    @staticmethod
    def _reply_complete(
        policy: ReplyPolicy,
        units: Sequence[InboundUnit],
    ) -> bool:
        if not units:
            return False
        payload = b"".join(unit.logical for unit in units)
        return policy.is_complete(payload, len(units))

    def _next_event(
        self,
        timeout: float,
        *,
        phase: str,
        context: str | None,
    ) -> _InboundEvent | None:
        if self._inbound:
            return self._inbound.popleft()
        unit = self.link.receive(timeout)
        if unit is None:
            return None
        self._notify(
            direction="receive",
            phase=phase,
            context=context,
            unit=unit,
        )
        event = self._classify(unit)
        self._inbound.append(event)
        return self._inbound.popleft()

    def _classify(self, unit: InboundUnit) -> _InboundEvent:
        logical = unit.logical
        is_control = (
            self.link.control_datagrams
            and len(logical) == 1
            and logical[0] in self.handshake_profile.control_codes
        )
        code = logical[0] if is_control else None
        return _InboundEvent(unit=unit, code=code)

    def _drain_locked(self) -> tuple[InboundUnit, ...]:
        self._inbound.clear()
        self._reply_buffer.clear()
        drained = []
        for _ in range(self.drain_limit):
            unit = self.link.receive(0.0)
            if unit is None:
                return tuple(drained)
            drained.append(unit)
            self._notify(
                direction="receive",
                phase="drain",
                context=None,
                unit=unit,
            )
        extra = self.link.receive(0.0)
        if extra is not None:
            self._notify(
                direction="receive",
                phase="drain",
                context=None,
                unit=extra,
            )
            raise DrainLimitError(self.drain_limit)
        return tuple(drained)

    def _require_ready(self) -> None:
        if self.state is SessionState.DESYNCHRONIZED:
            raise SessionDesynchronizedError(
                self._fault_reason or "unknown correlation failure"
            )
        if self.link.is_open and self.state is SessionState.CLOSED:
            raise ControllerError(
                "Controller session is not initialized; call open()"
            )
        if not self.link.is_open:
            raise ControllerError("Controller transport is not open")

    def _require_inactive_operation(self) -> None:
        if self._operation_active:
            raise UnsupportedExchangeError(
                "Controller operations cannot be started reentrantly"
            )

    def _begin_operation(self) -> None:
        self._require_inactive_operation()
        self._operation_active = True

    def _end_operation(self) -> None:
        self._operation_active = False

    def _require_empty_buffers(self) -> None:
        if self._inbound or self._reply_buffer:
            error = UnexpectedControllerReply(
                "An earlier exchange left buffered controller data"
            )
            self._set_desynchronized(str(error))
            raise error
        try:
            event = self._next_event(
                0.0,
                phase="preflight",
                context=None,
            )
        except OSError as cause:
            receipt = SendReceipt((), 0, 0, 0)
            error = ControllerTransportError(
                "Controller transport failed during exchange preflight",
                receipt,
                cause,
            )
            self._set_desynchronized(str(error))
            raise error from cause
        except Exception as cause:
            error = ControllerExchangeError(cause)
            error.receipt = SendReceipt((), 0, 0, 0)
            error.delivery_certainty = DeliveryCertainty.UNKNOWN
            self._set_desynchronized(str(error))
            raise error from cause
        except BaseException as error:
            setattr(error, "receipt", SendReceipt((), 0, 0, 0))
            setattr(error, "delivery_certainty", DeliveryCertainty.UNKNOWN)
            self._set_desynchronized(str(error))
            raise
        if event is not None:
            error = UnexpectedControllerReply(
                "Stale controller input preceded a new exchange",
                event.unit.logical,
            )
            self._set_desynchronized(str(error))
            raise error

    def _set_ready(self) -> None:
        self.state = SessionState.READY
        self._fault_reason = None

    def _set_closed(self) -> None:
        self.state = SessionState.CLOSED
        self._fault_reason = None
        self._inbound.clear()
        self._reply_buffer.clear()

    def _set_desynchronized(self, reason: str) -> None:
        self.state = SessionState.DESYNCHRONIZED
        self._fault_reason = reason

    def _notify(
        self,
        *,
        direction: EventDirection,
        phase: str,
        context: str | None,
        unit: OutboundPacket | InboundUnit,
        packet_index: int | None = None,
        attempt: int | None = None,
    ) -> None:
        if self.observer is None:
            return
        event = ExchangeEvent(
            direction=direction,
            phase=phase,
            link=self.link.name,
            context=context if direction == "send" else "reply",
            exchange_context=context,
            raw=unit.raw,
            logical=unit.logical,
            timestamp=time.monotonic(),
            packet_index=packet_index,
            attempt=attempt,
        )
        try:
            self.observer(event)
        except Exception:
            return

    def __enter__(self) -> ControllerClient:
        self.open()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, traceback
        try:
            self.close()
        except BaseException as cleanup_error:
            if exception is None:
                raise
            setattr(exception, "cleanup_error", cleanup_error)
