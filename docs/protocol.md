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
7. UDP or serial link strategy over raw transport I/O.
8. Serialized controller exchange state and policy.

The JSON IR records `container` as `rd`, `udp`, or `logical`. A single UDP
datagram can therefore be decoded, edited, and re-encoded with direction-aware
wire framing at the correct scrambled layer. Outbound job and request
datagrams carry a checksum prefix; reply datagrams do not. Multi-datagram
captures use the versioned transcript format so datagram boundaries, direction,
raw bytes, endpoints, timestamps, and decode issues remain separate from the
logical command stream. That transcript format is currently UDP-specific.

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
  only nonnegative zero-padded values. LightBurn refuses to export a shape
  crossing into negative machine space, so another origin or profile strategy
  is still needed to establish its five-group negative spelling.
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

The commonly reported maximum payload is 1470 bytes. Packet boundaries do not
participate in command framing. The outbound packetizer fragments bytes at the
configured payload limit without inspecting command semantics, and the
streaming decoder produces the same logical result for every read split. A
command may cross a packet boundary in either direction.

The LightBurn `E5 05` job checksum is separate. Across all 69 checked-in
exports it equals the sum of every logical job byte except the seven-byte
`E5 05` frame itself, including the final `D7`. Checksum policy is explicit:
`preserve` retains the represented value, while `recompute` always derives a
single checksum from the outgoing logical stream. The encoder never infers
intent from editable JSON metadata.

## Controller adapters and exchange state

Pinned permissive implementations agree on the direct UDP data ports: the
host listens on 40200 and sends to controller port 50200. `UdpTransport` uses
those defaults, binds the local address selected by the route to the
controller, and preserves one call per datagram. Opening the adapter alone
does not send a datagram. Opening `ControllerClient` with its default probe
does send logical `CE` as a request and waits for a response.

