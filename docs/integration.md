# Integration guide

`ruida-re` is the Ruida-specific backend beneath a laser application. The
application supplies geometry, path ordering, layer intent, machine selection,
and the decision to transmit. The library supplies the reversible Ruida byte
model and the controller-facing session.

This distinction is intentional:

- The host owns geometry editing, path optimization, raster generation, user
  confirmation, scheduling, and application safety policy.
- `ruida-re` owns profile-validated planned-job lowering, command field
  encoding, semantic registries, unknown-byte preservation, `.rd` wrappers and
  checksums, scrambling, byte-stream packetization, UDP and USB-serial link
  strategies, profile-driven UDP handshakes, bounded retries and replies,
  request/reply decoding, and UDP capture interpretation.
- The project does not provide geometry editing, image processing,
  rasterization, path planning, controller discovery, or a TCP bridge.

The API is pre-alpha. Lossless translation is extensively tested against the
checked-in corpus, but semantic and live-controller coverage remain incomplete.
The planned-job compiler has exact controlled-fixture coverage, not live
controller validation. Pin a package version and preserve unknown records when
integrating it.

## Planned-job compiler

`RuidaJobCompiler` accepts a small, immutable plan that is already ready for
emission. It produces a complete job `Program`; `CompileResult.encode_rd()`
then returns the scrambled `.rd` bytes with the profile envelope, derived
bounds, layer metadata, motion, one recomputed job checksum, and termination
records. Constructing a plan, compiling it, and encoding its result perform no
device I/O.

The public plan types are:

- `JobPlan`, an ordered tuple of layers;
- `LayerPlan`, one vector or raster layer and its final ordered events;
- `TravelTo`, an absolute non-marking XY move;
- `MarkTo`, an absolute marking XY move; and
- `SetModulation`, a normalized raster modulation change before later marked
  motion.

Canonical plan units are absolute machine-space millimetres for X and Y,
millimetres per second for speed, and percentages from 0 through 100 for layer
minimum power, layer maximum power, and raster modulation. Layer indices are
contiguous from zero. `SetModulation` is distinct from the layer power limits;
the host owns the mapping from source pixels or depth values to modulation.

A raster `LayerPlan` must state both `scan_axis` and `raster_strategy`. The
controlled profile accepts the four evidenced combinations:

- horizontal and unidirectional;
- horizontal and bidirectional;
- vertical and unidirectional; and
- vertical and bidirectional.

Every nonzero raster `MarkTo` must lie on the declared axis. Within a
unidirectional layer, all marked spans must also share one direction; travel
between rows remains unrestricted. Contradictory plans are rejected before
encoding.

```python
from ruida_re import (
    JobPlan,
    LayerPlan,
    MarkTo,
    RuidaJobCompiler,
    SetModulation,
    TravelTo,
)

layer = LayerPlan(
    index=0,
    kind="raster",
    speed_mm_s=100.0,
    min_power_percent=10.0,
    max_power_percent=90.0,
    scan_axis="horizontal",
    raster_strategy="unidirectional",
    air_assist=True,
    events=(
        TravelTo(23.5, 20.25),
        SetModulation(25.0),
        MarkTo(23.0, 20.25),
        TravelTo(22.5, 20.25),
        SetModulation(75.0),
        MarkTo(22.0, 20.25),
    ),
)
result = RuidaJobCompiler().compile(JobPlan(layers=(layer,)))
machine_file = result.encode_rd()
```

The currently controlled LightBurn 2.1.03 Ruida 644XS profile supports planar
XY vector and raster motion, one laser index, the four scan modes above, and
air assist. It does not support Z or rotary motion, additional laser heads,
frequency, pulse width, dwell, or arbitrary-angle raster metadata. An adapter
must reject any such source operation instead of silently discarding it,
mapping it to a nearby field, or emitting a partially faithful job.

Pass expansion also belongs to the host. Repeated planar passes can be
represented by repeated planned motion. A controlled LightBurn four-pass
3D-slice fixture was byte-identical when `zPerPass` changed from 0 to 0.5 mm,
so that setting is not treated as evidence for a Ruida Z command. A source
plan that requires actual Z movement must be rejected by this profile.

### Rayforge boundary

