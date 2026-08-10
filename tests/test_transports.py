"""Tests for direct Ruida controller I/O adapters."""

from __future__ import annotations

import socket
import unittest

from ruida_re.transports import (
    DrainLimitError,
    SerialTransport,
    UdpTransport,
)


class FakeSocket:
    def __init__(self, *args: object) -> None:
        self.bound: tuple[str, int] | None = None
        self.connected: tuple[str, int] | None = None
        self.closed = False
        self.timeout: float | None = None
        self.received: list[bytes | BaseException] = []
        self.sent: list[bytes] = []

    def connect(self, address: tuple[str, int]) -> None:
        self.connected = address

    def getsockname(self) -> tuple[str, int]:
        return "192.0.2.10", 49152

    def close(self) -> None:
        self.closed = True

    def setsockopt(self, *args: object) -> None:
        pass

    def bind(self, address: tuple[str, int]) -> None:
        self.bound = address

    def send(self, data: bytes) -> int:
        self.sent.append(data)
        return len(data)

    def recv(self, size: int) -> bytes:
        del size
        if not self.received:
            raise socket.timeout
        item = self.received.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def gettimeout(self) -> float | None:
        return self.timeout


class FakeSerial:
    def __init__(self, **options: object) -> None:
        self.options = options
        self.is_open = True
        self.timeout: float | None = 0
        self.received = bytearray()
        self.sent = bytearray()
        self.flushed = False
        self.read_sizes: list[int] = []

    @property
    def in_waiting(self) -> int:
        return len(self.received)

    def close(self) -> None:
        self.is_open = False

    def write(self, data: bytes) -> int:
        self.sent.extend(data)
        return len(data)

    def flush(self) -> None:
        self.flushed = True

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        data = bytes(self.received[:size])
        del self.received[:size]
        return data


class UdpTransportTest(unittest.TestCase):
    def test_receive_buffer_cannot_truncate_a_datagram(self) -> None:
        for receive_size in (1, 65534, True):
            with self.subTest(receive_size=receive_size):
                with self.assertRaises(ValueError):
                    UdpTransport(
                        "192.0.2.20",
                        receive_size=receive_size,
                    )

    def test_endpoints_require_nonempty_strings(self) -> None:
        for host in (None, "", 1):
            with self.subTest(host=host):
                with self.assertRaises(ValueError):
                    UdpTransport(host)
        for local_host in ("", 1):
            with self.subTest(local_host=local_host):
                with self.assertRaises(ValueError):
                    UdpTransport("192.0.2.20", local_host=local_host)

    def test_controller_destination_port_cannot_be_ephemeral(self) -> None:
        with self.assertRaises(ValueError):
            UdpTransport("192.0.2.20", controller_port=0)
        transport = UdpTransport("192.0.2.20", local_port=0)
        self.assertEqual(transport.local_port, 0)

    def test_uses_controller_and_listener_ports(self) -> None:
        sockets: list[FakeSocket] = []

        def factory(*args: object) -> FakeSocket:
            item = FakeSocket(*args)
            sockets.append(item)
            return item

        transport = UdpTransport("192.0.2.20", socket_factory=factory)
        transport.open()
        self.assertEqual(len(sockets), 2)
        self.assertEqual(sockets[1].bound, ("192.0.2.10", 40200))
        self.assertEqual(sockets[1].connected, ("192.0.2.20", 50200))
        transport.send(b"packet")
        self.assertEqual(sockets[1].sent, [b"packet"])
        sockets[1].received.append(b"reply")
        self.assertEqual(transport.receive(0.25), b"reply")
        self.assertIsNone(transport.receive(0.25))
        transport.close()
        self.assertFalse(transport.is_open)

    def test_drain_is_bounded(self) -> None:
        sockets: list[FakeSocket] = []

        def factory(*args: object) -> FakeSocket:
            item = FakeSocket(*args)
            sockets.append(item)
            return item

        transport = UdpTransport("192.0.2.20", socket_factory=factory)
        transport.open()
        sockets[1].received.extend([b"one", b"two", b"three"])
        with self.assertRaises(DrainLimitError):
            transport.drain(limit=2)

    def test_drain_rejects_an_invalid_limit(self) -> None:
        sockets: list[FakeSocket] = []

        def factory(*args: object) -> FakeSocket:
            item = FakeSocket(*args)
            sockets.append(item)
            return item

        transport = UdpTransport("192.0.2.20", socket_factory=factory)
        transport.open()
        with self.assertRaises(ValueError):
            transport.drain(limit=0)
        with self.assertRaises(ValueError):
            transport.drain(limit=True)
        for timeout in (True, float("inf"), float("nan")):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    transport.receive(timeout)

    def test_open_closes_a_socket_when_setup_is_interrupted(self) -> None:
        sockets: list[FakeSocket] = []

        class InterruptedSocket(FakeSocket):
            def bind(self, address: tuple[str, int]) -> None:
                del address
                raise KeyboardInterrupt

        def factory(*args: object) -> FakeSocket:
            socket_type = FakeSocket if not sockets else InterruptedSocket
            item = socket_type(*args)
            sockets.append(item)
            return item

        transport = UdpTransport("192.0.2.20", socket_factory=factory)
        with self.assertRaises(KeyboardInterrupt):
            transport.open()
        self.assertTrue(all(item.closed for item in sockets))
        self.assertFalse(transport.is_open)


