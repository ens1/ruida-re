"""Stable integration API for embedding the Ruida codec."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Literal, Sequence

from .codec import swizzle
from .program import (
    CONTAINERS,
    KnownCommand,
    Program,
    RawSpan,
    Record,
    decode,
)
from .registry import get_registry
from .specs import CommandRegistry
from .stream import StreamDecoder
from .transport import (
    DEFAULT_MTU,
    decode_datagram,
    frame,
    payload_chunks,
)


Context = Literal["job", "request", "reply"]
Container = Literal["rd", "udp", "logical"]


@dataclass(frozen=True)
class RuidaCodec:
    """Configured encoder and decoder for one Ruida protocol context."""

    magic: int = 0x88
    context: Context = "job"
    registry: CommandRegistry | None = None

    def __post_init__(self) -> None:
        if isinstance(self.magic, bool) or not 0 <= self.magic <= 0xFF:
            raise ValueError("Magic value must fit in one byte")
        if self.context not in ("job", "request", "reply"):
            raise ValueError(f"Unknown protocol context: {self.context}")
        if self.registry is None:
            object.__setattr__(self, "registry", get_registry(self.context))

    @property
    def command_names(self) -> tuple[str, ...]:
        """Return every structured command name available in this context."""
        return tuple(spec.name for spec in self._registry)

    @property
    def _registry(self) -> CommandRegistry:
        if self.registry is None:
            raise AssertionError("Codec registry was not initialized")
        return self.registry

    def decode(self, data: bytes, container: Container = "rd") -> Program:
        """Decode bytes at a declared protocol layer into a lossless IR."""
        return decode(
            data,
            magic=self.magic,
            registry=self._registry,
            context=self.context,
            container=container,
        )

    def stream_decoder(self) -> StreamDecoder:
        """Create an incremental decoder for logical stream chunks."""
        return StreamDecoder(self._registry)

    def command(self, name: str, **values: object) -> KnownCommand:
        """Create and validate one structured command by its stable name."""
        spec = self._registry.name(name)
        if spec is None:
            raise ValueError(
                f"Unknown {self.context} command: {name}"
            )
        copied_values = spec.normalize_values(dict(values))
        spec.encode(copied_values)
        return KnownCommand(
            offset=0,
            opcode=spec.opcode.hex(),
            name=spec.name,
            values=copied_values,
            shape_evidence=spec.shape_evidence,
            semantic_evidence=spec.semantic_evidence,
        )

    def opaque(self, data: bytes) -> RawSpan:
        """Create a verbatim record for bytes without a semantic schema."""
        return RawSpan(offset=0, raw=data.hex())

    def program(
        self,
        records: Sequence[Record],
        *,
        container: Container = "logical",
        header: bytes = b"",
    ) -> Program:
        """Build a Program and assign canonical logical byte offsets."""
        if container not in CONTAINERS:
            raise ValueError(f"Unknown container: {container}")
        if container != "rd" and header:
            raise ValueError(
                f"The {container} container cannot have an RDWORKV header"
            )
        positioned: list[Record] = []
        offset = 0
        for record in records:
            current = replace(record, offset=offset)
            positioned.append(current)
            offset += len(current.encode(self._registry))
        return Program(
            magic=self.magic,
            context=self.context,
            container=container,
            header=header.hex(),
            records=positioned,
            _registry=self._registry,
        )

    def encode(
        self,
        program: Program,
        *,
        container: Container | None = None,
        checksum_policy: str = "preserve",
    ) -> bytes:
        """Encode a Program using this codec's context and magic value."""
        if program.magic != self.magic:
            raise ValueError(
                f"Program magic 0x{program.magic:02x} does not match "
                f"codec magic 0x{self.magic:02x}"
            )
        if program.context != self.context:
            raise ValueError(
                f"Program context {program.context} does not match "
                f"codec context {self.context}"
            )
        output = replace(
            program,
            container=container or program.container,
        )
        return output.encode(
            registry=self._registry,
            checksum_policy=checksum_policy,
        )

    def encode_commands(
        self,
        records: Sequence[Record],
        *,
        container: Container = "logical",
        header: bytes = b"",
        checksum_policy: str = "preserve",
    ) -> bytes:
        """Build and encode a sequence of structured or opaque records."""
        program = self.program(
            records,
            container=container,
            header=header,
        )
        return self.encode(program, checksum_policy=checksum_policy)

    def encode_datagrams(
        self,
        program: Program,
        *,
        mtu: int = DEFAULT_MTU,
        checksum_policy: str = "preserve",
    ) -> tuple[bytes, ...]:
        """Encode a Program into transport datagrams without sending them."""
        if mtu <= 0:
            raise ValueError("MTU must be positive")
        logical_program = replace(
            program,
            container="logical",
            header="",
        )
        logical = self.encode(
            logical_program,
            checksum_policy=checksum_policy,
        )
        chunks = tuple(payload_chunks(logical, mtu))
        if self.context == "reply":
            return tuple(swizzle(chunk, self.magic) for chunk in chunks)
        return tuple(
            frame(swizzle(chunk, self.magic)) for chunk in chunks
        )

    def decode_datagrams(self, datagrams: Iterable[bytes]) -> Program:
        """Decode ordered datagrams as one logical command stream."""
        logical = b"".join(
            decode_datagram(datagram, self.context, self.magic)
            for datagram in datagrams
        )
        return self.decode(logical, container="logical")
