# Sources and provenance

Original `ruida-re` material is released under the WTFPL Version 2. Existing
projects are used as factual leads and executable comparison oracles. Except
for compact protocol primitives conservatively attributed in
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md), their parser control
flow, job builders, text formats, and source expression were not translated
into this repository.

## Primary fixtures

- LightBurn 2.1.03 on macOS.
- User-configured `Ruida 644XS` profile.
- One baseline, 12 one-variable exports, three advanced exports, nine raster
  exports, and 44 advanced-capability exports checked into
  `fixtures/lightburn-2.1.03`, for 69 `.rd` files total.
- The advanced corpus includes a real two-layer file, signed relative X/Y
  motion, and a mixed vector/raster job. A generated project crosses negative
  machine X; LightBurn rejects it as out of bounds, so no `.rd` is claimed for
  that case.
- Capability cases c001-c044 cover planned-path diagonal raster, two laser
  heads, stateful effective vector power, stationary dwell and pulse, RF
  frequency, fiber pulse width, paired Z-offset candidates, and controlled
  ambiguity or no-op results for cut-through, `zPerPass`, and material height.
  Nine family experiment manifests and nine strict reports bind those
  comparisons; every capture has zero opaque records and reproduces exactly.
- Rotary cases c045-c052 remain blocked. They require a LightBurn-exported
  project containing exactly one `GantryRotaryConfig` so its axis and unknown
  content can be preserved. No such authoritative template or rotary hardware
  was available, so no rotary project, `.rd`, or semantic claim is published.
- Project and machine-file SHA-256 values recorded in adjacent JSON manifests.
- LightBurn application SHA-256, complete profile dimensions/mirroring, and
  per-file generation stage recorded in those manifests. The baseline project
  is the generated input; matrix projects are the post-LightBurn normalized
  form saved after export.
- Capability captures bind the exact active profile or one of five controlled
  clones that enables only `EnableZ`, `Laser2Enabled`, `Laser1IsRFTube`,
  `Laser1IsFiber`, or `SaveRotaryConfig`. Published `.lbdev` files remove
  volatile local path settings. The publication manifest records each JSON
  Pointer removal, original-value digest and size, original profile hash, and
  sanitized published hash; capture-time identity is retained under
  `capture_origin` rather than falsely claiming that the two byte streams are
  identical.
- Raster projects embed deterministic synthetic PNGs and vary one scan,
  power, interval, or pass setting at a time. Their machine files were created
  with LightBurn's **Save RD file** action. Per-case attestations record
  `job_transmitted=false`; no Start, Send, or Run action was initiated for the
  fixture capture. Whether LightBurn itself opened its configured controller
  connection is explicitly `not-attested`, so offline file provenance is not
  overstated as proof of no controller contact.

The separate live-validation job described below is not one of these fixture
exports. Its execution does not change the offline provenance of the fixture
corpus.

These fixtures decide local shape and value questions. Merely seeing an opcode
establishes its frame shape for this LightBurn dialect, not its mnemonic. That
distinction is represented by separate `shape_evidence` and
`semantic_evidence` fields.

`hardware-observed` is a first-class value on both evidence axes. It identifies
a context-specific shape or meaning supported by a cited physical-controller
observation. It is deliberately narrower than an opcode-family claim: evidence
for a request or reply form does not silently promote a job-context command
with overlapping bytes.

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

## Reported software-stop request

The request-context software-stop contract is based on one pinned permissive
implementation, not a controller capture:

