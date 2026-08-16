# ruida-re

`ruida-re` is a standalone, embeddable Ruida backend and protocol research
project. It is intentionally independent of Rayforge and of any particular
laser application.

The host application owns geometry construction, path planning, rasterization,
the user interface, and the decision to operate a machine. `ruida-re` owns the
Ruida-specific boundary: planned-motion compilation, validated command records,
lossless `.rd` translation, scrambling, packetization, direct UDP and
USB-serial adapters, UDP acknowledgement handling, request/reply decoding,
evidence-labelled catalogs, and boundary-preserving UDP capture records. It
does not provide a geometry engine, rasterizer, path planner, or TCP bridge.

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
   results, hardware observations, reported meanings, and disagreements remain
   distinguishable in both JSON and the command registry.
   `hardware-observed` is available on both evidence axes and ties a scoped
   shape or meaning to a physical-controller observation. It does not promote
   the same opcode in another context or claim universal controller behavior.

The current registries contain 186 host/job shapes, 74 provisional request
candidates, and 10 reply shapes spanning hardware observation, reports,
disputes, and simulation. Sixty-nine checked-in LightBurn exports exercise
80 unique job shapes; all 69 parse without opaque frames and reproduce
byte-for-byte. A temporary LibLaserCut golden file exercises 123 frames with
the same result. Those results validate the current framing model and exact
translation, not every mnemonic, request, reply, or controller dialect in the
broader catalog. One generated mixed vector/raster job has also completed
successfully on one operator-observed Ruida 644XS setup. This is still
pre-alpha software, not a claim that the protocol is complete or that other
controller profiles have been validated.

Importing the package, constructing a codec, and translating bytes perform no
device I/O. The controller API is different: opening with its default UDP
probe sends a keep-alive request, and job methods transmit executable machine
data. Controller access is always an explicit application action.

## Install

No `ruida-re` package has been published to PyPI yet. After an official
release is announced from this repository, the normal installs will be:

```sh
python3 -m pip install ruida-re
python3 -m pip install 'ruida-re[serial]'
```

Until then, install the current checkout directly:

```sh
python3 -m pip install .
python3 -m pip install '.[serial]'
```

For an editable development install with the test dependencies:

```sh
python3 -m pip install -e '.[test]'
```

