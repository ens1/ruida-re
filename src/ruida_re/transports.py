"""Controller I/O adapters with no protocol state or job planning."""

from __future__ import annotations

from collections.abc import Callable
import math
import socket
from typing import Any, Literal, Protocol, runtime_checkable


TransportKind = Literal["udp", "serial"]
DEFAULT_CONTROLLER_PORT = 50200
DEFAULT_LOCAL_PORT = 40200
DEFAULT_SERIAL_BAUD = 115200


class TransportUnavailableError(RuntimeError):
    """Raised when an optional transport dependency is unavailable."""


class DrainLimitError(RuntimeError):
    """Raised when input remains after a bounded transport drain."""

    def __init__(self, limit: int):
        super().__init__(
            f"Transport still had input after {limit} drained reads"
        )
        self.limit = limit


def _validate_timeout(timeout: float) -> None:
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout < 0
    ):
        raise ValueError("Receive timeout must be finite and nonnegative")


@runtime_checkable
class ControllerTransport(Protocol):
    """Minimal synchronous byte transport required by ControllerClient."""

    @property
    def kind(self) -> str:
        ...

    @property
    def is_open(self) -> bool:
        ...

    def open(self) -> None:
        ...

    def close(self) -> None:
        ...

    def send(self, data: bytes) -> None:
        ...

    def receive(self, timeout: float) -> bytes | None:
        ...

    def drain(self, limit: int = 256) -> tuple[bytes, ...]:
        ...


def _bounded_drain(
    receive: Callable[[float], bytes | None],
    limit: int,
) -> tuple[bytes, ...]:
    if type(limit) is not int or limit <= 0:
        raise ValueError("Drain limit must be positive")
    received = []
    for _ in range(limit):
        item = receive(0.0)
        if item is None:
            return tuple(received)
        received.append(item)
    if receive(0.0) is not None:
        raise DrainLimitError(limit)
    return tuple(received)


def _close_after_failure(resource: Any, error: BaseException) -> None:
    try:
        resource.close()
    except BaseException as cleanup_error:
        try:
            setattr(error, "cleanup_error", cleanup_error)
        except Exception:
            error.add_note(f"Cleanup also failed: {cleanup_error}")


def _restore_after_failure(
    restore: Callable[[], None],
    error: BaseException,
) -> None:
    try:
        restore()
    except BaseException as cleanup_error:
        try:
            setattr(error, "cleanup_error", cleanup_error)
        except Exception:
            error.add_note(f"Cleanup also failed: {cleanup_error}")


class UdpTransport:
    """Direct Ruida UDP adapter using ports 40200 and 50200."""

    kind: TransportKind = "udp"

    def __init__(
        self,
        host: str,
        *,
        controller_port: int = DEFAULT_CONTROLLER_PORT,
        local_host: str | None = None,
        local_port: int = DEFAULT_LOCAL_PORT,
        receive_size: int = 65535,
        socket_factory: Callable[..., socket.socket] = socket.socket,
    ) -> None:
        if not isinstance(host, str) or not host:
            raise ValueError("Controller host must be a nonempty string")
        if local_host is not None and (
            not isinstance(local_host, str) or not local_host
        ):
            raise ValueError("Local host must be a nonempty string")
        if (
            type(controller_port) is not int
            or not 1 <= controller_port <= 65535
        ):
            raise ValueError(
                "Controller port must be between 1 and 65535"
            )
        if type(local_port) is not int or not 0 <= local_port <= 65535:
            raise ValueError("Local port must fit a UDP port")
        if type(receive_size) is not int or receive_size < 65535:
            raise ValueError(
                "UDP receive size must hold a complete maximum datagram"
            )
        self.host = host
        self.controller_port = controller_port
        self.local_host = local_host
        self.local_port = local_port
        self.receive_size = receive_size
        self._socket_factory = socket_factory
        self._socket: socket.socket | None = None

    @property
    def is_open(self) -> bool:
        return self._socket is not None

    def open(self) -> None:
        if self.is_open:
            return
        route = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            route.connect((self.host, self.controller_port))
            bind_host = self.local_host or route.getsockname()[0]
        except BaseException as error:
            _close_after_failure(route, error)
            raise
        else:
            route.close()
        connection = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            try:
                connection.setsockopt(
                    socket.SOL_SOCKET,
                    socket.SO_RCVBUF,
                    131072,
                )
            except OSError:
                pass
            connection.bind((bind_host, self.local_port))
            connection.connect((self.host, self.controller_port))
        except BaseException as error:
            _close_after_failure(connection, error)
            raise
        self._socket = connection

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def send(self, data: bytes) -> None:
        connection = self._require_open()
        sent = connection.send(data)
        if sent != len(data):
            raise OSError(f"UDP socket sent {sent} of {len(data)} bytes")

    def receive(self, timeout: float) -> bytes | None:
        _validate_timeout(timeout)
        connection = self._require_open()
        previous_timeout = connection.gettimeout()
        connection.settimeout(timeout)
        try:
            result = connection.recv(self.receive_size)
        except (socket.timeout, BlockingIOError):
            result = None
        except BaseException as error:
            _restore_after_failure(
                lambda: connection.settimeout(previous_timeout),
                error,
            )
            raise
        connection.settimeout(previous_timeout)
        return result

    def drain(self, limit: int = 256) -> tuple[bytes, ...]:
        """Remove queued datagrams, subject to a read-count bound."""
        return _bounded_drain(self.receive, limit)

    def _require_open(self) -> socket.socket:
        if self._socket is None:
            raise OSError("UDP transport is not open")
        return self._socket

    def __enter__(self) -> UdpTransport:
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


