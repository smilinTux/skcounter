# Immutable collector bundle

The collector candidate is built from a clean, reviewed Git commit without reading live configuration, state, TLS keys, CapAuth private material, or observations.

```bash
python3 scripts/build-immutable-collector.py \
  --source-ref HEAD \
  --output /tmp/skcounter-bundles
```

The builder emits an uncompressed deterministic tar archive named by its SHA-256 digest, a canonical JSON checksum record, and a closed `skfleet-service-runtime/v1` sidecar accepted by the fleet runtime contract. Two builds from the same commit and runtime inputs must have identical bytes. `MANIFEST.json` records:

- the canonical repository URL, exact commit, tree, source ref, and lockfile hash;
- every collector, CapAuth verifier, edge, unit, package, lockfile, runtime, shared-library, Python dependency, and configuration-template member;
- each member's path, mode, size, category, and SHA-256 digest;
- private Node, Python, GnuPG, Python standard-library, CapAuth dependency, native-loader, and shared-library bytes plus their versions, platform, and architecture.

The configuration member is an inert template. It contains no trusted issuer, certificate, key, token, or credential. The sealed collector unit executes the fleet runtime contract's atomic `~/.local/lib/skcounter-collector/current` link rather than a checkout or unversioned copied directory. `scripts/promote-immutable-collector.py` verifies the outer artifact digest, rejects unsafe archive entries, extracts into the content-addressed `skcounter-collector/versions/<sha256>` directory, verifies the closed inner manifest, and atomically updates that exact link. A failed qualification callback restores the exact prior pointer. Relative entrypoints invoke only the bundled loader, libraries, executables, standard library, and package closure. Edge source bytes remain provenance members and are not presented as a separately deployable runtime. Promotion is not authorization to activate a service.

Run the focused qualification without touching the live collector:

```bash
python3 -m unittest test_py.test_immutable_collector_bundle -v
```

The qualification performs two clean builds, verifies all member hashes and the closed fleet manifest, extracts the artifact, proves its Python path excludes user-site state, runs the bundled verifier fail-closed, starts the extracted TLS collector on loopback port 9398, reads `/healthz`, and proves duplicate replay reservation fails closed through the bundled Node runtime. It never imports the source checkout or submits to or mutates the live tailnet collector.