Python 3.11 or newer is required. The core codec and direct UDP runtime use
only the Python standard library. The `serial` extra adds USB serial support.
Before reporting a vulnerability or behavior that could cause unintended
laser emission or machine motion, follow the
[security policy](https://github.com/ens1/ruida-re/security/policy) instead of
opening a public issue.

## Compile a planned job

`RuidaJobCompiler` lowers an emission-ready, machine-space plan into a complete
`.rd` file. Compilation is offline: it derives the envelope, bounds, layer
metadata, motion records, job checksum, and termination records without
opening or contacting a device.

```python
from ruida_re import (
    JobPlan,
    LayerPlan,
    MarkTo,
    RuidaJobCompiler,
    SetModulation,
    TravelTo,
)

plan = JobPlan(
    layers=(
        LayerPlan(
            index=0,
            kind="raster",
            speed_mm_s=100.0,
            min_power_percent=10.0,
            max_power_percent=90.0,
            scan_axis="horizontal",
            raster_strategy="bidirectional",
            air_assist=True,
            events=(
                TravelTo(20.0, 20.0),
                SetModulation(50.0),
                MarkTo(30.0, 20.0),
            ),
        ),
    )
)
machine_file = RuidaJobCompiler().compile(plan).encode_rd()
```

Coordinates are absolute machine-space millimetres, speed is millimetres per
second, and layer power and modulation are percentages from 0 through 100.
`SetModulation(m)` selects the normalized position within the layer range, so
effective output is
`minimum + m / 100 * (maximum - minimum)`; it is not an absolute output
percentage.
One exact Rayforge-generated horizontal variable-power row using those
semantics has completed a supervised Boss LS2040 transfer at 100 mm/s. The
5%-15% layer artifact used paired normalized `C7`/`C2` values and only `AA`
marking chunks no longer than 4 mm; the operator reported, "Everything is as
expected". The
[scoped manifest](fixtures/hardware/boss-ls2040-usb-serial-rayforge-variable-raster-v1/manifest-v1.json)
records the exact 23 mm mark, 11 mm travel gap, and 26 mm mark plan. This is
visual evidence for that single row, not dimensional or power metrology,
proof of zero optical output in the gap, or broad raster validation.
An exact two-layer ordinary raster matrix separately exercised horizontal and
vertical bidirectional native raster at requested 20% power and 100 mm/s. Its
marked motion used only `AA` and `AB` chunks no longer than 4 mm, with travel
motion for the gaps. After the supervised transfer, the operator confirmed the
expected three broken horizontal rows and three broken vertical columns. The
[scoped matrix manifest](fixtures/hardware/boss-ls2040-usb-serial-rayforge-ordinary-raster-matrix-v1/manifest-v1.json)
binds that observation to the exact artifact. It is not dimensional or power
metrology, proof of zero optical output in the gaps, or unidirectional-raster
evidence.

An exact 769-byte production-path coupon separately exercised horizontal and
vertical unidirectional native raster at requested 20% power and 100 mm/s.
After one host packet with zero retries, the operator reported 12 expected
marks, no burnt return moves, and unchanged Z. The
[scoped unidirectional manifest](fixtures/hardware/boss-ls2040-usb-serial-rayforge-unidirectional-raster-v1/manifest-v1.json)
binds that observation to the exact artifact and mode pairs. It is visual and
controller-display evidence, not directional, dimensional, power, zero-output,
or Z-motion metrology.

Normal full-layer job-context air assist has a scoped positive observation on
the tested Boss LS2040. An exact air-off motion control produced an ambiguous
operator report of possible airflow masked by motor noise. Its paired 580-byte
air-on motion artifact was then transferred once with one host packet and zero
retries; the operator reported, "Air assist is confirmed, I felt the solenoid
turn on then off". A separately approved standalone `CA01` OFF-ON-OFF sequence
contained no motion, marking, or laser-enable commands and used a
5.002178-second host interval. All three USB-serial writes and flushes
completed, but the operator reported no change or relay/solenoid click. The
[scoped air-assist manifest](fixtures/hardware/boss-ls2040-usb-serial-rayforge-air-assist-v1/manifest-v1.json)
records the exact bytes and observations. Serial provided no controller or
state acknowledgement. The full-layer result is tactile operator evidence, not
pressure, flow, timing, relay-routing, current, or electrical metrology, and it
does not establish other controllers or UDP. It does not validate the
standalone sequence as a manual toggle or change compiler behavior.

The host must already have rendered images, generated scanlines, selected
blank and marked spans, optimized paths, applied machine transforms, and
expanded passes. `TravelTo`, `MarkTo`, and `SetModulation` describe that final
ordered result; they are not image or geometry primitives. The adapter must
preserve explicit step regimes such as process kind, speed, power limits, air,
head, scan axis, and scan strategy. It must split layers when a regime changes
and reject missing or ambiguous metadata rather than infer controller state.

The conservative `LIGHTBURN_2103_644XS` profile supports planar XY vector
motion, one laser head, air assist, and the four native horizontal/vertical
scan modes. Diagonal and cross-hatch output requires the explicit
`LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH` profile. The host supplies final
`TravelTo`/`MarkTo` paths grouped into `RasterSection` objects and sets
`raster_processing="planned-path"`. LightBurn's controlled diagonal exports
use general signed X/Y path motion; there is no evidenced Ruida angle field
for the compiler to fill in. `SetModulation` is not yet supported in a
planned-path section.

Advanced capability surfaces remain isolated behind explicit research
profiles. Exact single-section and two-section cross-hatch planned-path
subsets have positive operator-observed execution. Four dynamic-power jobs
have scoped operator observations: two together exposed persistent active
power and a missing restore, while corrected one-restore and two-restore
sequences executed successfully. A paired logical-Z subset also has scoped
controller-readout observations. Dual-head, RF, fiber, and the unobserved
stationary subsets retain offline fixture evidence only:

| Profile | Plan surface | Controlled serialization |
| --- | --- | --- |
| `LIGHTBURN_2103_644XS_PLANNED_PATH_RESEARCH` | one planned-path raster layer | host-planned diagonal/cross-hatch XY sections |
| `LIGHTBURN_2103_644XS_DUAL_LASER_RESEARCH` | two `LaserChannelPlan` entries | head-enable mask and independent layer/active power |
| `LIGHTBURN_2103_644XS_STATIONARY_RESEARCH` | `Dwell`, `Pulse` | non-marking `C6 11` dwell and marking `C6 10` pulse |
| `LIGHTBURN_2103_644XS_RF_RESEARCH` | `frequency_hz` | paired `C6 60` values in hertz |
| `LIGHTBURN_2103_644XS_FIBER_RESEARCH` | `pulse_width_ns` | `C6 66` value in nanoseconds |
| `LIGHTBURN_2103_644XS_Z_RESEARCH` | `z_offset_mm` | balanced, inverse `80 03` deltas around one native raster layer |
| `LIGHTBURN_2103_644XS_DYNAMIC_POWER_RESEARCH` | stateful `MarkWithPower`, baseline `MarkTo`, producer-only `MarkWithCurrentPower` | explicit effective active powers and baseline restoration |

These profiles reproduce controlled LightBurn machine files exactly. A
single-section 45-degree planned path and a separate two-section cross-hatch
path have also executed successfully on one Boss LS2040 configured as a Ruida
644XS. The exact cross-hatch evidence is in its
[companion manifest](fixtures/hardware/ruida-644xs-usb-serial-planned-path-v1/cross-hatch-observation-v1.json).
The broader planned-path mode remains research-only because these exact coupons
do not validate arbitrary paths or settings. The profiles are opt-in and
intentionally narrow; the default compiler still rejects those fields. The
fixture-derived built-in limits are 200 ms for stationary events, 10–20 kHz
for RF frequency, 0–200 ns for fiber pulse width, and 1 mm absolute Z offset.
The dual-head, stationary, RF, fiber, and dynamic-power profiles accept
exactly one vector layer at index zero. Planned-path and Z profiles each
accept exactly one raster layer at index zero; Z requires native raster. The
host must provide resolved per-head powers to `MarkWithPower`, not a source
application's PowerScale value. Cut-through remains unsupported because the
fixtures do not distinguish start from end behavior or establish independent
head-2 through power. Rotary remains blocked without an exported rotary
template and hardware. Passes remain host-expanded. Controlled changes to
`zPerPass` and material height produced byte-identical `.rd` files, so neither
is treated as a Z command.

Two exact 673-byte native-raster jobs used logical Z offsets of +1.0 and
-1.0 mm. The positive case serialized `80 03` deltas of -1.0 then +1.0 mm;
the negative case reversed the pair. Each host transfer reported one packet
and zero retries without controller or execution acknowledgement. From a
reported 18.2 mm starting readout, the operator observed 17.2 mm during the
positive job and 19.2 mm during the negative job, followed by returns to
18.2 mm. The negative job's marks were reported as expected, with no collision
or unexpected movement. The
[paired logical-Z manifest](fixtures/hardware/boss-ls2040-usb-serial-rayforge-logical-z-v1/manifest-v1.json)
binds those reports to the exact payloads. This is controller-readout evidence,
not independent physical-direction, displacement, accuracy, backlash, or
repeatability metrology. Interrupted restoration, other offsets and layer
structures, and broader compatibility remain unvalidated; the profile stays
research-only.

Dynamic vector power is stateful. Layer setup establishes the baseline.
`MarkWithPower` emits resolved per-channel active powers, marks to its endpoint,
and leaves those powers active. A following ordinary `MarkTo` means baseline
layer power, so the compiler emits an explicit baseline-power envelope before
that mark. `MarkWithCurrentPower` deliberately keeps the active override and
exists only to reproduce a known producer stream; normal host lowering should
not use it. Consecutive `MarkWithPower` events each emit their own explicit
envelope, and the compiler does not invent an end-of-layer restore. Hosts can
require this behavior through `DYNAMIC_POWER_RESTORE_CONTRACT == 1`.

The first three scoped observations are recorded in the
[dynamic-vector hardware manifest](fixtures/hardware/boss-ls2040-usb-serial-rayforge-dynamic-vector-v1/manifest-v1.json),
and the repeated-restore result has a
[content-addressed companion manifest](fixtures/hardware/boss-ls2040-usb-serial-rayforge-dynamic-vector-v1/dynamic-repeated-observation-v4.json).
An initial 15%-10%-15% coupon looked solid and was inconclusive. A longer
15%-5%-15% coupon moved continuously but visibly marked only its first 30 mm.
Its reviewed payload lowered active power before the middle span and omitted a
baseline restore before the last span. For that controller and job, the result
is consistent with the lower active state persisting and both later spans
remaining below the cardboard's visible marking threshold. A corrected
15%-5%-15% coupon inserted a second operation-5 envelope restoring the layer's
5%-15% baseline before the final mark. The operator reported an approximately
30 mm line, a gap, and another approximately 30 mm line. The one-packet host
transfer had no retry, controller acknowledgement, or execution
acknowledgement. This supports the explicit restore for that exact one-layer,
one-enabled-command-channel, 100 mm/s path; it is not calibrated power
metrology, physical channel-routing evidence, or mode-wide validation, and the
profile remains research-only.

A fourth 15%-5%-15%-5%-15% artifact encoded two reductions and two explicit
restorations across five planned 16 mm spans. The operator reported three
lines and two gaps. The approximate reported lengths are not metrology, and
the gaps are not proof of zero optical output. This supports only that exact
repeated-restore sequence.

### Zero-power safety boundary

An exact nominal-0% no-dwell control emitted visible laser power and drew a
rectangle on the same Boss LS2040. The operator reported, "There was laser
emission. I see a clearly drawn rectangle, maybe 25mmx50mm". The cause was not
isolated: raw zero may mean default, stale, or no update; a controller or
power-supply firing floor or another field may contribute. Minimal
through-power fields were present, but their effect was not isolated. The
negative evidence and paired program are recorded in the
[zero-power safety manifest](fixtures/hardware/boss-ls2040-usb-serial-zero-power-safety-v1/manifest-v1.json).

The paired job containing four C6 11 dwell records was stopped before
transfer, and the earlier planned Z coupons were also withheld and remain
quarantined offline. That C6 11 pair and those earlier Z artifacts therefore
remain hardware-unobserved. Both zero-power programs are published only with
`.rd.quarantined` suffixes and are do-not-send evidence. The later paired
logical-Z readout observations above do not turn the quarantined dwell pair
into sendable evidence or establish physical Z metrology.

The compiler now rejects marking when any enabled channel's minimum or maximum
would encode below raw power 16, and applies the same floor to raster marking
modulation. This is a conservative generation boundary based on the observed
producer field floor, not proof that raw 16 or another low setting is
physically safe. It does not inspect, rewrite, or make safe any existing,
cached, hand-authored, or externally supplied `.rd` file.

### First live validation

The first live test, recorded in the
[hardware-validation manifest](fixtures/hardware/ruida-644xs-usb-serial-v1/manifest-v1.json),
used the configured Ruida 644XS profile over macOS USB serial at 115200 baud
with scrambling magic `0x88`. Before sending a job, a read-only `DA 00`
request for address 5 returned a fixed nine-byte `DA 01` reply for address 5
with value 300000.

That exchange gives request-context `DA 00` and reply-context numeric `DA 01`
both `hardware-observed` shape and semantic evidence. It does not promote
job-context `DA 00`, whose shape and semantics remain `reported`, or the
state-changing `DA 01` setter, whose shape is `fixture-observed` and semantics
remain `reported`.

The generated mixed vector/raster job was 689 bytes and decoded as 107 known
records with no issues. Its bounds were X 20 through 30 mm and Y 20 through
26 mm. It used 100 mm/s, 20% maximum power, raster modulation at 10%, 15%, and
20%, and air assist off. After explicit approval, the same job was transmitted
twice. The operator saw activity on the first attempt but reported that the
material was misplaced; after repositioning the material, the operator
reported complete success on the second attempt.

That result is an operator observation, not automatic sensor or output
verification. It covers one physical controller/profile and one job. It does
not validate every command, transport, controller model, or safety behavior.
The host remains responsible for job review, authorization, supervision, and
machine safety.

### Planned-path live validation

The scoped
[planned-path hardware manifest](fixtures/hardware/ruida-644xs-usb-serial-planned-path-v1/manifest-v1.json)
records two supervised executions of the same 574-byte, one-section
45-degree job. It contained five separate marks at 100 mm/s within X 20
through 32 mm and Y 20 through 40 mm. The 10% run produced all five intended
motions but no visible cardboard mark. The otherwise byte-identical 15% run
produced five correct lines with no connecting burn and no reported
over-burning.

That observation validates only the exact single-section, constant-power,
head-1 subset. The job used five absolute travel/cut pairs and did not contain
the operation-5 separator used by multiple sections. A later
[cross-hatch companion](fixtures/hardware/ruida-644xs-usb-serial-planned-path-v1/cross-hatch-observation-v1.json)
records a 666-byte Rayforge artifact containing two sections, one operation-5
separator, and five marks in each diagonal direction. At 15% and 100 mm/s, the
operator reported both directions visible and no connection burns. The small
reported edge is a decoded 0.3507 mm `cut_relative` mark, with no C6 10 pulse
record. Its one-packet, zero-retry host receipt has no controller or execution
acknowledgement.

The exact observations do not validate arbitrary cross-hatch paths,
135-degree or relative diagonal motion generally, grayscale modulation, dense
scanning, other settings, or other controllers. The mode-wide profile retains
its conservative `not-observed` execution label and research-only selector.

## Embed the codec

Decode and reproduce an `.rd` file without contacting a controller:

```python
from pathlib import Path

from ruida_re import RuidaCodec

codec = RuidaCodec(context="job")
source = Path("input.rd").read_bytes()
program = codec.decode(source, container="rd")
assert codec.encode(program) == source
```

Applications can construct validated records by stable command name while the
codec owns their Ruida byte representation:

```python
move = codec.command("move_absolute", x_mm=20.0, y_mm=20.0)
end = codec.command("end_of_file")
logical = codec.encode_commands([move, end], container="logical")
```

For controller access, use `ControllerClient` with either `UdpTransport` or
`SerialTransport`. The following code opens the socket without an implicit
probe, then explicitly transmits one Ruida keep-alive packet. It does not send
a job:

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

`ControllerClient.stop_process()` sends the evidence-labelled Ruida software
stop request through the same serialized session. It is not an emergency stop.
Its receipt proves only the link-specific exchange: UDP success includes a
controller packet acknowledgement, while serial success records a completed
host write without an acknowledgement. Neither result proves that physical
execution has halted, so an integration must retain any execution-unknown
state until it independently establishes that the controller is idle.

`ControllerClient.read_machine_status()` is an experimental read-only status
query. It returns an immutable `MachineStatus` with the raw numeric word,
reported `moving`, `job_running`, and `part_end` flags, and every remaining bit
in `unknown_bits`. The address and flag labels are implementation-reported;
the library does not infer idle state or execution completion from them. One
Boss LS2040 USB-serial capture observed the exact address-512 request and a
correlated zero reply, but did not include an operator physical-state report
or an active-to-complete transition. See the integration guide before using
the result for machine state.

The same bounded DA00 path provides experimental raw focus and position reads:

```python
focus = client.read_focus_depth()
x = client.read_current_x()
y = client.read_current_y()
z = client.read_current_z()
```

The fixed semantic U14 addresses are `0x010E`, `0x0221`, `0x0231`, and
`0x0241`. Their meanings come from pinned implementations. Operator-supervised
USB-serial snapshots from one controller returned values from those addresses;
an exact-controller-associated private LightBurn export identifies Focus
Distance as 9.3 while one `0x010E` read returned raw 9300. The operator supplied
both artifacts, but the controller model identity was not independently
verified. That was initial one-point corroboration. The supervised validation
below adds raw 9400 alongside display 9.4 on the same controller, but still
does not establish a general conversion contract. Every result preserves
`raw_value`; `hypothesized_mm` remains a
non-authoritative interpretation, not a calibrated measurement or a value
safe to write back generically.

`build_autofocus_candidate()` constructs logical `D8 2E` for offline capture
planning. Its effect and reply behavior remain unknown, so
`send_no_reply_request()` refuses it. There is no live autofocus trigger.

An experimental USB-serial-only
`compare_and_set_focus_distance_raw()` operation reads `0x010E`, requires an
exact expected value and `confirm_unverified_write=True`, then emits one
implementation-reported DA01 command with the requested raw value duplicated.
It accepts only the built-in serial link with the evidenced magic `0x88`.
Both raw values must be from `FOCUS_DISTANCE_RAW_MIN` (0) through
`FOCUS_DISTANCE_RAW_MAX` (1000000000). Those limits reproduce the audited
LightBurn metadata and signed-integer setter representation. They are
representation guards, not controller capability, validity, or machine-safe
focus claims.
The library imposes no fixed per-call delta because legitimate lens changes
can require a large adjustment.

The immutable result records the prior and requested raw values plus host-side
serial send progress. It is not a controller acknowledgement and performs no
readback. One supervised exact-controller validation sent 9300 to 9400 once,
then observed three raw-9400 reads through a fresh connection. The operator
reported a 9.4 Focus Distance display and a completed panel-Autofocus endpoint
of Z 9.4. One 9400-to-9300 rollback and three fresh raw-9300 reads followed;
the operator then reported a restored panel-Autofocus endpoint of Z 9.3. Each
DA01 receipt contained one packet, one transmission, and zero retries. See the
[scoped evidence fixture](fixtures/hardware/operator-controlled-ruida-usb-serial-focus-distance-write-v1/manifest-v1.json).

That result applies only to the exact controller and two values. It includes
no reset or power-cycle persistence test, controller acknowledgement,
independent position metrology, probe-trigger capture, or contact-coordinate
validation. Panel Autofocus was used; logical `D8 2E` was not transmitted.
Generic structured request and job sends reject a `set_setting` record for
`0x010E`; the low-level codec, opaque-record paths, and direct transport access
are protocol research tools, not a safety sandbox.

**Machine safety:** controller calls can move axes, change outputs, or start
work depending on the records sent and controller state. Use an idle machine,
known-safe material and power state, physical supervision, and the machine's
normal interlocks. Never use an unreviewed decoded or generated job as a live
test vector.

`ControllerClient` is synchronous and serializes whole exchanges. Queue calls
from a worker when a UI must remain responsive, and never interleave direct
transport reads or writes with client operations. An uncertain delivery or
reply timeout faults the session and in-place recovery is refused. Because
the protocol has no transaction identifier, even reopening the same endpoint
cannot prove that every delayed response is gone. Close the session and start
a new one only after the application has established link and controller
quiescence, while treating residual correlation risk explicitly. See the
[integration guide](docs/integration.md) for link profiles, bounded reply
policies, custom transports, request flow, and diagnostics.

## Controller command line

The installed `ruida-controller` command provides a noninteractive, JSON
operation surface. Select exactly one direct link. A UDP probe sends a Ruida
keep-alive and waits for a controller acknowledgement:

```sh
ruida-controller probe --udp 192.0.2.10
```

The safe `request` surface is derived from catalog interaction metadata and
exposes only evidence-backed, read-only commands known to produce reply data.
The command must also declare its accepted reply name and correlation fields.
Currently that set contains `get_setting`:

```sh
ruida-controller request get_setting \
  --udp 192.0.2.10 --values '{"address":5}' \
  --max-chunks 1 --max-bytes 64 --expected-chunks 1
```

Every CLI request requires an explicit completion rule. UDP can use an
expected datagram count or byte count. Serial must use `--expected-bytes` and
cannot use read-chunk counts, because operating-system stream reads are not
protocol boundaries.

Sending a job can move the machine and produce laser output. It requires both
an unmistakable execution acknowledgement and an explicit checksum policy:

```sh
ruida-controller send-job reviewed-job.rd --udp 192.0.2.10 \
  --confirm-machine-execution --checksum recompute
```

The command decodes and validates the `.rd` file before opening a transport.
Decode issues stop execution unless `--allow-decode-issues` is explicit.
USB serial uses `--serial DEVICE` instead of `--udp HOST`.

UDP transcripts require an explicit local endpoint and are atomic,
no-clobber files unless `--force` is supplied:

```sh
ruida-controller probe --udp 192.0.2.10 \
  --local-host 192.0.2.20 --transcript probe.json
```

Success writes one JSON object to standard output. Failures write JSON to
standard error; an interrupted operation exits `130`. A nonzero status alone
never proves that a machine operation did not complete: inspect
`operation_completed`, `delivery_certainty`, and any partial `receipt` before
deciding whether a retry is safe. Serial transcript output is refused because
serial reads are stream chunks, not UDP datagrams.

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

The host/job catalog is deliberately broad. The request view remains a
provisional opcode-family subset, not a complete direction classifier. Only
request-context `DA 00` and reply-context numeric `DA 01` have
`hardware-observed` shape and semantic evidence; one exchange is not a broad
request/reply capture corpus.

Inspect the evidence-labelled registry:

```sh
ruida-spec --context job
ruida-spec --context request
ruida-spec --context reply
```

Generate the versioned, language-neutral command catalog:

```sh
ruida-catalog --output catalog.json
ruida-conformance --output conformance.json
```

The structural Program envelope, catalog, conformance vectors, and UDP
transcript formats have JSON Schemas under `schemas/`; the transcript schema
is self-contained. The generated catalog snapshot under `spec/` supplies
normative command and field validation. Its companion vectors exercise every
primitive field codec, byte scrambling, job checksum construction, and
direction-aware UDP framing. Applications can read the same versioned
artifacts without repository paths:

```python
from ruida_re import CATALOG_V1, CONFORMANCE_V1, read_artifact_json

catalog = read_artifact_json(CATALOG_V1)
vectors = read_artifact_json(CONFORMANCE_V1)
```

The conformance artifact also includes a nested `serial_vectors` exchange for
the captured address-5 request and reply. The v1 schema makes that group
optional so older v1 documents remain valid; the current generator always
includes it and the current tests require and exercise it.

See the [conformance guide](docs/conformance.md) for the language-neutral test
contract.

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
python3 -m pip install -e '.[test]'
PYTHONPATH=src:tests python3 -m unittest discover -s tests -v
```

The suite covers byte scrambling, numeric boundaries, signed coordinate
forms, synthetic encode/decode symmetry for every registered schema, every
split point of every registered shape, semantic-frame isolation, arbitrary
binary and JSON round trips, packet framing, checksum updates, versioned
catalog and transcript data, injectable UDP and serial adapters, controller
session state, and real LightBurn output. The automated suite does not contact
hardware.

The LibLaserCut comparison can be repeated without checking its LGPL fixture
into this repository:

```sh
liblasercut_root='https://raw.githubusercontent.com/t-oster/LibLaserCut'
liblasercut_commit='ebe72ea3af3b2ab52d797d8100c635f68722100e'
liblasercut_fixture='test-output/'\
'de.thomas_oster.liblasercut.drivers.Ruida.out'
liblasercut_sha='5842a78ecb9abd195db502551b95de4d4'
liblasercut_sha="${liblasercut_sha}10cebe16cf2212fbad8d7bcf32a0500"
curl -L "$liblasercut_root/$liblasercut_commit/$liblasercut_fixture" \
  -o /tmp/liblasercut-ruida.out
ruida-verify /tmp/liblasercut-ruida.out \
  --expected-sha256 "$liblasercut_sha" \
  --require-structured
```

## LightBurn fixture workflow

LightBurn is used as a reference compiler, not as a runtime dependency.

Generate fresh baseline and discovery projects under `work/`:

```sh
ruida-fixture generate
ruida-matrix generate
ruida-advanced generate
ruida-raster-fixture generate
ruida-capability-fixture generate \
  --directory work/lightburn-2.1.03/capabilities
```

The advanced-capability matrix uses explicit profile evidence. On macOS, make
an offline snapshot of the one Ruida device entry in a caller-selected
LightBurn preferences JSON file, then derive five deterministic research
clones:

```sh
capability_root=work/lightburn-2.1.03/capabilities
ruida-lightburn-profile snapshot LIGHTBURN_PREFS_JSON \
  "$capability_root/profiles/ruida-644xs-active.lbdev"
ruida-lightburn-profile generate \
  "$capability_root/profiles/ruida-644xs-active.lbdev" \
  "$capability_root/profiles/research"
```

The clones change exactly one setting: `EnableZ`, `Laser2Enabled`,
`Laser1IsRFTube`, `Laser1IsFiber`, or `SaveRotaryConfig`. Generating or
snapshotting profiles does not launch LightBurn or contact a controller.

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
ruida-raster-fixture record
```

Capability recording is incremental and requires both the profile artifact
used for the export and an explicit attestation that LightBurn's **Save RD
file** action was used. For example, record an ungated export against the
active profile, or a Z-gated export against the `enable-z` matrix variant:

```sh
capability_root=work/lightburn-2.1.03/capabilities
ruida-capability-fixture record --directory "$capability_root" \
  --profile-evidence \
  "$capability_root/profiles/ruida-644xs-active.lbdev" \
  --attest-lightburn-save-rd

ruida-capability-fixture record --directory "$capability_root" \
  --profile-evidence \
  "$capability_root/profiles/research/lightburn-profile-matrix.json" \
  --profile-variant enable-z --attest-lightburn-save-rd
```

Record each newly exported profile group before adding exports made with a
different profile. The manifest binds the project, `.rd`, selected profile,
and attestation hashes. The attestation states that no job was transmitted;
whether LightBurn itself opened a configured controller connection is
deliberately recorded as `not-attested`.

Derive and strictly analyze one family without launching LightBurn:

```sh
capability_root=work/lightburn-2.1.03/capabilities
ruida-experiment derive "$capability_root/capabilities.json" diagonal-raster
ruida-experiment analyze \
  "$capability_root/diagonal-raster.experiment.json" \
  --output "$capability_root/diagonal-raster.report.json" --strict
```

Maintainers can publish a captured matrix through the no-clobber promotion
step. It copies only captured c001-c044 project/RD pairs, keeps c045-c052
blocked, sanitizes volatile local profile paths, retains the original profile
hashes separately from the published hashes, and regenerates strict reports:

```sh
capability_root=work/lightburn-2.1.03/capabilities
ruida-capability-fixture promote --directory "$capability_root" \
  --output-directory fixtures/lightburn-2.1.03/capabilities
```

Raster discovery projects embed tiny synthetic grayscale PNGs and remain
`pending` until every project has a corresponding LightBurn `.rd` export.
They cover protocol-facing scan direction, scan axis, interval, variable
power, and four-pass 3D slicing. The capability matrix adds diagonal planned
paths, two laser heads, stateful effective vector power, stationary dwell and
pulse, RF frequency, fiber pulse width, Z-offset candidates, and negative
results for other controls. These tools do not implement image processing or
send data to a controller.

Advanced recording is incremental: it records every available export and
labels unavailable discovery cases. LightBurn exported the
multilayer, relative-motion, and mixed vector/raster cases. It rejected the
negative machine-space case because no shape remained inside the configured
work area, so that case is retained as `blocked` without inventing an `.rd`
result.

Generators refuse to overwrite an existing project or manifest unless
`--force` is explicit. They also accept `--directory`, so an installed command
writes only to a caller-selected path or under `work/` in the current
directory. The checked-in `fixtures/` tree is never a default output target.

See [protocol notes](docs/protocol.md) for the layers and verified facts, and
[sources and provenance](docs/sources.md) for the source and provenance policy.

## License

Original `ruida-re` material is released under the
[Do What The Fuck You Want To Public License, Version 2](LICENSE). Applicable
permissions and attribution for referenced third-party source expression are
preserved in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