For a Rayforge integration, the adapter belongs after Rayforge has rendered
and dithered or depth-processed an image, generated scanlines, chosen
overscan and scan direction, optimized and expanded paths and passes, and
applied placement and machine transforms. The boundary is:

```text
Rayforge document/image processing
    -> ordered machine-space operations
    -> thin Rayforge-to-JobPlan adapter
    -> RuidaJobCompiler
    -> complete .rd bytes
```

The adapter translates final non-marking motion to `TravelTo`, final marking
motion to `MarkTo`, and normalized per-span raster power to `SetModulation`.
It also transfers supported layer speed, power limits, air state, and raster
axis and strategy. A bitmap, depth map, optimizer setting, or application
model should not cross this boundary. That keeps generic laser planning in
Rayforge and controller-specific envelope and byte work in `ruida-re`.

The adapter must be stateful and section-aware. One Rayforge document layer
can contain several steps with different speed, power, head, air, or process
kind, so it is not necessarily one `LayerPlan`. Split the ordered stream into
contiguous Ruida layers whenever one of those controlled regimes changes.
Convert Rayforge feed rates from millimetres per minute to millimetres per
second. Map each configured machine head explicitly to the one-based Ruida
laser index; do not derive an index from display order.

For constant-power scanlines, positive spans become `MarkTo` and exact-zero
spans become `TravelTo`; use the resolved static output as both layer power
limits and emit no modulation records. For variable-power scanlines, each
positive eight-bit sample is already the absolute resolved output: emit
`SetModulation(100 * sample / 255)` followed by `MarkTo`, and translate zero
to `TravelTo`. Do not multiply those samples by an earlier step-power value a
second time. Planar depth passes are ordinary repeated motion; any remaining
nonzero Z coordinate is unsupported.

This mapping requires explicit resolved step metadata: vector or raster kind,
speed, static and minimum/maximum power, air state, head, scan axis, scan
strategy, and layer color. A single scanline cannot always reveal its intended
strategy, and absolute variable-power samples cannot reconstruct the source
minimum/maximum settings. If Rayforge's final operation stream has discarded
one of those values, preserve it upstream in step/section markers or a sidecar
before calling `ruida-re`. Guessing a default in the adapter is not a supported
fallback. Mid-path vector power changes, arbitrary-angle raster, frequency,
pulse width, dwell, extra heads, and actual Z motion must likewise be rejected
until the selected Ruida profile supports them.

## Offline codec

`RuidaCodec` performs no device I/O. Configure one instance for each direction
whose semantics you need:

```python
from pathlib import Path

from ruida_re import Program, RuidaCodec

job_codec = RuidaCodec(context="job")
request_codec = RuidaCodec(context="request")
reply_codec = RuidaCodec(context="reply")

source = Path("input.rd").read_bytes()
program = job_codec.decode(source, container="rd")

json_text = program.to_json()
restored = Program.from_json(json_text)
assert job_codec.encode(restored) == source
```

The three containers name an actual protocol layer:

- `rd` is a scrambled machine file, optionally with a ten-byte `RDWORKV`
  wrapper.
- `udp` is one complete datagram. Job and request datagrams include their
  two-byte packet checksum; reply datagrams do not.
- `logical` is the unscrambled command stream with no file or transport
  envelope.

Do not guess a container from content in application code. Record it alongside
the bytes or use the `Program.container` value produced by decoding.

### Constructing records

The host can ask the context registry for its stable command names, then create
validated records without implementing field widths or scrambling:

```python
codec = RuidaCodec(context="job")
assert "move_absolute" in codec.command_names

move = codec.command("move_absolute", x_mm=20.0, y_mm=20.0)
end = codec.command("end_of_file")
fragment = codec.program([move, end], container="logical")
logical_bytes = codec.encode(fragment)
```

That fragment illustrates the low-level record API; it is not presented as a
complete, safe laser job. Use the planned-job compiler for supported whole
jobs. Unknown or dialect-specific frames can be retained verbatim with
`codec.opaque(raw_bytes)`.

Structured edits should normally use an explicit job-checksum policy:

```python
edited_rd = job_codec.encode(program, checksum_policy="recompute")
```

