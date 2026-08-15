# Boss LS2040 address-512 zero-value capture

This fixture records one approved, read-only USB-serial exchange captured on
2026-08-15. Semantic U14 address `0x0200` encoded as groups `04 00`, so the
logical request was `DA 00 04 00`. The controller returned a correlated
nine-byte `DA 01` reply with numeric value zero.

The capture proves only the exact exchange and returned value on that setup.
The address label and three flag labels used by the experimental typed API
come from the pinned MeerK40t implementation. This capture does not prove that
the address is machine status, that zero means idle, or that any reported flag
tracks active work or completion. No physical-effect observation accompanied
the query, and UDP was not tested.
