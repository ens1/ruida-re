# Protocol notes

These notes distinguish stream grammar, observed field layout, and reported
meaning. Bytes shown below are logical bytes after unscrambling unless a table
explicitly says raw wire bytes.

## Layers

The implementation keeps these transformations separate:

1. Logical command framing.
2. Optional semantic decoding of a complete frame.
3. Reversible byte scrambling with a controller-specific magic byte.
4. Optional ten-byte `RDWORKV` file wrapping.
5. Direction-aware UDP framing and outbound additive checksums.
6. Context-selected host, request, and reply semantic registries.

The JSON IR records `container` as `rd`, `udp`, or `logical`. A single UDP
datagram can therefore be decoded, edited, and re-encoded with direction-aware
wire framing at the correct scrambled layer. Outbound job and request
datagrams carry a checksum prefix; reply datagrams do not. Multi-datagram
capture files still require an external record boundary or capture format.

This is important for correctness. A reported opcode shape is never allowed to
determine where the following command begins.

## Logical framing hypothesis

Every checked-in LightBurn fixture satisfies one lexical rule: a logical frame
begins with a high-bit byte, then contains only seven-bit bytes until the next
high-bit byte. The implementation treats that rule as the current grammar,
not as a universal claim about every controller dialect.

The decoder first splits the complete stream with only this invariant. It then
tries every semantic shape registered for the frame's opcode, longest opcode
first. A shape is accepted only when:

- every field is valid;
- it consumes the complete frame; and
- exactly one shape matches.

Otherwise the complete frame remains opaque. Leading low-bit bytes also remain
opaque. Known frames retain their original bytes, so even a noncanonical field
encoding is reused until its structured values are edited.

The streaming decoder uses the same rule and waits for the next high-bit byte
before committing the preceding frame. Consequently its result is independent
of UDP or read-buffer split positions.

Ruida-pa contains an unresolved comment describing `D0 29 89 89`, which would
contradict this grammar if those are all logical bytes in one command. No
fixture or capture here reproduces it. The decoder does not special-case that
report: those bytes remain exactly recoverable as opaque frames. If a capture
confirms high-bit operands, the lexical grammar must change at this layer.

## Primitive fields

- U14 and S14 use two base-128 digits, most significant first.
- U35 uses five base-128 digits, most significant first.
- Relative coordinates are signed 14-bit micrometers, spanning -8.192 mm
  through 8.191 mm.
- Reference implementations interpret coordinate-style five-group fields as
  having 32 meaningful bits. The decoder accepts both zero-padded and correctly
  sign-extended negative 32-bit forms, while retaining exact source bytes. The
  canonical encoder currently emits the zero-padded form. Checked fixtures use
  only nonnegative zero-padded values; a negative-coordinate LightBurn export
  is still needed to establish LightBurn's spelling.
- Coordinates and speeds observed in fixtures store their physical value
  multiplied by 1000.
- Power uses U14 scaled by 16383 for 100%. Controlled 1%, 50%, 99%, and 100%
  exports distinguish that denominator from 16384.
- Colors are 24-bit BGR integers carried in five seven-bit groups and exposed
  as RGB integers.
- File names are zero-terminated seven-bit byte strings.
- Element names are eight source bytes packed into two five-group integers.

Field classes reject high-bit operands. This makes the structured encoder and
decoder symmetric and prevents a truncated command from consuming the next
command-start byte.

## Byte scrambling

For each logical byte, swap bits 0 and 7, XOR a magic value, and add one modulo
256. The common file magic is `0x88`. The operation is independently reversible
for every byte.

With magic `0x88`:

| Reported meaning | Logical | Raw wire |
| --- | --- | --- |
| Acknowledge | `CC` | `C6` |
| Error | `CD` | `46` |
| Keep alive | `CE` | `C8` |
| Negative acknowledge | `CF` | `48` |

Capture-derived implementations disagree on whether a packet-checksum
rejection is logical `CD` or `CF`. That behavior requires a controller capture;
the codec does not silently equate them.

## Checksums

An outbound UDP job or request packet is:

```text
big-endian-u16(sum(scrambled-payload) mod 65536) + scrambled-payload
```

A reply UDP datagram is only the scrambled reply payload, without the
two-byte checksum prefix.

The commonly reported maximum payload is 1470 bytes. A command can cross
packet boundaries; packet boundaries therefore do not participate in command
framing.