`recompute` replaces an existing `E5 05` job checksum. It does not synthesize a
missing checksum command or complete an otherwise partial job. `preserve`
keeps the represented value and is the correct choice for unchanged forensic
round trips.

### Datagram translation

Applications that own their own I/O can use the codec without
`ControllerClient`:

```python
request_program = request_codec.program(
    [request_codec.command("keep_alive_request")]
)
packets = request_codec.encode_datagrams(request_program, mtu=1024)
decoded_reply = reply_codec.decode_datagrams(received_datagrams)
```

The outbound packetizer fragments the logical byte stream without inspecting
command semantics. A command may therefore cross a packet boundary. The
decoder joins ordered datagrams at the logical layer, so network boundaries do
not change command parsing. The application remains responsible for preserving
datagram order and direction.

## Controller session

`ControllerClient` combines a codec with a synchronous transport and owns the
Ruida exchange state. Construction does not open a device or send data.

### Installed command line

`ruida-controller` is a noninteractive wrapper around the same transports and
session state. It prints one compact JSON result rather than prompting. Common
options can appear before or after the subcommand, and exactly one link is
required:

```sh
ruida-controller probe --udp 192.0.2.10
ruida-controller probe --serial /dev/ttyUSB0
```

A successful UDP probe means the keep-alive exchange was acknowledged. A
successful serial probe proves that the device opened; the serial link has no
UDP-style acknowledgement and reports `controller_acknowledged` as false.

The CLI's `request NAME` command is intentionally narrower than the Python
request registry. It derives its allowlist from catalog entries whose shape
and semantics have cited evidence, `controller_effect` is `read-only`, and
`reply_behavior` is `data`. It must also declare allowed reply commands and
correlation fields. This prevents a mutating or no-reply command from taking
effect and then looking like a failed request when no reply arrives.
The current set contains `get_setting`:

```sh
ruida-controller request get_setting \
  --udp 192.0.2.10 --values '{"address":5}' \
  --first-timeout 1 --idle-timeout 0.05 --total-timeout 5 \
  --max-chunks 1 --max-bytes 64 --expected-chunks 1
```

The values argument must be one strict JSON object. Reply time, chunk, and byte
bounds are validated before any device is opened. Every CLI request requires
an expected byte or datagram count. A serial request must use
`--expected-bytes` and cannot use `--expected-chunks`, because stream read
chunks are timing-dependent rather than protocol boundaries.

`send-job` decodes its input as a job-context `.rd` file and validates the
selected checksum policy before opening the link. Empty jobs and decoded jobs
with opaque or invalid frames are refused by default. Bypassing decode issues
requires `--allow-decode-issues`; it does not establish that those frames are
safe. Execution always requires the full acknowledgement flag and an explicit
checksum choice:

```sh
ruida-controller send-job reviewed-job.rd --udp 192.0.2.10 \
  --confirm-machine-execution --checksum recompute
```

That command can move axes, switch outputs, and fire the laser. The flag only
records caller intent; it does not replace review of the file, controller
state, work area, material, power, cooling, interlocks, emergency stop, and
physical supervision.

Use `--serial DEVICE` instead of `--udp HOST` for USB serial. UDP transcript
output is deliberately unavailable on serial because stream reads are not
datagram boundaries. For UDP, recording requires an explicit local address so
the transcript does not invent endpoint metadata:

```sh
ruida-controller probe --udp 192.0.2.10 \
  --local-host 192.0.2.20 --local-port 40200 \
  --transcript probe.json
```

Transcript output uses atomic no-clobber creation. `--force` is required to
replace an existing file, and cannot be supplied without `--transcript`.

Successful operations write JSON to standard output and exit `0`. Failures
write JSON to standard error. Usage, input, and output failures exit `2`;
controller or cleanup failures exit `1`; interruption exits `130`. These
status codes describe the CLI result, not whether physical delivery occurred.
Failure JSON can include:

- `receipt`, including completed packet count, transmissions, and retries;
- `delivery_certainty`, when the controller state machine can establish it;
- `phase`, `packet_index`, `code`, or logical reply bytes when applicable;
- `operation_completed`, which is true when the requested operation returned
  successfully before a later close or transcript-output failure; and
