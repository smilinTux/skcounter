# Immutable collector bundle

The collector candidate is built from a clean, reviewed Git commit without reading live configuration, state, TLS keys, CapAuth private material, or observations.

```bash
python3 scripts/build-immutable-collector.py \
  --source-ref HEAD \
  --output /tmp/skcounter-bundles
```

The builder emits an uncompressed deterministic tar archive named by its SHA-256 digest and a canonical JSON checksum record. Two builds from the same commit and runtime inputs must have identical bytes. `MANIFEST.json` records:

- the canonical repository URL, exact commit, tree, source ref, and lockfile hash;
- every collector, CapAuth verifier, edge, unit, package, lockfile, runtime, shared-library, Python dependency, and configuration-template member;
- each member's path, mode, size, category, and SHA-256 digest;
- Node, Python, GnuPG, CapAuth, platform, and architecture versions.

The configuration member is an inert template. It contains no trusted issuer, usable path, certificate, key, token, or credential. Runtime executables and dynamically discovered libraries are captured as identity evidence. The archive is a source and artifact candidate, not an installer and not authorization to activate a service.

Run the focused qualification without touching the live collector:

```bash
python3 -m unittest test_py.test_immutable_collector_bundle -v
```

The qualification performs two clean builds, verifies all member hashes, starts an isolated TLS collector on loopback port 9398 with temporary configuration and state, reads `/healthz`, and proves duplicate replay reservation fails closed. It never submits to or mutates the live tailnet collector.
