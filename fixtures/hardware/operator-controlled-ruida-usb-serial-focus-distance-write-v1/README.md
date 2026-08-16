# Scoped Focus Distance write and rollback evidence

This fixture records one supervised Focus Distance change and rollback on one
exact operator-controlled Ruida controller over USB serial. It contains no
device path or device identifier.

No standalone raw transport transcript file was saved during the supervised
session, so no transcript hash is available. The manifest records the parsed
DA00 values, the returned `FocusDistanceWriteReceipt` and `SendReceipt` fields,
and the operator's contemporaneous attestations that were retained from the
session.

The host first read address `0x010E` three times and received raw 9300 each
time. It then called `compare_and_set_focus_distance_raw()` once for
9300 to 9400. The returned `SendReceipt` contained one DA01 packet, one
transmission, one completed packet, and zero retries. After closing the write
session, a separate client session opened a fresh connection and read raw 9400
three times. The operator reported that the controller's Focus Distance
display showed 9.4 and that a completed, panel-invoked Autofocus routine ended
with a Z display of 9.4.

The rollback followed the same shape: one compare-and-set call from 9400 to
9300, one DA01 packet, one transmission, one completed packet, and zero
retries. After closing that write session, another separate client session
opened a fresh connection and read raw 9300 three times. The operator ran
Autofocus from the panel again and confirmed that it ended with a Z display of
9.3.

This establishes immediate write acceptance, fresh-connection readback,
display correlation, a completed panel-Autofocus endpoint change, and rollback
for only this controller and these two values. The serial receipts are
host-side write evidence, not controller acknowledgements. The typed method
itself still performs no readback.

No controller reset or power cycle was performed, so persistence is unknown.
No probe-trigger signal, contact event, contact coordinate, force, physical
distance, or independent Z position was captured. The panel invoked
Autofocus; the offline-only `D8 2E` candidate was not transmitted. This
fixture therefore does not validate contact probing, autofocus safety or
repeatability, arbitrary values, another controller or firmware, or UDP.