class SerialTransportTest(unittest.TestCase):
    def test_device_requires_a_nonempty_string(self) -> None:
        for device in (None, "", 1):
            with self.subTest(device=device):
                with self.assertRaises(ValueError):
                    SerialTransport(device)

    def test_uses_115200_8n1_compatible_defaults(self) -> None:
        connections: list[FakeSerial] = []

        def factory(**options: object) -> FakeSerial:
            item = FakeSerial(**options)
            connections.append(item)
            return item

        transport = SerialTransport(
            "/dev/ttyUSB0",
            serial_factory=factory,
        )
        transport.open()
        self.assertEqual(connections[0].options["baudrate"], 115200)
        self.assertEqual(connections[0].options["bytesize"], 8)
        self.assertEqual(connections[0].options["parity"], "N")
        self.assertEqual(connections[0].options["stopbits"], 1)
        transport.send(b"packet")
        self.assertEqual(connections[0].sent, b"packet")
        self.assertTrue(connections[0].flushed)
        connections[0].received.extend(b"reply")
        self.assertEqual(transport.receive(0.25), b"reply")
        self.assertEqual(connections[0].read_sizes[-2:], [1, 4])
        transport.close()
        self.assertFalse(transport.is_open)

    def test_rejects_a_closed_factory_result(self) -> None:
        connections: list[FakeSerial] = []

        def factory(**options: object) -> FakeSerial:
            item = FakeSerial(**options)
            item.is_open = False
            connections.append(item)
            return item

        transport = SerialTransport(
            "/dev/ttyUSB0",
            serial_factory=factory,
        )
        with self.assertRaises(OSError):
            transport.open()
        self.assertFalse(transport.is_open)
        self.assertFalse(connections[0].is_open)

    def test_send_requires_an_exact_integer_write_count(self) -> None:
        class MissingCountSerial(FakeSerial):
            def write(self, data: bytes) -> None:
                del data
                return None

        connection = MissingCountSerial()
        transport = SerialTransport(
            "/dev/ttyUSB0",
            serial_factory=lambda **_options: connection,
        )
        transport.open()
        with self.assertRaises(OSError):
            transport.send(b"packet")

    def test_open_closes_serial_when_validation_is_interrupted(self) -> None:
        class InterruptedSerial:
            closed = False

            @property
            def is_open(self) -> bool:
                raise KeyboardInterrupt

            def close(self) -> None:
                self.closed = True

        connection = InterruptedSerial()
        transport = SerialTransport(
            "/dev/ttyUSB0",
            serial_factory=lambda **_options: connection,
        )
        with self.assertRaises(KeyboardInterrupt):
            transport.open()
        self.assertTrue(connection.closed)
        self.assertFalse(transport.is_open)


if __name__ == "__main__":
    unittest.main()
