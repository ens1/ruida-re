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

`serial_vectors` represents a complete request/reply exchange as one nested
vector rather than treating its two directions as unrelated messages. The
current vector `serial.exchange.get-setting-address-5` specifies:

- magic `0x88` and `checksumless-scrambled-stream` framing;
- request-context logical bytes `da000005`, which encode to derived
  host-to-controller wire bytes `d489890d`;
- reply-context hardware-observed wire bytes `d409890d89899b2fe9`, which
  decode to logical bytes `da0100050000122760`;
- no separate serial acknowledgement; and
- correlation of request address 5 with reply address 5 and numeric value
  300000.

Its ordered assertions require request encoding, request decoding, reply
decoding, address correlation, and the absence of a separate acknowledgement.
The nested shape prevents a runner from accidentally applying UDP checksum or
acknowledgement rules to either side of this serial exchange.

## Evidence

Every vector has an evidence classification and source IDs. Controlled
fixture vectors also include a repository path and SHA-256 digest. Pinned
external sources are repeated with revision, URL, license, and role metadata.

`codec-contract` means the vector fixes canonical behavior implemented by the
published codec. `controlled-fixture` means the value is also present in a
content-addressed LightBurn export. `hardware-observed` means the vector is
tied to a content-addressed physical-controller observation.
`pinned-reference` records one pinned source, while
`pinned-reference-agreement` records independent agreement.

The serial vector retains byte-level provenance inside that classification:
the request wire is `derived-from-logical`, while the reply wire is
`hardware-observed`. In the command catalog, the same evidence term applies
to shape and semantics only for request-context `get_setting` (`DA 00`) and
reply-context numeric `setting_reply` (`DA 01`). Job-context `DA 00` remains
reported, and the state-changing `DA 01` setter retains fixture-observed shape
with reported semantics.

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
without changing the schema version.

`serial_vectors` is an optional, backward-compatible v1 schema extension: it
is defined in `properties` but omitted from the required-property list. The
current generator always includes the group, and current tests require its
exact exchange and verify that the artifact still validates when the group is
removed. Older v1 documents therefore remain valid under the current schema.
This preserves the structural-versioning promise because no previously valid
v1 shape or meaning was invalidated and no new required property was added. A
breaking structural or semantic contract change still requires a new
conformance schema version. A consumer pinned to an older schema snapshot may
reject the new property and should update its pinned schema and artifact
together; forward compatibility with unknown properties is not implied.