class SerialTransport:
    """Ruida USB serial adapter with an optional pyserial dependency."""

    kind: TransportKind = "serial"

    def __init__(
        self,
        device: str,
        *,
        baudrate: int = DEFAULT_SERIAL_BAUD,
        receive_size: int = 65535,
        serial_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not isinstance(device, str) or not device:
            raise ValueError("Serial device must be a nonempty string")
        if type(baudrate) is not int or baudrate <= 0:
            raise ValueError("Baud rate must be positive")
        if type(receive_size) is not int or receive_size <= 0:
            raise ValueError("Receive size must be positive")
        self.device = device
        self.baudrate = baudrate
        self.receive_size = receive_size
        self._serial_factory = serial_factory
        self._serial: Any | None = None

    @property
    def is_open(self) -> bool:
        return bool(self._serial is not None and self._serial.is_open)

    def open(self) -> None:
        if self.is_open:
            return
        factory = self._serial_factory
        options: dict[str, object] = {
            "port": self.device,
            "baudrate": self.baudrate,
            "bytesize": 8,
            "parity": "N",
            "stopbits": 1,
            "timeout": 0,
        }
        if factory is None:
            try:
                import serial
            except ImportError as error:
                raise TransportUnavailableError(
                    "Serial support requires the 'serial' package extra"
                ) from error
            factory = serial.Serial
        if self._serial is not None:
            self._serial.close()
            self._serial = None
        connection = factory(**options)
        try:
            if not getattr(connection, "is_open", False):
                raise OSError(
                    "Serial factory returned a closed connection"
                )
        except BaseException as error:
            _close_after_failure(connection, error)
            raise
        self._serial = connection

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def send(self, data: bytes) -> None:
        connection = self._require_open()
        written = connection.write(data)
        if type(written) is not int or written != len(data):
            raise OSError(
                f"Serial port wrote {written} of {len(data)} bytes"
            )
        connection.flush()

    def receive(self, timeout: float) -> bytes | None:
        _validate_timeout(timeout)
        connection = self._require_open()
        previous_timeout = connection.timeout
        connection.timeout = timeout
        try:
            first = connection.read(1)
            if not first:
                result = None
            else:
                data = bytearray(first)
                capacity = self.receive_size - len(data)
                if capacity > 0:
                    waiting = int(getattr(connection, "in_waiting", 0))
                    if waiting > 0:
                        data.extend(
                            connection.read(min(waiting, capacity))
                        )
                result = bytes(data)
        except BaseException as error:
            _restore_after_failure(
                lambda: setattr(
                    connection,
                    "timeout",
                    previous_timeout,
                ),
                error,
            )
            raise
        else:
            connection.timeout = previous_timeout
        return result

    def drain(self, limit: int = 256) -> tuple[bytes, ...]:
        """Remove queued stream reads, subject to a read-count bound."""
        return _bounded_drain(self.receive, limit)

    def _require_open(self) -> Any:
        if not self.is_open:
            raise OSError("Serial transport is not open")
        return self._serial

    def __enter__(self) -> SerialTransport:
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
