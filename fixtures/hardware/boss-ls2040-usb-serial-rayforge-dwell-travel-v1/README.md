# Scoped Boss LS2040 C611-after-travel evidence

This directory preserves the exact three-stage Rayforge-generated dwell
package described by `manifest-v1.json`. After separate explicit approvals,
the control, one-delay sentinel, and four-delay payload were each transferred
once to one operator-identified Boss LS2040 over USB serial. Each host-side
summary reported one packet and zero retries. The transport provided no
controller or execution acknowledgement.

All three artifacts contain one planned 5 mm anchor at 15% requested power,
followed only by four `TravelTo` events. The control has no dwell. The sentinel
places one 100 ms `C6 11` `additional_delay` immediately after the first
post-anchor travel. The full payload places the same command immediately after
each of the four post-anchor travels. None contains a `C6 10`
`laser_interval` command, and none marks after the anchor.

The operator reported the control as, "I see one faint line, vertical, about
5mm". For the sentinel, the operator reported, "It looks like it did a
rectangle with pauses at the corner? Nothing other than a horizontal line,
about 5mm". For the full payload, the operator reported, "Yes, one faint line,
pauses at the corners".

This is narrowly scoped, qualitative evidence that the exact one- and
four-command C611-after-travel artifacts produced perceived pauses without a
visible post-anchor mark. There was no timing, dimensional, optical,
electrical, or motion metrology, so it does not establish that a pause lasted
100 ms. It does not validate mark-adjacent dwell, 200 ms dwell, C610/Pulse,
other timing values or positions, or profile-wide behavior. The earlier
zero-power 200 ms artifact remains quarantined and must not be sent.
