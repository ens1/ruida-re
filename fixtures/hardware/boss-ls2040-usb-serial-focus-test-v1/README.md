# Ruida Focus Test scoped hardware observation

This note records supervised operator observations made on 2026-08-16 with
one Boss LS2040 configured as a Ruida 644XS and connected over USB serial. It
does not include a raw serial transcript or a checked-in `.rd` payload.

An initial five-layer stepped Focus Test ran with the laser source physically
disabled. The operator observed the planned X and Z motion. After completion,
the displayed Z returned to 9.300 mm and Focus Distance remained 9.300 mm.

An exact 128-layer Focus Test file was then rejected by the controller as
invalid before motion. Current Z and Focus Distance remained at raw value
9300. A 30-layer broad-range transfer attempted immediately afterward produced
no visible motion and left the separate current-Z read at 3000000 until
the operator ran panel Autofocus. That attempt is classified as indeterminate
because the controller's preceding invalid-file state had not been cleared; it
is neither positive nor negative evidence for the 30-layer profile.

After restoring a clean Autofocus baseline, an exact 30-layer, 60 mm Focus
Test spanning displayed Z 9.300–10.300 mm at 10 mm/s, 1% power, and air off was
transferred in seven host-reported packets. Its SHA-256 was:

```text
69bf75754534e9267ae764729747ea1a4cd85555e78dca30b07612d9fc33ddb8
```

The controller accepted and executed that file. The operator visibly observed
X motion and Z stepping. No visible mark was expected or observed at 1% power.
After execution, status was `0x10600`, current Z was raw 9296, and Focus
Distance was raw 9300. Thus the observed final current Z had a -4 micrometre
residual relative to the raw-9300 baseline despite the payload's zero net
wire-delta sum.

A later exact 30-layer positive-power Focus Test spanned displayed Z
0.500–25.500 mm from a 9.300 mm baseline, corresponding to logical targets
+8.800 through -16.200 mm. It ran at 100 mm/s, 15% power, and air off in seven
host-reported packets. Its SHA-256 was:

```text
4aa7f8577589dd6e1424b385120bb078ffdb9964d3b1d426c23d4b919a1c5e76
```

The controller executed the file and the operator observed its visible line.
After execution, current Z was raw 9312 and Focus Distance was raw 9300, a
+12 micrometre current-Z residual from the baseline. Two subsequent narrowed
30-layer positive-power Focus Test jobs also executed on the same setup; their
complete payloads and raw transcripts are not part of this note.

This establishes operator-observed execution only for the exact 30-layer
controller/profile subset described above. It does not establish behavior for
31–127 layers, arbitrary power, speed, geometry, or Z ranges, other Ruida
controllers, physical Z displacement, accuracy, backlash, repeatability, or
interruption recovery. The exact 128-layer rejection likewise applies only to
the rejected artifact. The research profile therefore stops at the largest
positively observed count, 30, while retaining its broader limitations.
The Focus Test mode carries a scoped `operator-observed` label from this note;
the enclosing research profile retains its broader `not-observed` execution
label.
