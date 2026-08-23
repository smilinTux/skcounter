# SKCounter

**SKCounter is the governed, provider-neutral AI usage accounting facade for the SK fleet.** It gives harness users and operators one stable command and aggregate contract while allowing the scanner backend to be replaced.
**Maturity tier:** T0 - Classical on the CapAuth signing and HTTPS transport surfaces. **Version phase:** Incubating. **Current version:** 0.2.0.

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

Version 0.2.0 ships:

- A stable `skcounter` command and replaceable backend interface.
- Local Codex, Claude, OpenCode, and other supported harness discovery.
- Governed local reports and normalized `skcounter.snapshot.v1` aggregate snapshots.
- A private append-only local observation store.
- A durable edge outbox with signed HTTPS push and idempotent acknowledgements.
- A tailnet-only chiap04 collector with CapAuth issuer binding, replay control, append-only observations, projection outbox, health, metrics, and retention.
- Observable per-user systemd scheduling definitions with a 15 minute interval and up to 2 minutes of randomized delay.
- A policy that blocks upstream submission, autosubmit, login, credential operations, remote synchronization, subprocess capture, LLM summarization, the upstream TUI, and unknown commands.
- Per-user deployment and rollback tooling for eligible harness nodes.
- SKCapstone Fleet CronJob status for package, backend, timer, result, private outbox depth, and last acknowledgement.
- A loopback-only SKGateway aggregate adapter on `chiap01` using the dedicated `gateway_observed` lane and capability.
- A read-only SKDashboard projection under `Economy > AI Usage`.

SKCounter does not centrally pull or store raw harness sessions. The SKGateway adapter reads only the gateway's privacy-safe local token API and never receives prompts, responses, credentials, or request bodies.

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

Install the passive package on every approved harness-capable node. Activate collection once per authorized harness principal, never once per machine as root. Edge collectors push normalized aggregates to the chiap04 collector. Central systems pull only version and health state.

An enabled edge timer normally produces a central acknowledgement within 17 minutes: the 15 minute interval plus up to 2 minutes of randomized delay, followed by collection time. The first run is scheduled five minutes after activation. An on-demand run can report immediately with `systemctl --user start skcounter-edge.service`.

The `chiap01` SKGateway adapter emits a separate `gateway_observed` measurement lane every 15 minutes. The dashboard never sums it with `harness_reported` by default because one request can appear in both.

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

**Observability and Scheduling:** every scheduled edge run is wrapped with a private run ledger, failure capture, a critical SKCapstone alert, and on-demand edge status. It conforms to the [Observability and Scheduling Standard](https://github.com/smilinTux/sk-standards/blob/main/standards/OBSERVABILITY_AND_SCHEDULING_STANDARD.md). The current SKCapstone GTD CLI records the adapter as `manual` while preserving a stable `cron:` source reference; a control-plane compatibility card tracks adding `cron` as a first-class source enum.

**Service units:** the collector is a Tier B leaf service with bounded restart backoff and a limiter; the edge job is a one-shot timer. Paths are stable under `~/.local/lib/skcounter`, stale units are removed during rollback, and state retention is bounded. This conforms to the [Service Unit Standard](https://github.com/smilinTux/sk-standards/blob/main/standards/SERVICE_UNIT_STANDARD.md).

## Related projects / See also

- **Depends on:** [Tokscale](https://github.com/junhoyeo/tokscale), the pinned initial local scanner backend.
- **Used by:** [SKDashboard](https://github.com/smilinTux/skdashboard), which projects aggregate observations in the Economy workspace.
- **Integrates with:** [SKCapstone](https://github.com/smilinTux/skcapstone), which owns fleet coordination, health, and deployment evidence.
- **Integrates with:** [SKGateway](https://github.com/smilinTux/skgateway), which supplies privacy-safe local aggregates for the separate gateway measurement lane.
- **Standards:** [sk-standards](https://github.com/smilinTux/sk-standards), the canonical repository documentation, testing, scheduling, and service standards.

## License

SKCounter original code is licensed under Apache-2.0. Tokscale remains licensed under MIT and is reproduced only with the attribution in [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).