- MeerK40t defines `STOP_PROCESS` as logical `D8 01` in its
  [pinned command table](https://github.com/meerk40t/meerk40t/blob/5f68a45bff41d98e4d3fe8b8267857218099afa8/meerk40t/ruida/rdjob.py#L126-L134).
- Its Ruida controller's
  [abort path](https://github.com/meerk40t/meerk40t/blob/5f68a45bff41d98e4d3fe8b8267857218099afa8/meerk40t/ruida/controller.py#L412-L414)
  calls the job's stop operation, whose
  [encoder](https://github.com/meerk40t/meerk40t/blob/5f68a45bff41d98e4d3fe8b8267857218099afa8/meerk40t/ruida/rdjob.py#L1869-L1876)
  emits `STOP_PROCESS` on the normal controller channel.
- Its UDP
  [handshaker](https://github.com/meerk40t/meerk40t/blob/5f68a45bff41d98e4d3fe8b8267857218099afa8/meerk40t/ruida/udp_connection.py#L251-L299)
  treats only controller-memory reads as reply-producing and otherwise waits
  for the normal packet acknowledgement.

This supports `reported` shape and semantic evidence, a `machine-action`
effect, and no application-data reply for request-context `process_stop`.
It does not promote the overlapping job-context command, establish a
hardware-observed stop effect, or prove execution completion after a host
write or packet acknowledgement.

## First live hardware validation

One configured Ruida 644XS was exercised from macOS over USB serial at 115200
baud with scrambling magic `0x88`. The
[hardware-validation manifest](../fixtures/hardware/ruida-644xs-usb-serial-v1/manifest-v1.json)
records that a read-only `DA 00` request for address 5 returned a fixed
nine-byte `DA 01` reply for address 5 with value 300000.

The generated conformance artifact content-addresses that manifest in one
nested `serial_vectors` exchange. It records logical request `da000005` and
derived wire `d489890d`, plus hardware-observed reply wire
`d409890d89899b2fe9` and logical reply `da0100050000122760`. The vector uses
magic `0x88`, checksumless stream framing, no separate acknowledgement, and
address correlation from request address 5 to reply address 5 and value
300000.

A generated 689-byte mixed vector/raster job decoded as 107 known records with
no issues before transmission. Its bounds were X 20 through 30 mm and Y 20
through 26 mm. It used 100 mm/s, 20% maximum power, raster modulation values
of 10%, 15%, and 20%, and air assist off. The job was transmitted twice after
explicit approval. The first transmission produced observed activity while
the material was misplaced. After repositioning the material, the operator
reported complete success on the second transmission.

The report is based on operator observation; no automatic sensor or output
measurement verified execution. It covers one controller/profile and one
generated job. The manifest records the observed reply bytes, but it is not a
complete serial transcript or a multi-controller compatibility claim.

The controlled LightBurn `r005` and `r006` exports change only grayscale
minimum power from 10% to 30%. Their paired `C7`/`C2` bytes remain identical,
and their seven decoded values track normalized source-image darkness rather
than either absolute output range. This agrees with LightBurn's official
[grayscale documentation](https://docs.lightburnsoftware.com/latest/Reference/CutSettingsEditor/ImageMode/#grayscale),
which defines image output between Min Power and Max Power. The
[pinned Raygeo source](https://github.com/ens1/raygeo/blob/5663bec8c5d47ebb7f3f09d6df0658f5bdac8583/src/image/scan.rs#L641-L665)
independently confirms that Rayforge scanline bytes are already absolute
resolved outputs. A Rayforge adapter must therefore inverse-normalize each
positive byte into the layer range before emitting `SetModulation`; direct
`sample / 255` transfer would scale the output twice.

## Planned-path live hardware validation

The
[planned-path hardware manifest](../fixtures/hardware/ruida-644xs-usb-serial-planned-path-v1/manifest-v1.json)
content-addresses two 574-byte variants of one single-section 45-degree job.
Both decoded as 77 known records with no issues and reproduced exactly with a
recomputed checksum. They used five absolute travel/cut pairs at 100 mm/s,
head 1, and air assist off within X 20 through 32 mm and Y 20 through 40 mm.

Each serial transfer completed as one 574-byte host write with no retry and no
controller acknowledgement. At 10%, the operator observed five movements but
no visible cardboard marks. At 15%, the operator reported five correct lines,
no connecting burn, and no over-burning. This is execution evidence from one
Boss LS2040 configured as a Ruida 644XS, not dimensional or power metrology.

Those two executed files contain no second `RasterSection`, operation-5
section separator, cross-hatch, signed-relative diagonal cut, or modulation.
A later
[cross-hatch companion manifest](../fixtures/hardware/ruida-644xs-usb-serial-planned-path-v1/cross-hatch-observation-v1.json)
content-addresses a Rayforge-generated 666-byte artifact with two sections,
five marks in each diagonal direction, and one operation-5 separator. Its host
summary reported one packet and zero retries, with no controller or execution
acknowledgement. The operator reported, "Crosshatch is good. Both directions
are visible, no connection burns, and no burns. I can see the one small edge,
the beam obviously pulsed at the top left of the crosshatch". The small
top-left edge is a decoded 0.3507 mm `cut_relative` mark; no C6 10 record
exists, so it is not pulse evidence.

These exact observations are not dimensional, angle, timing, or power
metrology. The broader planned-path profile remains research-only with a
mode-wide `not-observed` execution label rather than projecting the coupons
onto arbitrary planned paths.

## Dynamic-power live hardware validation

The
[dynamic-vector hardware manifest](../fixtures/hardware/boss-ls2040-usb-serial-rayforge-dynamic-vector-v1/manifest-v1.json)
content-addresses the first three exact Rayforge-generated one-layer jobs
executed on one Boss LS2040. The first 15%-10%-15% coupon was visually
inconclusive. A longer
15%-5%-15% coupon visibly marked only its first approximately 30 mm; its
payload contained a reduced-power envelope but no explicit baseline restore.

The corrected 564-byte job decoded as 86 known records with no issues and
reproduced exactly with preserved and recomputed checksums. It used three 30 mm
absolute cuts at Y 95 mm and 100 mm/s. A seven-record envelope selected layer
zero and reduced the laser-1 active-power command fields to 5% before the
middle cut. A second seven-record envelope restored the layer's 5%-15%
laser-1 command-field range before the trailing cut; inactive laser-2 command
fields remained 40%-40% in both envelopes.

The corrected artifact completed one 564-byte host-side serial transfer with
no retry. That receipt is not a controller or execution acknowledgement. The
operator reported, "Perfect. A ~30mm line, a gap, and a ~30mm line". This is
scoped evidence for the explicit restore in that exact one-enabled-command-
channel, one-layer, 100 mm/s sequence. It is not dimensional or optical-power
metrology, physical channel-routing evidence, or validation of arbitrary
dynamic-power paths. The broad profile retains its mode-wide `not-observed`
label and remains research-only.

The
[repeated-restore companion](../fixtures/hardware/boss-ls2040-usb-serial-rayforge-dynamic-vector-v1/dynamic-repeated-observation-v4.json)
records a fourth exact artifact containing five decoded 16 mm spans at
15%-5%-15%-5%-15% and four alternating reduce/restore envelopes. Its host
summary reported one packet and zero retries without controller or execution
acknowledgement. The operator reported, "Yes, I see 3 lines, maybe 20mm each,
two gaps". The decoded spans are 16 mm, so the reported lengths are not
metrology. This is scoped evidence for the exact repeated-restore sequence,
not calibrated-power, zero-output, or mode-wide evidence.

## Native unidirectional raster live hardware validation

The
[unidirectional raster manifest](../fixtures/hardware/boss-ls2040-usb-serial-rayforge-unidirectional-raster-v1/manifest-v1.json)
content-addresses one exact 769-byte Rayforge production-path coupon with
horizontal and vertical unidirectional native-raster layers. Its host transfer
reported one packet and zero retries without controller or execution
acknowledgement. The operator reported the expected 12 marks, no burnt return
moves, and unchanged Z. This is scoped visual and controller-display evidence
for that exact artifact, not directional, dimensional, power, zero-output, or
Z-motion metrology.

## Air-assist negative or inconclusive hardware evidence

The
[air-assist evidence manifest](../fixtures/hardware/boss-ls2040-usb-serial-rayforge-air-assist-v1/manifest-v1.json)
records three narrowly scoped results from one Boss LS2040. An air-off motion
control completed one host packet with zero retries, but the operator could
not distinguish possible airflow from motor noise. Its paired 580-byte air-on
motion artifact was later transferred once with one host packet and zero
retries. The operator reported, "Air assist is confirmed, I felt the solenoid
turn on then off". This supports normal full-layer job-context air assist only
for that exact artifact and setup.

A separately approved standalone sequence wrote logical `CA 01 12`,
`CA 01 13`, and `CA 01 12` over USB serial with a 5.002178-second host
interval. Their exact scrambled wire bytes were `c4099b`, `c4091b`, and
`c4099b`. All three writes and flushes completed, but serial supplied no
controller or state acknowledgement. The operator observed no physical change
or relay/solenoid click and reported that LightBurn also appeared to fail.
That standalone result remains inconclusive and is not evidence for a manual
toggle. The full-layer observation is tactile operator evidence, not timing,
pressure, flow, relay-routing, current, or electrical metrology, and it does
not establish other controllers or UDP. No compiler behavior is changed.

## Zero-power negative hardware evidence

The
[zero-power safety manifest](../fixtures/hardware/boss-ls2040-usb-serial-zero-power-safety-v1/manifest-v1.json)
records a nominal-0% no-dwell control that unexpectedly emitted visible laser
power and drew a rectangle on the same Boss LS2040. The operator reported,
"There was laser emission. I see a clearly drawn rectangle, maybe 25mmx50mm".
It was transferred in one host-reported packet with zero retries and no
controller or execution acknowledgement. The cause was not isolated: raw zero
may mean default, stale, or no update; a firing floor or another field may
contribute; and the minimal through-power fields were not independently tested.

The paired artifact with four 200 ms C6 11 delays was stopped before transfer,
and the earlier planned Z coupons were also withheld and remain quarantined
offline. Those artifacts therefore remain hardware-unobserved. Two later exact
±1 mm logical-Z jobs produced opposite operator-observed machine-readout
changes and numerical returns to the reported start. Their
[paired manifest](../fixtures/hardware/boss-ls2040-usb-serial-rayforge-logical-z-v1/manifest-v1.json)
does not claim physical direction or mechanical metrology. The two zero-power
programs are retained only with `.rd.quarantined` suffixes as do-not-send
evidence.

The compiler now rejects marking if an enabled channel's minimum or maximum,
or raster marking modulation, would encode below raw value 16. The floor is a
conservative generation-time boundary derived from producer evidence, not
proof that raw 16 is physically safe. Existing, cached, hand-authored, and
external `.rd` files are not retroactively inspected or protected.

## Read-only setting request evidence

The installed CLI exposes request-context `DA 00` because the live observation
and both pinned implementations distinguish it from the state-changing
`DA 01` operation and expect reply data:

- ruida-pa labels controller memory readable via `DA`, defines `DA 00` as
  `GET_SETTING`/read and `DA 01` as `SET_SETTING`/write, then parses fixed
  `DA 01` replies: [pinned command
  table](https://github.com/StevenIsaacs/ruida-pa/blob/92efde98004d9948474eb712ef6f5b164f468c4f/protocols/ruida/ruida_protocol.py#L201-L205),
  [pinned DA entries](https://github.com/StevenIsaacs/ruida-pa/blob/92efde98004d9948474eb712ef6f5b164f468c4f/protocols/ruida/ruida_protocol.py#L547-L550),
  [pinned reply parser](https://github.com/StevenIsaacs/ruida-pa/blob/92efde98004d9948474eb712ef6f5b164f468c4f/ruidadriver/rd_transport.py#L195-L213).
- MeerK40t's emulator handles `DA 00` as a memory lookup followed by a
  `DA 01` response, while its `DA 01` branch writes values:
  [pinned emulator](https://github.com/meerk40t/meerk40t/blob/5f68a45bff41d98e4d3fe8b8267857218099afa8/meerk40t/ruida/emulator.py#L661-L696).
- MeerK40t reports semantic address `0x0200` as machine status and labels
  `0x01000000`, `0x00000002`, and `0x00000001` as moving, part end, and job
  running, respectively:
  [pinned status table](https://github.com/meerk40t/meerk40t/blob/5f68a45bff41d98e4d3fe8b8267857218099afa8/meerk40t/ruida/rdjob.py#L217-L249).
  Those labels remain implementation-reported; the local address-512 capture
  establishes only the request/reply shape and returned zero value.
- ruida-pa maps semantic addresses `0x010E`, `0x0221`, `0x0231`, and `0x0241`
  to focus depth and current X, Y, and Z. It treats the coordinate values as
  signed dimensions scaled by 1000 while leaving focus depth as `TBDU35`:
  [pinned memory table](https://github.com/StevenIsaacs/ruida-pa/blob/92efde98004d9948474eb712ef6f5b164f468c4f/protocols/ruida/ruida_protocol.py#L312-L370).
  MeerK40t independently reports the current-position address bytes and
  signed coordinate decoder, while only its emulator suggests that focus
  depth raw 5000 represents 5 mm:
  [pinned addresses](https://github.com/meerk40t/meerk40t/blob/5f68a45bff41d98e4d3fe8b8267857218099afa8/meerk40t/ruida/rdjob.py#L239-L254),
  [pinned decoder](https://github.com/meerk40t/meerk40t/blob/5f68a45bff41d98e4d3fe8b8267857218099afa8/meerk40t/ruida/rdjob.py#L340-L381),
  [pinned emulator value](https://github.com/meerk40t/meerk40t/blob/5f68a45bff41d98e4d3fe8b8267857218099afa8/meerk40t/ruida/emulator.py#L1088-L1098).
  Those implementation reports alone do not establish values on the
  operator-controlled device, so the typed API preserves raw U35 and labels
  every unit conversion as a hypothesis. The initial single-point capture is
  scoped below.
- Both pinned implementations label logical `D8 2E` as Focus Z, but neither
  establishes its physical effect or reply behavior on hardware:
  [ruida-pa command table](https://github.com/StevenIsaacs/ruida-pa/blob/92efde98004d9948474eb712ef6f5b164f468c4f/protocols/ruida/ruida_protocol.py#L510-L538),
  [MeerK40t command table](https://github.com/meerk40t/meerk40t/blob/5f68a45bff41d98e4d3fe8b8267857218099afa8/meerk40t/ruida/rdjob.py#L130-L150).
  It remains an offline-only candidate, not a live controller operation.

The live exchange and independent implementation reports establish different,
explicitly scoped evidence states. Request-context `get_setting` (`DA 00`) and
reply-context numeric `setting_reply` (`DA 01`) have `hardware-observed` shape
and semantic evidence. Job-context `get_setting` remains `reported` on both
axes. The generic state-changing `set_setting` (`DA 01`) registry record
retains a `fixture-observed` shape and `reported` semantics; the numeric reply
does not validate that write command. The later supervised Focus Distance
exchange provides narrower host-transmission and effect evidence for only
address `0x010E`, two values, and one exact controller. It does not promote
the generic registry record or guarantee behavior for another address,
controller, or firmware dialect.

## Experimental Focus Distance write evidence

The narrow typed write surface is based on a static audit of the same
LightBurn 2.1.03 application identified by SHA-256
`909262ec7f67b1accbf42f9905ded18a317febb09202ff8cfa81bc0256f7d02a`
in the primary fixture manifests. The audit observed these implementation
facts without copying application code or redistributing the binary:

- `GetMachineSettingsInfo` associates Focus Distance with address bytes
  `02 0E`, a displayed minimum of 0 mm, a displayed maximum of 1,000,000 mm,
  and three decimal places.
- `SetMachineSettingsValues` multiplies the displayed millimetre value by
  1000 and converts the result to a signed integer.
- `SendConfigurationCommand(unsigned short, int)` constructs logical DA01
  with the two address bytes and passes the same integer twice to
  `WriteLongPair(int, int)`. That encoder emits five base-128 groups for each
  integer.
- The USB-serial branch scrambles and submits the resulting 14 bytes as one
  write, waits up to 500 ms for host-side send completion, and does not read a
  controller acknowledgement.

The typed operation is limited to the built-in USB-serial link with magic
`0x88`; no static or captured evidence supports alternate magic values for
this setting write.

An operator-supplied private machine-settings export contains
`Focus Distance`, LightBurn ID `0x20e`, value `9.3`. A separate supervised
read-only USB-serial register snapshot,
`ruida-z-register-baseline-v1.json`, SHA-256
`5e0cb66fd9ccc2bc62deefe69e47e45c50e95b1d7da0e84a7a96cde848db0c23`,
records exact logical request `DA00020E` and matching logical reply
`DA01020E0000004854`, whose U35 value is 9300. Together those artifacts
corroborate the setting identity and scale on that controller at that instant.
The operator associated both artifacts with the exact same controller; the
controller model was not independently verified. The full private export is
deliberately not distributed.

One later supervised sequence on that exact controller started with three
raw-9300 DA00 samples. Exactly one typed 9300-to-9400 compare-and-set produced
a `SendReceipt` with one DA01 packet, one transmission, one completed packet,
and zero retries. After that session closed, a fresh connection returned raw
9400 three times. The operator reported that the controller displayed Focus
Distance 9.4 and that a completed panel-invoked Autofocus routine ended at Z
9.4. Exactly one 9400-to-9300 rollback produced the same one-packet,
one-transmission, zero-retry receipt shape; another fresh connection returned
raw 9300 three times, and the operator reported a restored completed-Autofocus
endpoint of Z 9.3. The public
[evidence manifest](../fixtures/hardware/operator-controlled-ruida-usb-serial-focus-distance-write-v1/manifest-v1.json)
separates host observations from operator attestations and omits private device
metadata. No standalone raw transport transcript file was saved, so the
manifest explicitly records that no transcript hash is available and names
the parsed readings, receipt fields, and operator attestations on which it is
based.

This is immediate acceptance, effect, and rollback evidence for only that
controller and those two values. The serial receipts remain host-side write
evidence rather than controller acknowledgements, and the typed method itself
still performs no readback. No reset or power cycle tested persistence. No
probe trigger, contact event, contact coordinate, force, physical distance, or
independent Z position was captured. Autofocus was invoked from the panel;
logical `D8 2E` remains untested on hardware. The result does not establish
contact probing, autofocus safety or repeatability, another value, another
controller or firmware, or UDP.
Its fixed 0-through-1,000,000,000 raw bounds reproduce the audited LightBurn
display metadata and signed-integer setter representation. They are not
controller limits, capability claims, or machine-safe focus bounds. The
library imposes no fixed per-call delta limit.

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
