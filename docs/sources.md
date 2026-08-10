# Sources and provenance

`ruida-re` is an original, schema-driven implementation released under the
MIT license. Existing projects are used as factual leads and executable
comparison oracles; their parser control flow, job builders, text formats, and
source expression were not translated into this repository.

## Primary fixtures

- LightBurn 2.1.03 on macOS.
- User-configured `Ruida 644XS` profile.
- One baseline, 12 one-variable exports, and two advanced exports checked into
  `fixtures/lightburn-2.1.03`.
- The advanced corpus includes a real two-layer file and signed relative X/Y
  motion. A third generated project crosses negative machine X; LightBurn
  rejects it as out of bounds, so no `.rd` is claimed for that case.
- Project and machine-file SHA-256 values recorded in adjacent JSON manifests.
- LightBurn application SHA-256, complete profile dimensions/mirroring, and
  per-file generation stage recorded in those manifests. The baseline project
  is the generated input; matrix projects are the post-LightBurn normalized
  form saved after export.

These fixtures decide local shape and value questions. Merely seeing an opcode
establishes its frame shape for this LightBurn dialect, not its mnemonic. That
distinction is represented by separate `shape_evidence` and
`semantic_evidence` fields.

No RDWorks fixture is currently claimed. Future Windows exports will be kept
as a separately versioned, producer-labelled corpus and compared against the
same neutral Program, catalog, and conformance contracts; they will not be
silently treated as equivalent to the LightBurn dialect.

## Permissive references