The built-in USB-serial adapter uses the source-reported defaults of 115200
baud and 8N1. Its outbound bytes are scrambled but do not carry the UDP
two-byte checksum. The same sources report no per-packet ACK wait for this
serial path. One macOS USB-serial run with magic `0x88` has now exercised a
configured Ruida 644XS, but these rules still lack a checked-in capture corpus
from multiple controller models. The exact pinned code links are collected
under [transport evidence](sources.md#transport-evidence).

The built-in default profile applies this UDP exchange policy:

1. Fragment the logical stream, then scramble and checksum each datagram.
2. Send one datagram and wait before sending the next.
3. Classify only an exact one-byte logical control datagram as a handshake.
4. Accept logical `CC` as acknowledgement, and `CC` or `CE` for a
   keep-alive.
5. Retry the identical datagram only after logical `CF`, subject to a bounded
   retry count.
6. Treat logical `CD` as rejection and an acknowledgement timeout as an
   indeterminate failure. A timeout is not blindly resent.
7. Reject reply data that arrives before its ACK. A dialect may explicitly
   declare data-as-acknowledgement behavior, but the default does not infer it.
8. Collect reply data under total time, chunk, and byte bounds, then require
   its declared command type and correlation fields before reporting success.

The reported UDP exchange has no transaction sequence number. Request/reply
association therefore depends on strict serialization: one session operation
must own the transport until its ACK and optional replies are consumed. The
library locks whole synchronous exchanges and leaves UI scheduling to the
host. Direct transport reads or writes must not be interleaved with an active
client operation. Reply-producing UDP requests are limited to one datagram
until a stronger correlation rule is evidenced; serial writes remain one byte
stream and may be fragmented.

An acknowledgement or reply timeout, or a transport failure, makes correlation
uncertain. The session enters a fail-closed desynchronized state, reports
partial packet progress and delivery certainty, and rejects later exchanges
without attempting in-place recovery. The protocol has no sequence number, so
reopening the same endpoint cannot prove that delayed input has disappeared.
The application must close the session, establish controller and link
quiescence, and explicitly accept the remaining correlation uncertainty
before starting a new session.

Handshake dispositions and reply completion are declarative policies, not a
claim that every controller dialect has been verified. A profile can swap
`CD` and `CF` behavior or explicitly allow reply data to imply delivery when a
capture supports that model. Reply policies can declare exact chunk or byte
counts, a completion predicate, or a bounded idle-gap fallback. Declarative
request contracts constrain accepted reply commands and echoed fields. The
correct profiles, multi-datagram reply termination, and broader serial request
behavior still need a public hardware capture corpus. No TCP bridge envelope
is currently implemented.

## First live controller observation

The configured Ruida 644XS profile was exercised from macOS over USB serial at
115200 baud with scrambling magic `0x88`. The
[hardware-validation manifest](../fixtures/hardware/ruida-644xs-usb-serial-v1/manifest-v1.json)
records that a read-only `DA 00` request for address 5 returned one fixed
nine-byte `DA 01` reply for address 5 with value 300000.

The generated conformance artifact represents this as one nested
`serial_vectors` exchange. Logical request `da000005` deterministically
scrambles to `d489890d`; logical reply `da0100050000122760` was observed as
wire bytes `d409890d89899b2fe9`. Both use magic `0x88` in a checksumless serial
stream, with no separate acknowledgement. The request and reply correlate on
address 5, and the reply adds value 300000.

`hardware-observed` is assigned on both evidence axes only to the captured
request-context `DA 00` read and reply-context numeric `DA 01` response.
Job-context `DA 00` remains `reported` for shape and semantics. The `DA 01`
setter retains its `fixture-observed` shape and `reported` semantics; an
observed reply opcode does not validate the setter command.

The transmitted mixed vector/raster job was 689 bytes and decoded before
transmission as 107 known records with no issues. It covered X 20 through
30 mm and Y 20 through 26 mm, used 100 mm/s and 20% maximum power, selected
10%, 15%, and 20% raster modulation, and left air assist off. The identical
job was transmitted twice after explicit approval. The first attempt produced
observed activity but the material was misplaced. After repositioning the
material, the operator reported complete success on the second attempt.

No automatic sensor or output measurement verified physical execution. This
is evidence for one controller/profile, serial path, and mixed job only. It
does not establish broader opcode meanings, controller compatibility, or
machine safety.

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

## Advanced LightBurn evidence

Three additional controlled exports extend the matrix without changing the
lexical model:

- A two-layer job uses actual RD layers 0 and 1 throughout speed, power,
  color, bounds, selection, and motion records. `CA 22 01` accompanies the two
  active layers, while `CA 02 00` and `CA 02 01` select them for execution.
- A ten-segment source polyline is optimized into four relative cuts. Logical
  `AB` carries -2 mm then +2 mm vertical moves, and `AA` carries -3 mm then
  +3 mm horizontal moves. This confirms the signed relative field behavior
  while also demonstrating that an RD stream cannot generally reconstruct
  the unoptimized source segmentation.
- A mixed vector/raster job emits a complete vector layer followed by a
  complete horizontal-unidirectional raster layer. The raster layer retains
  its distinct runtime setup and closes with `E7 00` immediately before the
  shared `EB` array end.

All three files decode with zero opaque frames, reproduce exactly, and bring the
checked LightBurn shape set to 67. The generated absolute-negative project is
retained, but LightBurn reports that no shape is in bounds and produces no
machine file; there is no synthetic negative-coordinate fixture.

## Controlled raster evidence

Nine LightBurn 2.1.03 exports use embedded synthetic bitmaps to isolate scan
strategy, interval, grayscale modulation, and 3D-slice behavior. Every export
decodes entirely into known frames, has no issues, and reproduces exactly.
Together they bring the checked LightBurn shape set to 72.

The image layer has one coherent processing-mode mapping for the recorded
Ruida 644XS profile:

| Planned scan strategy | `CA 41` value | leading `CA 01` operation |
| --- | ---: | ---: |
| horizontal, unidirectional | 1 | 2 |
| horizontal, bidirectional | 2 | 1 |
| vertical, unidirectional | 3 | 4 |
| vertical, bidirectional | 4 | 3 |

Vector layers use zero for both fields. Threshold, grayscale, and 3D-slice
jobs with the same horizontal unidirectional plan all use the same `1`/`2`
pair. The host therefore owns scan planning; the Ruida layer envelope records
the resulting axis and direction strategy rather than a source-image mode.

Raster runtime setup differs systematically from the vector setup. It omits
the two through-power records, places active min/max power before on/off delay,
and closes raster motion with `E7 00` before the global `EB` array end and
final `E7 00` block end.

Grayscale motion uses paired `C7` then `C2` records with identical normalized
power values before each marked span. Changing only layer minimum power from
10% to 30% leaves all paired modulation bytes unchanged while changing the
four layer/runtime minimum-power records. The modulation is therefore kept
separate from layer min/max transfer. The historical physical channel label
for `C2` remains unverified.

LightBurn selects compact signed relative motion after the first absolute
position: `8A`/`8B` for blank axial spans, `89` for two-axis travel, and
`AA`/`AB` for marked axial spans. The 0.25 mm interval fixture changes planned
spacing and span count; it does not emit `C6 10` or a native pixel payload.

Declared job/layer bounds are derived from emitted motion endpoints and
rounded half-up to hundredths of a millimetre, while motion retains
thousandth-millimetre precision. At setting address 800, both values equal the
nonnegative integer truncation of total marked distance in millimetres: the
controlled 3.5, 5.5, and 13.5 mm cases emit 3, 5, and 13.

LightBurn expands pass count into ordinary planned motion. For this profile,
changing a four-pass 3D-slice project's `zPerPass` from 0 to 0.5 mm produces a
byte-identical `.rd` file. That is evidence that this setting does not encode
Z motion here, not evidence for any reported Z-axis opcode.

## Advanced capability evidence

Forty-four additional LightBurn 2.1.03 exports, c001 through c044, isolate
advanced controls under the active Ruida 644XS profile and five one-setting
research clones. All 44 pass strict decoding with no opaque frames, a
consistent job checksum, and exact byte reproduction. Together with the 25
earlier exports, they bring the checked LightBurn corpus to 69 files and the
observed job-shape set to 80.

The diagonal-raster family establishes a planned-path representation rather
than an angle command. The 45-degree and 135-degree files use `CA 41 00`, a
leading `CA 01 00`, and ordinary absolute or signed relative X/Y travel and cut
motion, including `A9` for non-cardinal marked segments. Bidirectional choice
changes host-planned ordering. Cross-hatch emits a second complete path block
after `E7 00`, separated by contextual `CA 01 05`, and closes that block with
another `E7 00`. Operation 5 is therefore used as a section separator in this
envelope; it is not named as a diagonal or cross-hatch opcode. Controlled
diagonal grayscale modulation is not present. Address-800 values are 6, 6, 8,
and 14 for the two 45-degree cases, 135-degree case, and cross-hatch case;
they do not establish a general geometric formula. The compiler has a
deterministic path-derived default and accepts an explicit
`reported_job_metric_mm` only when a host has producer provenance for that
metadata value.

The remaining controlled results are:

- **Two laser heads (c006-c010).** `CA 03` is an enable bitmask: values 1, 2,
  and 3 enable head 1, head 2, and both. `C6 01`/`C6 02` and
  `C6 31`/`C6 32` carry head-1 active and layer power; `C6 21`/`C6 22` and
  `C6 41`/`C6 42` independently carry head-2 active and layer power.
- **Dynamic vector power (c015-c021).** A 50% source PowerScale on a layer
  ranging from 10% to 70% inserts an operation-5/select-layer envelope with
  effective head-1 power from 10% through 40% immediately before each affected
  mark. Omitted, explicit 0%, and explicit 100% cases do not establish three
  distinct wire states. The compiler consequently accepts resolved per-mark
  channel powers, not a PowerScale scalar or formula.
- **Stationary timing (c022-c026 and c033-c036).** `C6 11` follows controlled
  start/end delay placement and carries a non-marking dwell duration in
  milliseconds scaled by 1000. Dot mode uses `C6 10` between non-cut moves;
  its value tracks timed marking pulses in the same units. These are exposed
  separately as `Dwell` and `Pulse`.
- **RF and fiber profiles (c037-c042).** Enabling RF override inserts two
  `C6 60` records whose U35 value tracks 20,000 or 10,000 hertz. The two
  selectors remain cautiously named. Under the fiber clone, `C6 66` tracks
  pulse width at 0, 100, or 200 nanoseconds; its selector roles likewise
  remain unverified.
- **Paired Z offset (c043-c044).** Positive LightBurn `zOffset` inserts an
  `80 03` delta of -1 mm before the one native raster block and +1 mm after it;
  negative offset reverses the pair. This proves the balanced serialized
  envelope, not physical axis direction or hardware effect.
- **Negative Z results (c011-c014).** Positive and negative `zPerPass`, and a
  1 mm material-height change, each produce a byte-identical `.rd` relative to
  the zero baseline. They are not mapped to `80 03` or another Z command.
- **Cut-through ambiguity (c027-c032).** Enabling start or end produces the
  same two through-power records; enabling both is identical to start-only.
  Changing the source head-1 through power changes both records, while changing
  head-2 through power changes neither. Endpoint identity and independent
  head-2 through power are therefore not established, and no compiler profile
  exposes cut-through.

The generated c045-c052 rotary cases remain blocked. No project containing one
authoritative `GantryRotaryConfig` was available to preserve its axis and
unknown fields, and no rotary hardware was available. No `.rd` fixture or
rotary compiler behavior is inferred.

The conservative job profile excludes planned-path raster and every other
capability that lacks hardware execution evidence. Planned-path raster,
two-head power, dynamic mark power, stationary events, RF frequency, fiber
pulse width, and paired Z offsets each use a separate opt-in research profile.
Those profiles reproduce their offline goldens exactly and have
`execution_evidence="not-observed"` for the advanced mode.

## Registry coverage

The current registries contain:

- 186 broad host/job command hypotheses;
- 74 provisional request candidates; and
- 10 hardware-observed, reported, disputed, or simulator-only reply shapes.

The host/job catalog covers motion, cut motion, power, speed, timing, layer
metadata, process control, memory access, document metadata, arrays, elements,
checksum, and file termination. The request view is a provisional opcode-family
subset, not a complete direction classifier. The address-5 live observation
above records one exchange with derived request wire and observed reply wire,
not a broader capture corpus.

Each registry record exposes:

- `shape_evidence`: whether its byte shape was seen in a fixture or hardware
  exchange, merely reported, conflicts across reports, or remains an uncited
  hypothesis;
- `semantic_evidence`: whether values were controlled in fixtures, observed
  on hardware, reported, or disputed;
- separate shape and semantic sources, plus notes for known disagreements.
- `controller_effect` and `reply_behavior` interaction classifications; and
- allowed reply commands and request-to-reply field matches where evidence
  supports them.

`hardware-observed` is a first-class value on both axes. It means that the
specific context, shape, and interpreted values have a physical-controller
observation in the cited source. It does not transfer across job, request, and
reply registries merely because their opcodes overlap, and it is not a claim
about every Ruida profile or firmware dialect.

## Unresolved evidence

These are research tasks, not parser exceptions:

- Canonical negative five-group coordinate spelling needs a controller-origin
  or machine-profile strategy that LightBurn will export.
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
- `DA 01` replies can depend on the requested memory address. The tested
  Ruida 644XS returned a fixed nine-byte numeric reply with value 300000 for
  address 5. MeerK40t's emulator synthesizes a CString for `DA 01 05 7F`,
  while ruida-pa models a fixed numeric reply. Other addresses and controller
  profiles remain unresolved; unmatched forms remain opaque.
- Controlled raster, grayscale, and 3D-slice jobs use planned motion plus
  power changes rather than a native pixel payload. Recovering an original
  source image from an optimized motion stream is not generally reversible.
  Diagonal binary raster is now established as planned path motion, but
  diagonal grayscale, physical Z behavior, overscan bounds, and other
  controller profiles remain unresolved.

Additional matrices should cover arrays, diagonal grayscale, rotary,
cut-through endpoint semantics, overscan, physical execution of the advanced
research profiles, real request/reply captures, and complete UDP transactions.