- `operation_result`, which preserves the successful receipt when a later
  cleanup or output step fails.

Never retry solely because the process returned a nonzero status. When
`operation_completed` is true, the requested action already completed. When
it is false, use the partial receipt and delivery certainty; `unknown` means a
retry may duplicate machine effects. A saved UDP transcript can provide the
wire evidence for diagnosis without changing that delivery conclusion.

### Direct UDP

The built-in UDP adapter defaults to local port 40200 and controller port
50200. `open(probe=True)` sends a Ruida keep-alive and waits for its response;
use `probe=False` when opening the socket must not transmit anything.
Even when an injected raw transport is already open, a new client starts
uninitialized: call `open()` so it drains stale input before the first
exchange. `assume_synchronized=True` is an explicit expert escape hatch for a
transport whose session boundary was established elsewhere.

```python
from ruida_re import ControllerClient, UdpTransport

transport = UdpTransport("CONTROLLER_IP")
client = ControllerClient(transport)
client.open(probe=False)
try:
    receipt = client.keep_alive()
finally:
    client.close()
```

For each outbound UDP packet the client applies scrambling and the packet
checksum, waits for the controller handshake, retries the same packet only
after a negative acknowledgement, and fails on an error or exhausted retry
budget. An acknowledgement timeout is reported without a blind resend because
the protocol has no transaction identifier proving that a timed-out command
was not executed. The client then enters `DESYNCHRONIZED` state and rejects
later operations. In-place `recover()` is deliberately refused. Closing and
reopening the same endpoint also cannot prove that a delayed response is gone:
the application must first establish link and controller quiescence and then
explicitly accept the residual correlation uncertainty before a new session.

The default `HandshakeProfile` accepts logical `CC` as a normal ACK and either
`CC` or `CE` for a keep-alive, retries logical `CF`, and rejects logical `CD`.
Those dispositions are declarative because pinned implementations disagree on
some controller behavior. An integration can supply a different profile when
hardware captures justify it.

Sending a job is deliberately a separate, explicit call:

```python
job = job_codec.decode(Path("reviewed-job.rd").read_bytes())

client.open(probe=True)
try:
    receipt = client.send_job(job, checksum_policy="recompute")
finally:
    client.close()
```

The `send_job` call can cause physical motion or laser output. Do not run this
example against a machine unless the file, controller state, work area,
material, power state, interlocks, and emergency-stop access have all been
reviewed under physical supervision.

### USB serial

Install the optional dependency, then provide the operating-system serial
device name:

```sh
python3 -m pip install -e '.[serial]'
```

```python
from ruida_re import ControllerClient, ReplyPolicy, SerialTransport

transport = SerialTransport("/dev/ttyUSB0")
client = ControllerClient(transport)
client.open()
try:
    response = client.request_command(
        "get_setting",
        address=5,
        reply_policy=ReplyPolicy(expected_bytes=9),
    )
finally:
    client.close()
```

The default is 115200 baud, 8 data bits, no parity, and one stop bit. The
serial path sends scrambled payload without the UDP checksum or UDP
acknowledgement wait. Replies are still direction-aware Ruida data. Device
naming and access permissions remain operating-system concerns; for example,
Windows normally uses a name such as `COM3`.

The nine-byte setting reply is reported independently by the pinned ruida-pa
and MeerK40t implementations. It is not a guarantee that every address or
controller dialect returns that shape; use a different evidence-backed
completion predicate when required.

### Request/reply results

`send_no_reply_request(program)` sends only structured commands whose catalog
contract declares that they return no data.
`request(program)` and `request_command(name, **values)` additionally collect
and decode the following reply stream. They return a
`ControllerResponse` containing:

- `receipt`, with transmitted packets, transmission count, retry count, and
  the number of packets whose delivery completed;
- `program`, decoded in the reply context;
- `wire_chunks`, the original ordered UDP datagrams or serial read chunks; and
- `logical`, the assembled unscrambled reply bytes.

The offline decoder preserves unknown reply frames, but a live typed request
does not call them success. It requires zero decode issues, exactly one known
reply command allowed by the request contract, and equality for every declared
request-to-reply field match. A mismatch faults the session while retaining
the completed send receipt and delivery certainty. A reply-producing UDP
request must fit one datagram; the library rejects a split request rather than
guessing which packet caused the reply. Serial writes form one stream and may
be split.

