# Contributing

Contributions are welcome, especially small changes with explicit protocol
evidence and focused tests.

## Development

Create an editable install and run the complete offline suite:

```sh
python3 -m pip install -e '.[test]'
python3 -m pytest -v
```

The automated suite must not open a controller connection or contact hardware.
Use injected transports and checked-in fixtures for tests.

## Evidence and safety

- Keep shape and semantic claims separate and cite the exact fixture, source,
  or hardware observation supporting each claim.
- Preserve unknown and disputed frames losslessly instead of guessing.
- Record the producer, version, generation method, and SHA-256 digest for new
  binary fixtures. Remove personal paths, device identifiers, addresses, and
  other private machine data before committing them.
- Do not include third-party source or fixtures unless their license permits
  redistribution and the provenance is documented.
- Never transmit a test job as part of a contribution workflow. Live hardware
  evidence requires separate operator review and approval outside the test
  suite. Unsafe executable evidence must be quarantined and marked do-not-send.

Use the private process in [SECURITY.md](SECURITY.md) for vulnerabilities or
behavior that could cause unintended laser emission or machine motion.