- [`StevenIsaacs/ruida-pa` at
  `92efde9`](https://github.com/StevenIsaacs/ruida-pa/tree/92efde98004d9948474eb712ef6f5b164f468c4f),
  MIT. Used to audit opcode and field hypotheses, reply directions, and
  controller-memory reports. Its pinned analyzer states that replies omit the
  checksum, while its transport applies the checksum only to outbound UDP
  data: [analyzer](https://github.com/StevenIsaacs/ruida-pa/blob/92efde98004d9948474eb712ef6f5b164f468c4f/protocols/ruida/ruida_analyzer.py#L213-L228),
  [transport](https://github.com/StevenIsaacs/ruida-pa/blob/92efde98004d9948474eb712ef6f5b164f468c4f/ruidadriver/rd_transport.py#L187-L213).
- [`meerk40t/meerk40t` at
  `5f68a45`](https://github.com/meerk40t/meerk40t/tree/5f68a45bff41d98e4d3fe8b8267857218099afa8),
  MIT. Used as a command-layout, scrambling, checksum, and encoder comparison
  oracle.
- [`barebaric/rayforge` at
  `f20f8cf`](https://github.com/barebaric/rayforge/tree/f20f8cfe42f6789dc984981f9cd102c3909fb6af),
  MIT. Used to compare card magic values, packet framing, and simulator
  hypotheses.

MIT code was not wholesale copied. Protocol facts are re-expressed through the
independent frame grammar, field codecs, registry, and tests in this project.

One ruida-pa table contains an unresolved comment about logical bytes
`D0 29 89 89`, which conflicts with the current seven-bit-operand grammar:
[pinned table](https://github.com/StevenIsaacs/ruida-pa/blob/92efde98004d9948474eb712ef6f5b164f468c4f/protocols/ruida/ruida_protocol.py#L505-L507).
It is recorded as contrary evidence, not implemented as an opcode exception.

## Transport evidence

Transport defaults and exchange behavior are taken from pinned source, not an
unversioned wiki or a moving branch:

- Ruida-pa's direct UDP adapter sends to port 50200 and binds the routed local
  address on port 40200: [pinned UDP
  adapter](https://github.com/StevenIsaacs/ruida-pa/blob/92efde98004d9948474eb712ef6f5b164f468c4f/ruidadriver/transport/udp_transport.py#L15-L42).
  MeerK40t independently labels the same two ports as controller-defined:
  [pinned UDP
  transport](https://github.com/meerk40t/meerk40t/blob/5f68a45bff41d98e4d3fe8b8267857218099afa8/meerk40t/ruida/udp_transport.py#L16-L23).
- Ruida-pa's USB adapter opens a pyserial connection at 115200 baud, eight data
  bits, no parity, and one stop bit: [pinned USB-serial
  adapter](https://github.com/StevenIsaacs/ruida-pa/blob/92efde98004d9948474eb712ef6f5b164f468c4f/ruidadriver/transport/usb_transport.py#L36-L44).
- Its shared transport scrambles both paths but prepends the additive checksum
  only for UDP: [pinned packet
  construction](https://github.com/StevenIsaacs/ruida-pa/blob/92efde98004d9948474eb712ef6f5b164f468c4f/ruidadriver/rd_transport.py#L185-L193).
  The same implementation enters ACK handling only for UDP and explicitly
  proceeds without an ACK on USB serial: [pinned send
  state](https://github.com/StevenIsaacs/ruida-pa/blob/92efde98004d9948474eb712ef6f5b164f468c4f/ruidadriver/rd_transport.py#L274-L291).
- MeerK40t also constructs UDP packets as a big-endian additive checksum over
  scrambled data: [pinned packet
  construction](https://github.com/meerk40t/meerk40t/blob/5f68a45bff41d98e4d3fe8b8267857218099afa8/meerk40t/ruida/udp_connection.py#L145-L147).
  Its handshaker describes serialized send/ACK/reply operation without
  sequence numbers and resends on NAK: [pinned state-machine
  description](https://github.com/meerk40t/meerk40t/blob/5f68a45bff41d98e4d3fe8b8267857218099afa8/meerk40t/ruida/udp_connection.py#L204-L229),
  [pinned ACK/NAK
  handling](https://github.com/meerk40t/meerk40t/blob/5f68a45bff41d98e4d3fe8b8267857218099afa8/meerk40t/ruida/udp_connection.py#L256-L305).

These links support the adapter defaults and the current session policy. They
are not a substitute for controller captures. In particular, timeout behavior,
the meanings of logical `CD` and `CF`, reply termination, and differences
among controller models remain evidence-labelled research questions.

## Read-only setting request evidence

The installed CLI exposes `DA 00` only because both pinned implementations
distinguish it from the state-changing `DA 01` operation and expect reply
data:

- ruida-pa labels controller memory readable via `DA`, defines `DA 00` as
  `GET_SETTING`/read and `DA 01` as `SET_SETTING`/write, then parses fixed
  `DA 01` replies: [pinned command
  table](https://github.com/StevenIsaacs/ruida-pa/blob/92efde98004d9948474eb712ef6f5b164f468c4f/protocols/ruida/ruida_protocol.py#L201-L205),
  [pinned DA entries](https://github.com/StevenIsaacs/ruida-pa/blob/92efde98004d9948474eb712ef6f5b164f468c4f/protocols/ruida/ruida_protocol.py#L547-L550),
  [pinned reply parser](https://github.com/StevenIsaacs/ruida-pa/blob/92efde98004d9948474eb712ef6f5b164f468c4f/ruidadriver/rd_transport.py#L195-L213).
- MeerK40t's emulator handles `DA 00` as a memory lookup followed by a
  `DA 01` response, while its `DA 01` branch writes values:
  [pinned emulator](https://github.com/meerk40t/meerk40t/blob/5f68a45bff41d98e4d3fe8b8267857218099afa8/meerk40t/ruida/emulator.py#L661-L696).

This establishes independent implementation evidence, not a hardware capture
or a guarantee that every address and controller dialect uses the same reply
shape. The catalog therefore labels both command semantics `reported` and
keeps their source identifiers explicit.

## Other comparison oracles

### LibLaserCut

[`t-oster/LibLaserCut` at
`ebe72ea`](https://github.com/t-oster/LibLaserCut/tree/ebe72ea3af3b2ab52d797d8100c635f68722100e)
is LGPL-3.0-or-later. Its 1012-byte golden file is not redistributed here. The
exact audit input is:

```text
test-output/de.thomas_oster.liblasercut.drivers.Ruida.out
SHA-256 5842a78ecb9abd195db502551b95de4d410cebe16cf2212fbad8d7bcf32a0500
```

It can be downloaded at that pinned commit and checked with:

```sh
ruida-verify /tmp/liblasercut-ruida.out \
  --expected-sha256 5842a78ecb9abd195db502551b95de4d410cebe16cf2212fbad8d7bcf32a0500 \
  --require-structured
```

The current result is 123 known frames, zero opaque frames, exact direct and
JSON round trips. No LibLaserCut Java source or golden binary is included.

### ruida-laser

[`jnweiger/ruida-laser` at
`a1e7b9b`](https://github.com/jnweiger/ruida-laser/tree/a1e7b9b93b10d5cac79c875bc3efec46f7397a11)
is treated as GPL-2.0-only. It was used only as an executable disassembly and
documentation oracle. No source, table structure, or tests were copied.

## Disagreement policy

When references disagree:

1. The current lexical hypothesis preserves every byte independently of
   semantic recognition.
2. Conflicting shapes are labelled rather than silently merged.
3. Ambiguous semantic matches remain opaque.
4. A controlled fixture or hardware capture is required before evidence
   metadata is promoted.

If a capture contradicts the lexical hypothesis itself, the grammar is revised
at the framing layer. Opcode-specific parser exceptions are not added.

This keeps exact translation available while semantic knowledge grows without
turning one prior implementation's assumptions into protocol truth.
