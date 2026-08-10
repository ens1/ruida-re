"""Canonical sample values for declarative command-shape tests."""

from ruida_re.fields import (
    AbsoluteMmField,
    ByteField,
    BytesField,
    ColorField,
    CStringField,
    PackedBytes8Field,
    PowerField,
    RelativeMmField,
    S7Field,
    S35Field,
    ScaledS32Field,
    ScaledU35Field,
    U14Field,
    U35Field,
)


def sample_value(field):
    if isinstance(field, BytesField):
        return bytes(field.size).hex()
    if isinstance(field, CStringField):
        return b"fixture".hex()
    if isinstance(field, PackedBytes8Field):
        return b"12345678".hex()
    if isinstance(field, ColorField):
        return 0x123456
    if isinstance(field, PowerField):
        return 37.5
    if isinstance(
        field,
        (AbsoluteMmField, ScaledS32Field, ScaledU35Field),
    ):
        return 12.345
    if isinstance(field, RelativeMmField):
        return -1.25
    if isinstance(field, S7Field):
        return -12
    if isinstance(field, S35Field):
        return -12345
    if isinstance(field, (ByteField, U14Field, U35Field)):
        return 42
    raise AssertionError(f"No sample for {field!r}")
