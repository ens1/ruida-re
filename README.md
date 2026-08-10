# ruida-re

`ruida-re` is a standalone Ruida protocol encoder, decoder, and research
harness. It is intentionally independent of Rayforge and of any particular
laser application.

The project separates two different claims:

1. **Syntactic translation is lossless.** All checked-in fixtures follow the
   current lexical hypothesis: after unscrambling, a frame starts with a
   high-bit byte and continues through seven-bit bytes until the next high-bit
   byte. The decoder applies that rule before semantic lookup. A known shape is
   accepted only when it consumes one complete frame. Unknown, malformed,
   ambiguous, and future frames remain opaque. Regardless of semantic
   recognition, unchanged `encode(decode(data)) == data`.
2. **Semantic translation is evidence-labelled.** Shape evidence and mnemonic
   evidence are recorded separately. A frame observed in LightBurn output is
   not automatically presented as having a proven meaning. Controlled fixture
   results, reported meanings, and disagreements remain distinguishable in
   both JSON and the command registry.

The current registries contain 184 host/job shapes, 74 provisional request
candidates, and 10 reported or simulated reply shapes. Thirteen checked-in
LightBurn exports exercise 65 unique job shapes; all 13 parse without opaque
frames and reproduce byte-for-byte. A temporary LibLaserCut golden file
exercises 123 frames with the same result. Those results validate the current
framing model and exact translation, not every mnemonic or direction in the
broader catalog.

This repository never connects to a controller or starts a laser job.

## Install

From a checkout:

```sh
python3 -m pip install -e .
```

Python 3.11 or newer is required. The runtime has no third-party dependencies.

## Translate

Decode a scrambled `.rd` file to editable JSON:

```sh
ruida-decode input.rd job.json
```

Encode unedited JSON back to `.rd`, preserving its checksum:

```sh
ruida-encode job.json output.rd
```

After structured edits, explicitly request a canonical checksum:

```sh
ruida-encode --checksum recompute job.json output.rd
```

Unedited JSON reproduces the input exactly, including an optional ten-byte
`RDWORKV` wrapper, original field encodings, and opaque frames. Editing a known
record regenerates that frame. Checksum behavior is explicit: `preserve` never
infers intent from editable JSON metadata, while `recompute` always replaces
the single `E5 05` value from the outgoing logical stream.

Both translation commands reject identical input/output paths and refuse to
replace an existing output unless `--force` is supplied. Writes are atomic.
`ruida-decode --strict` returns a failure status if any frame is opaque or
invalid while still producing the diagnostic JSON.

Select a context-specific semantic registry for an unscrambled stream:

```sh
ruida-decode --container logical --context request request.bin request.json
ruida-decode --container logical --context reply response.bin response.json
```

The JSON document also records its byte container. Decode one complete UDP
datagram or an already-unscrambled logical stream with:

```sh
ruida-decode --container udp --context request request.bin request.json
ruida-decode --container udp --context reply packet.bin packet.json
ruida-decode --container logical logical.bin logical.json
```

The context determines wire framing: request and job datagrams have a two-byte
checksum prefix, while reply datagrams contain only scrambled reply bytes.
`ruida-encode` reproduces the recorded container. `--container` can override
it when deliberately translating between layers; an `RDWORKV` header must be
cleared before changing a wrapped file to `udp` or `logical`.

The host/job catalog is deliberately broad. The request view is still a
reported opcode-family subset, not a complete direction classifier, and no
real request/reply capture corpus is checked in yet.

Inspect the evidence-labelled registry:

```sh
ruida-spec --context job
ruida-spec --context request
ruida-spec --context reply
```

Compare two exports after unscrambling them:

```sh
ruida-diff before.rd after.rd --commands
```

Produce a reproducible exact-translation report for any external fixture:

```sh
ruida-verify input.rd --expected-sha256 DIGEST --require-structured
```

## Test

```sh
PYTHONPATH=src:tests python3 -m unittest discover -s tests -v
```

The suite covers byte scrambling, numeric boundaries, signed coordinate
forms, synthetic encode/decode symmetry for every registered schema, every
split point of every registered shape, semantic-frame isolation, arbitrary
binary and JSON round trips, packet framing, checksum updates, and real
LightBurn output.

The LibLaserCut comparison can be repeated without checking its LGPL fixture
into this repository:

```sh
curl -L \
  https://raw.githubusercontent.com/t-oster/LibLaserCut/ebe72ea3af3b2ab52d797d8100c635f68722100e/test-output/de.thomas_oster.liblasercut.drivers.Ruida.out \
  -o /tmp/liblasercut-ruida.out
ruida-verify /tmp/liblasercut-ruida.out \
  --expected-sha256 5842a78ecb9abd195db502551b95de4d410cebe16cf2212fbad8d7bcf32a0500 \
  --require-structured
```

## LightBurn fixture workflow

LightBurn is used as a reference compiler, not as a runtime dependency.

Generate fresh baseline and discovery projects under `work/`:

```sh
ruida-fixture generate
ruida-matrix generate
ruida-advanced generate
```

LightBurn 2.1.03 does not expose a supported headless machine-file export. On
macOS, the optional helper drives only **File → Save RD file** through
accessibility automation:

```sh
ruida-lightburn-export \
  work/lightburn-2.1.03/vector/v001-single-line.lbrn2 \
  work/lightburn-2.1.03/vector/v001-single-line.rd
```

It does not click Start, Send, or Run. Record hashes after export:

```sh
ruida-fixture record
ruida-matrix record
ruida-advanced record
```

Generators refuse to overwrite an existing project or manifest unless
`--force` is explicit. They also accept `--directory`, so an installed command
writes only to a caller-selected path or under `work/` in the current
directory. The checked-in `fixtures/` tree is never a default output target.

See [protocol notes](docs/protocol.md) for the layers and verified facts, and
[sources and provenance](docs/sources.md) for the source and provenance policy.