The LightBurn `E5 05` job checksum is separate. Across all 13 checked-in
exports it equals the sum of every logical job byte except the seven-byte
`E5 05` frame itself, including the final `D7`. Checksum policy is explicit:
`preserve` retains the represented value, while `recompute` always derives a
single checksum from the outgoing logical stream. The encoder never infers
intent from editable JSON metadata.

## Controlled LightBurn evidence

The baseline project is one line from `(20, 20)` to `(30, 20)` mm at 10 mm/s,
with 10% minimum and 20% maximum power. LightBurn 2.1.03 emitted 492 bytes and
70 frames. All frames have a unique registered shape and the file reproduces
exactly.

Selected logical frames are:

```text
0092 C9 04 00 00 00 00 4E 10           layer speed
0100 C6 31 00 0C 66                    layer laser 1 minimum power
0105 C6 32 00 19 4D                    layer laser 1 maximum power
0445 88 00 00 01 1C 20 00 00 01 1C 20 absolute move
0456 A8 00 00 01 6A 30 00 00 01 1C 20 absolute cut
0484 E5 05 00 00 01 27 75              job checksum: 21493
0491 D7                                  end of file
```

The one-variable matrix establishes:

- power raw values 16, 164, 8192, 16219, and 16383 for requested 0%, 1%, 50%,
  99%, and 100%; LightBurn clamped the requested 0% case to 16, while raw zero
  remains representable and controller behavior at zero is untested;
- exact thousandth precision for 12.345 mm/s and a raw value of 100 for
  0.1 mm/s;
- `CA 01 12` and `CA 01 13` for air off and on in this job mode;
- geometry and min/max bounds under X, Y, and vertical changes; and
- a lone UI layer C01 is compacted to RD job layer 0, while its blue display
  color remains in `CA 06`.

The matrix contains 12 exports plus the baseline, covering 65 unique shapes.
Shape observation and semantic evidence are separate metadata axes. For
example, `D8 00` and `F0` occur in every file, but their historical mnemonic
interpretations remain disputed.

## Registry coverage

The current registries contain:

- 184 broad host/job command hypotheses;
- 74 provisional request candidates; and
- 10 reported, disputed, or simulator-only reply shapes.

The host/job catalog covers motion, cut motion, power, speed, timing, layer
metadata, process control, memory access, document metadata, arrays, elements,
checksum, and file termination. The request view is a reported opcode-family
subset, not a complete direction classifier. No real controller request/reply
capture corpus is checked in.

Each registry record exposes:

- `shape_evidence`: whether its byte shape was seen in a fixture, merely
  reported, conflicts across reports, or remains an uncited hypothesis;
- `semantic_evidence`: whether values were controlled in fixtures, reported,
  or disputed;
- separate shape and semantic sources, plus notes for known disagreements.

## Unresolved evidence

These are research tasks, not parser exceptions:

- Canonical negative five-group coordinate spelling needs a LightBurn export.
- `E7 04`, `E7 08`, and `F2 05` have a stable 14-byte payload, but prior
  projects describe the fields differently. The baseline supports two U14
  counts followed by two coordinate-style values.
- `D8 00`, `D8 12`, and `F0` have stable shapes and conflicting names.
- `C6 15` and `C6 16` have stable shapes but disputed timing meaning, so their
  names remain neutral delay-set labels.
- Several E8, DA05, E732, and D90F payload layouts differ across reported
  controller dialects. Far-axis identities are disputed even where a field
  width is reported. The disagreement is labelled rather than selected
  silently.
- `DA 01` replies can depend on the requested memory address. MeerK40t's
  emulator synthesizes a CString for `DA 01 05 7F`, while ruida-pa models a
  fixed numeric reply. Both hypotheses conflict and no hardware capture here
  resolves them; unmatched forms remain opaque.
- Raster, grayscale, and depth-map jobs reported so far use motion plus power
  changes rather than a proven native pixel payload. Recovering an original
  source image from an optimized motion stream is not generally reversible.

Generated multilayer, relative-motion, and negative-coordinate projects are
ready for the next LightBurn export pass. Additional matrices should cover two
active layers, arrays, multiple lasers, rotary axes, raster modes, grayscale,
depth maps, real request/reply captures, and complete UDP transactions.
