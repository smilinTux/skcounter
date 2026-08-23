# Changelog

All notable changes to SKCounter are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/smilinTux/skcounter/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/smilinTux/skcounter/releases/tag/v0.1.0
