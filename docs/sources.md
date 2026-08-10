# Sources and provenance

`ruida-re` is an original, schema-driven implementation released under the
MIT license. Existing projects are used as factual leads and executable
comparison oracles; their parser control flow, job builders, text formats, and
source expression were not translated into this repository.

## Primary fixtures

- LightBurn 2.1.03 on macOS.
- User-configured `Ruida 644XS` profile.
- One baseline and 12 one-variable exports checked into
  `fixtures/lightburn-2.1.03`.
- Project and machine-file SHA-256 values recorded in adjacent JSON manifests.
- LightBurn application SHA-256, complete profile dimensions/mirroring, and
  per-file generation stage recorded in those manifests. The baseline project
  is the generated input; matrix projects are the post-LightBurn normalized
  form saved after export.

These fixtures decide local shape and value questions. Merely seeing an opcode
establishes its frame shape for this LightBurn dialect, not its mnemonic. That
distinction is represented by separate `shape_evidence` and
`semantic_evidence` fields.

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