`ReplyPolicy` provides a total deadline, chunk and byte bounds, and optional
exact chunk count, byte count, or completion predicate. Datagram links may use
a bounded idle gap when no explicit rule is supplied. Stream links reject
that policy: they require an expected byte count or content predicate and
forbid chunk-count completion. This prevents an operating-system read split
from defining completion. Immediate excess and stale preflight input fault the
session, but no local policy can prove that a later response will never arrive.

### Synchronous ownership

The client starts no worker thread. `open`, `keep_alive`, `send_job`, and
request methods block until their configured timeout or result. An internal
lock serializes complete exchanges even when callers use multiple threads.
Treat one client as one protocol session:

1. Queue application requests onto one worker when the UI must remain
   responsive.
2. Do not read from or write to the underlying transport while the client owns
   it.
3. Inspect the attached partial receipt and delivery certainty on failures.
4. Close a desynchronized session. Establish link and controller quiescence
   before opening a new one, and never assume a partial job can be resumed.

Cancellation follows the same rule. An interrupt after transmission attaches
the partial receipt and delivery certainty, faults the session, and then
propagates. A failed cleanup never replaces the original exchange failure.

This ownership rule keeps ACKs and replies associated with the request that
caused them. A host can put asynchronous or event-driven behavior above the
synchronous boundary without duplicating Ruida state handling.

### Custom adapters

An application can inject its own transport by implementing the small
`ControllerTransport` protocol:

```python
class ControllerTransport:
    kind: str

    @property
    def is_open(self) -> bool: ...
    def open(self) -> None: ...
    def close(self) -> None: ...
    def send(self, data: bytes) -> None: ...
    def receive(self, timeout: float) -> bytes | None: ...
    def drain(self, limit: int = 256) -> tuple[bytes, ...]: ...
```

The built-in link factory recognizes transport kinds `"udp"` and `"serial"`.
It selects once at the boundary; the exchange state machine does not branch on
transport kind. A custom transport kind must be paired with an explicit
`RuidaLink` implementation when constructing `ControllerClient`. A vendor TCP
bridge cannot be labelled as UDP or serial unless its additional envelope and
exchange behavior are implemented and evidenced separately.

An explicit link also declares `receive_boundaries` as `"datagram"` or
`"stream"`. The latter activates content-based reply completion rules; a
transport read is never treated as a protocol boundary merely because it
returned bytes once.

```python
transport = MyTransport(...)
link = MyRuidaLink(transport, magic=0x88)
client = ControllerClient(transport, link=link)
```

The optional observer receives an `ExchangeEvent` only after successful wire
I/O. Events identify link, phase, wire context, enclosing exchange context,
raw and logical bytes, packet attempt, and a monotonic timestamp. Observer
errors do not alter protocol state.

For UDP, `TranscriptObserver` turns those events into a lossless transcript.
Endpoints are explicit so diagnostics never guess routing metadata:

```python
from ruida_re import Endpoint, TranscriptObserver

observer = TranscriptObserver(
    host=Endpoint("192.0.2.20", 40200),
    controller=Endpoint("192.0.2.10", 50200),
)
client = ControllerClient(transport, observer=observer)
client.open(probe=True)
try:
    client.keep_alive()
finally:
    client.close()

json_text = observer.transcript.to_json()
```

The observer preserves malformed datagrams and records their decode issues.
It rejects serial or custom-link events instead of inventing datagram
boundaries; `non_udp="ignore"` is available only as an explicit policy.

## Catalogs, schemas, and captures

The language-neutral command catalog is generated from the same registries as
the Python codec:

```sh
ruida-catalog --output catalog.json
ruida-conformance --output conformance.json
```

The repository publishes versioned JSON Schemas for the structural Program
envelope, the catalog, and UDP transcripts under `schemas/`, with a generated
catalog snapshot under `spec/`. The transcript schema embeds the Program
schema and can validate a complete transcript without an external resolver.
The catalog publishes every primitive field codec with declarative JSON
domains, base-128 layout, signed padding, units, scaling, packing, and rounding
behavior. Its source records include pinned provenance and licenses. Command
evidence fields distinguish observed shape, reported semantics, conflict, and
uncited hypothesis. `controller_effect` and `reply_behavior` provide a
declarative interaction policy. `reply_commands` and `reply_field_matches`
define typed response validation and correlation where evidence supports it;
unknown values remain ineligible for safe high-level interfaces.

