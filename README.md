# SKCounter

**SKCounter is the governed, provider-neutral AI usage accounting facade for the SK fleet.** It gives harness users and operators one stable command and aggregate contract while allowing the scanner backend to be replaced.
**Maturity tier:** T0 - N/A, no key material. **Version phase:** Incubating. **Current version:** 0.1.0.

The initial backend is [Tokscale](https://github.com/junhoyeo/tokscale), pinned to version 4.13.0. Tokscale remains separately attributed under its MIT license. SKCounter original code is Apache-2.0 licensed.

## Quickstart

Requirements are Node.js 20 or newer and npm.

```bash
npm ci --ignore-scripts
npm test
./scripts/install-user.sh
skcounter doctor
```

The installer places `skcounter` in `${SKCOUNTER_PREFIX:-$HOME/.local}/bin`. It runs as the harness user and never requires root access.

## Current release boundary

Version 0.1.0 ships:

- A stable `skcounter` command and replaceable backend interface.
- Local Codex, Claude, OpenCode, and other supported harness discovery.
- Governed local reports and normalized `skcounter.snapshot.v1` aggregate snapshots.
- A private append-only local observation store.
- A policy that blocks upstream submission, autosubmit, login, credential operations, remote synchronization, subprocess capture, LLM summarization, the upstream TUI, and unknown commands.
- A chiap08 user-level canary.
- A read-only SKDashboard projection under `Economy > AI Usage`.

Version 0.1.0 does not ship the network collector, CapAuth report capability, fleet timers, or SKGateway adapter. Those remain separately governed deployment tasks.

## Usage

```bash
skcounter
skcounter models --json
skcounter monthly --since 2026-08-01
skcounter clients --json
skcounter snapshot --since 2026-08-01
skcounter collect --since 2026-08-01
skcounter report --full
skcounter doctor
skcounter backend
```

`skcounter report` always adds `--no-summarize`. A policy denial exits with status 2. A collection or backend failure exits with status 1 or the backend status.

## Security boundary

Harness stores can contain sensitive prompts, responses, source code, paths, and credentials. SKCounter scans only stores owned by the current operating-system user. The normalized snapshot excludes raw prompt and response content, source paths, workspace paths, raw session identifiers, tool arguments, and credentials.

Normal local reports may fetch public pricing metadata. Provider subscription quota calls and upstream social reporting are blocked. Backend configuration is isolated under `~/.config/skcounter/providers/`.

## Cluster architecture

Install the passive package on every approved harness-capable node. Activate collection once per authorized harness principal, never once per machine as root. Edge collectors push normalized aggregates to the planned chiap04 collector. Central systems pull only version and health state.

SKGateway emits a separate `gateway_observed` measurement lane. The dashboard never sums it with `harness_reported` by default because one request can appear in both.

## Provider replacement

Provider-specific behavior lives in `src/providers/`. A replacement backend must pass the facade tests and emit `schemas/skcounter.snapshot.v1.schema.json`. Fleet callers never invoke Tokscale directly.

## Documentation

- [SOP.md](./SOP.md): canonical build, test, release, deployment, rollback, and troubleshooting procedures.
- [Architecture](./docs/ARCHITECTURE.md): fleet placement, data flow, privacy boundaries, and measurement semantics.
- [Rollout](./docs/ROLLOUT.md): staged central collector and fleet activation plan.
- [Security policy](./SECURITY.md): reporting, supported versions, and repository threat boundary.
- [Technical security model](./docs/SECURITY.md): local stores, CapAuth, collection, and supply-chain controls.
- [Snapshot schema](./schemas/skcounter.snapshot.v1.schema.json): provider-neutral aggregate contract.
- [Contributing](./CONTRIBUTING.md) and [changelog](./CHANGELOG.md).

## Standards posture

**Observability and scheduling:** version 0.1.0 ships no scheduled job. Any future collector timer must be wrapped with a run ledger, failure capture, alerting, and on-demand status in conformance with the [Observability and Scheduling Standard](https://github.com/smilinTux/sk-standards/blob/main/standards/OBSERVABILITY_AND_SCHEDULING_STANDARD.md).

**Service units:** N/A for version 0.1.0 because the repository ships no long-running systemd unit. Any future network collector must conform to the [Service Unit Standard](https://github.com/smilinTux/sk-standards/blob/main/standards/SERVICE_UNIT_STANDARD.md).

## Related projects / See also

- **Depends on:** [Tokscale](https://github.com/junhoyeo/tokscale), the pinned initial local scanner backend.
- **Used by:** [SKDashboard](https://github.com/smilinTux/skdashboard), which projects aggregate observations in the Economy workspace.
- **Integrates with:** [SKCapstone](https://github.com/smilinTux/skcapstone), which owns fleet coordination, health, and future deployment evidence.
- **Integrates with:** [SKGateway](https://github.com/smilinTux/skgateway), which will emit a separate gateway measurement lane.
- **Standards:** [sk-standards](https://github.com/smilinTux/sk-standards), the canonical repository documentation, testing, scheduling, and service standards.

## License

SKCounter original code is licensed under Apache-2.0. Tokscale remains licensed under MIT and is reproduced only with the attribution in [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).
