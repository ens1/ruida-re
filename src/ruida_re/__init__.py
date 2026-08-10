"""Public API for lossless Ruida protocol translation."""

from .codec import swizzle, unswizzle
from .program import (
    KnownCommand,
    Program,
    RawSpan,
    decode,
    decode_path,
)
from .registry import get_registry
from .stream import StreamDecoder
from .transport import (
    decode_datagram,
    decode_packet,
    encode_datagram,
    encode_packet,
)


__all__ = (
    "KnownCommand",
    "Program",
    "RawSpan",
    "StreamDecoder",
    "decode",
    "decode_datagram",
    "decode_packet",
    "decode_path",
    "encode_datagram",
    "encode_packet",
    "get_registry",
    "swizzle",
    "unswizzle",
)
