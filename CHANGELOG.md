# Changelog

## Unreleased

- Add deterministic content-addressed collector bundles with complete source, dependency, runtime, configuration, and provenance manifests plus isolated replay and port 9398 health qualification.

All notable changes to SKCounter are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-08-23

### Added

- Added the HTTPS chiap04 aggregate collector with strict snapshot validation, CapAuth verification, trusted issuer binding, clock-skew and replay controls, append-only observations, idempotent acknowledgements, and a rebuildable projection outbox.
- Added the per-principal edge runner with a private durable outbox, short-lived `skcounter.report.submit` tokens, bounded retry, acknowledgement verification, sent retention, and status reporting.
- Added isolated service identity and collector TLS provisioning tools.
- Added a checksum-verified user-level Node.js bootstrap for eligible nodes whose system runtime is older than Node.js 20.
- Added a fail-closed edge launcher that selects the installed CapAuth Python runtime without assuming one virtual-environment layout across the fleet.
- Added canonical fleet node overrides so host aliases such as `chioc09` report under their approved CMDB identity without changing local hostname state.
- Added SKCapstone Fleet CronJob registration and node-owned health projection for package, backend, timer, result, outbox depth, and last acknowledgement.
- Added a loopback-only SKGateway aggregate adapter with a distinct service identity, `skcounter.gateway.submit` capability, and `gateway_observed` lane.
- Added hardened systemd collector and observable per-user timer units.
- Added a separately scheduled and reversible SKGateway adapter unit.
- Added collector health and Prometheus metrics endpoints.
- Added Node and Python tests for schema denial, identity binding, duplicate handling, replay, outage recovery, and private storage.

### Security

- Bound every report credential to one issuer, subject, node, and principal.
- Restricted central ingestion to an issuer-bound `harness_reported` or `gateway_observed` lane and the `models`, `daily`, `hourly`, and `time_metrics` privacy-safe views.
- Kept capability tokens out of files, command arguments, logs, and acknowledgements.
- Added TLS certificate verification over the encrypted tailnet path.

### Operations

- Added a 15 minute edge schedule with up to 2 minutes of randomized delay and a five minute first-run delay.
- Added private JSONL run evidence, critical alerts, deduplicated GTD failure capture, retention, health, metrics, and rollback procedures.
- Qualified eight native Linux harness principals, one WSL harness principal, the chiap04 collector, the chiap01 gateway adapter, Fleet health, and SKDashboard lane separation.

## [0.1.0] - 2026-08-23

### Added

- Added the provider-neutral `skcounter` CLI and replaceable backend contract.
- Added the exact `@tokscale/cli` 4.13.0 backend pin with MIT attribution.
- Added governed model, monthly, hourly, client, graph, time, pricing, and report commands.
- Added `skcounter.snapshot.v1` normalization for daily, model, hourly, and activity aggregates.
- Added private append-only local observation storage with exclusive writes.
- Added doctor and backend self-report commands.
- Added user-level install and rollback scripts.
- Added architecture, rollout, security, repository governance, and sk-standards deployment procedures.
- Added reusable documentation and CI integrity gates plus Node.js 20 and 22 test coverage.

### Security

- Blocked upstream social submission, autosubmit, login, account, credential, quota, synchronization, subprocess, summarization, TUI, and unknown command paths.
- Isolated backend configuration and redirected the upstream social API base to unused loopback.
- Excluded raw prompt and response content, source paths, workspace paths, raw session identifiers, tool arguments, and credentials from the normalized snapshot contract.
- Kept harness and gateway observations in separate measurement lanes.

### Verified

- Passed 25 Node.js tests covering facade dispatch, policy denials, provider isolation, snapshot normalization, private storage, and failures.
- Passed package dry-run, syntax, dependency audit, live chiap08 snapshot schema, installation, policy denial, and rollback checks.
- Passed the SKDashboard aggregate projection and full 235-test dashboard suite before integration.

[Unreleased]: https://github.com/smilinTux/skcounter/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/smilinTux/skcounter/releases/tag/v0.2.0
[0.1.0]: https://github.com/smilinTux/skcounter/releases/tag/v0.1.0
