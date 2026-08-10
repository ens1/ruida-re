# Fixture notice

The projects in this directory were created specifically for protocol research
and contain only synthetic geometry. The corresponding `.rd` files were
generated from those projects with the LightBurn version and Ruida profile
recorded in each JSON manifest.

No LightBurn executable, library, template, or third-party project is included.
LightBurn is a trademark of LightBurn Software LLC and is not affiliated with
this repository.

The fixture manifests, synthetic project inputs, and captured machine-file
outputs are distributed under the repository's MIT license to the extent they
are copyrightable. They consist primarily of test data and observed protocol
bytes. The LGPL LibLaserCut golden used as a comparison oracle is deliberately
not included; its pinned source and digest are documented in
`docs/sources.md`.

An `.rd` file is controller program data. These fixtures are for decoding and
comparison and should not be sent to a machine without independent review.
