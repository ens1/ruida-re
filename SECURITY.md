# Security policy

`ruida-re` can generate and transmit commands to hazardous machinery. Treat a
defect that could cause unintended laser emission, motion, Z movement, air
assist state, power, or repeated delivery as a safety-sensitive security
issue.

## Supported versions

This project is pre-alpha. Security fixes are made on the latest default
branch and, when practical, the most recent release. Older commits and
research profiles are not supported release lines.

## Private reporting

Use **Report a vulnerability** on the repository's **Security** tab. This
creates a private GitHub security advisory that only the reporter and invited
maintainers can see. GitHub makes private vulnerability reporting available
to public repositories. Maintainers should enable it immediately after
changing this repository's visibility and before announcing the public
release.

If that button is unavailable, do not disclose the vulnerability or an
executable proof of concept in a public issue. Contact a maintainer through an
already established private channel and ask to open a private advisory.

Include the affected version or commit, controller and transport scope,
expected and observed behavior, and the smallest offline reproducer possible.
Provide hashes for binary fixtures. Clearly label any machine-executable file
and whether it has ever been transmitted.

## Safe disclosure

- Do not test a report on live machinery unless the operator has reviewed and
  explicitly approved the exact payload and is present with an emergency stop.
- Prefer an offline compiler, decoder, fixture, or injected transport
  reproducer. Automated tests must not contact hardware.
- Do not attach credentials, access tokens, private keys, private network
  addresses, device paths or serial numbers, personal paths, or unsanitized
  controller captures. This project does not need or retain secrets. Rotate
  any secret that was exposed before reporting it.
- Do not publish executable payloads that could produce unsafe behavior while
  a report is under review. Unsafe evidence retained in the repository must be
  unmistakably quarantined and documented as do-not-send.

The maintainers will validate the scope, coordinate a fix and release, and
credit the reporter if requested. Public disclosure should wait until a fix or
documented mitigation is available.

General protocol uncertainty, feature requests, and documentation corrections
that do not create a safety or security risk may use the public issue tracker.
