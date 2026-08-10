# Conformance vectors

`spec/conformance-v1.json` is the executable, language-neutral companion to
the command catalog. Its Draft 2020-12 schema is
`schemas/conformance-v1.schema.json`. Both files are also shipped as package
resources.

The document is deterministic and content-addresses the exact catalog it was
generated against. A downstream implementation can use it without importing
Python or copying internal test code.

## Vector groups

`field_vectors` covers every codec ID published by catalog v1. Each record has
separate values for the JSON input to the canonical encoder and the JSON
output from the decoder:

```json
{
  "codec": "power-u14",
  "encode_json_value": 50.0,
  "wire_hex": "4000",
  "decode_json_value": 50.003051944088384,
  "canonical": true
}
```

The two JSON values can differ because a physical value may be quantized to a
wire integer. Implementations should perform both assertions:

1. Encoding `encode_json_value` with `parameters` produces `wire_hex`.
2. Decoding `wire_hex` produces `decode_json_value`.

Each vector also carries an explicit ordered `assertions` list, so a generic
runner does not need to infer which forward and reverse operations apply.

The vectors include fixed and parameterized fields, signed values, numeric
scaling, channel reordering, terminated strings, and packed byte words.

`swizzle_vectors` applies the default magic value to every possible input
byte in one vector. It must pass in both directions: logical to scrambled and
scrambled to logical.

`job_checksum_vectors` contains the complete logical input, excluding the
entire `E5 05` checksum frame. The expected integer is the unbounded sum of
those logical bytes. The vector also gives the canonical encoded checksum
command. This checksum is distinct from the UDP packet checksum.

`udp_vectors` makes direction and public context part of the test:

- A host-to-controller job or request is scrambled, summed modulo 65536, and
  prefixed with that checksum as two big-endian bytes.
- A controller-to-host reply is scrambled but has no checksum prefix.

The vectors expose the intermediate scrambled bytes and checksum separately,
so a failure can be assigned to transformation, arithmetic, byte order, or
framing instead of treated as one opaque mismatch.

## Evidence

Every vector has an evidence classification and source IDs. Controlled
fixture vectors also include a repository path and SHA-256 digest. Pinned
external sources are repeated with revision, URL, license, and role metadata.

`codec-contract` means the vector fixes canonical behavior implemented by the
published codec. `controlled-fixture` means the value is also present in a
content-addressed LightBurn export. `pinned-reference` records one pinned
source, while `pinned-reference-agreement` records independent agreement.

These labels do not promote provisional command meanings or claim universal
controller behavior. They state why each byte-level expectation is present.

## Generation and checking

Generate the canonical JSON on standard output:

```sh
ruida-conformance
```

Check the repository artifact without changing it:

```sh
ruida-conformance --check spec/conformance-v1.json
```

Write a new file without replacing an existing path:

```sh
ruida-conformance --output /tmp/conformance-v1.json
```

Replacement requires the explicit `--force` option.

Python integrations can call `build_conformance()` for JSON-compatible values
or `conformance_json()` for canonical text. Installed-resource consumers can
read `CONFORMANCE_V1` and `CONFORMANCE_SCHEMA_V1` through
`ruida_re.resources`.

## Versioning

The artifact schema identifier is `ruida-re.conformance.v1`. A downstream
suite should reject an unknown identifier and pin both the conformance file
and its referenced catalog SHA-256. Additive vector cases can be published
without changing the schema version. A structural or semantic contract change
requires a new conformance schema version.