The Program schema deliberately validates the versioned envelope rather than
duplicating hundreds of catalog-dependent command alternatives. Normative
semantic validation uses the selected Program context and `catalog-v1`:

1. Resolve each structured record by stable command name in that context.
2. Require the catalog opcode and the exact catalog field-name set.
3. Apply each field's declared codec JSON domain and conversion semantics.
4. If `raw` exists for a known record, decode it and require the same opcode,
   fields, and values before reusing the bytes.

JSON Schema defines `integer` by numeric value, so inputs such as `1.0` are
valid integers. Loaders must accept finite mathematically integral JSON
numbers for integer domains and normalize their emitted form to an integer;
booleans, non-finite values, and fractional numbers remain invalid.

All v1 schema numbers are limited to the interoperable JSON range
`-9007199254740991` through `9007199254740991`. Nonnegative metadata such as
record offsets and checksum bases uses the same upper limit; individual
catalog fields normally impose much narrower protocol limits. A strict loader
must validate integer-domain tokens before converting them: `5.0` is
integral, but `5.0000000000000001` is not. Number-domain values use the
catalog's IEEE-754 binary64 semantics, so bounded decimal input is
canonicalized to the nearest binary64 value. Loaders must reject an
out-of-range exponent before expanding it into an integer. Ordinary finite
physical values such as `0.1` remain valid numbers.

An older consumer may retain an unknown structured record only when `raw` is
present and matches its declared opcode; it then reproduces those bytes
without claiming to understand or safely edit them. The Python `Program`
implementation enforces this algorithm.

Installed applications can read those same resources from wheels and zip
imports without resolving a checkout path:

```python
from ruida_re import CATALOG_V1, read_artifact_json

catalog = read_artifact_json(CATALOG_V1)
```

`spec/conformance-v1.json` is the executable companion to the catalog. It
content-addresses the exact catalog and supplies canonical encode/decode cases
for every field codec, the full byte-scrambling domain, a fixture-derived job
checksum, and checksum-bearing request versus checksumless reply datagrams.
See [conformance vectors](conformance.md) for the downstream test procedure.

`Transcript` preserves UDP datagram boundaries, direction, raw bytes, optional
endpoints and timestamps, decoded Program IR, and checksum/decode issues:

```python
from ruida_re import Endpoint, Transcript

capture = Transcript()
capture.capture(
    raw_datagram,
    direction="inbound",
    context="reply",
    source=Endpoint("192.0.2.10", 50200),
)
json_text = capture.to_json()
```

Packet-local decoding may be opaque when one logical command crosses a packet
boundary. `capture.decode_flow("outbound", "job")` reassembles matching packets
within a selected transcript range before applying the command grammar.

An invalid outbound checksum remains in the transcript with an issue instead
of being discarded. The current transcript schema is UDP-specific; serial
stream capture needs an external read-boundary record until a separate stream
capture schema is defined.

## Evidence limits

Exact translation means unchanged bytes survive decode and encode, including
unknown records. It does not prove that every registered mnemonic is correct
or that emitting every construct is safe on every Ruida controller. In
particular:

- request and reply classification is still based mostly on pinned reference
  implementations rather than a checked-in hardware capture corpus;
- controller-specific command variants and magic values need more captures;
- absolute negative five-group spelling and several disputed field layouts
  remain open; signed relative X/Y motion is fixture-backed;
- controlled horizontal and vertical raster motion, grayscale modulation, and
  host-expanded 3D-slice motion are fixture-backed for one profile; source
  image reconstruction, arbitrary-angle raster metadata, and native Z motion
  remain outside that evidence; and
- live UDP and serial behavior has injectable test coverage, but not a public
  multi-controller compatibility matrix.

See [protocol notes](protocol.md) for layer-level facts and unresolved
questions, and [sources and provenance](sources.md) for the evidence policy.
