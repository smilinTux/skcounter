# SKCounter

SKCounter is the SK fleet's governed token usage accounting facade. It gives operators one stable `skcounter` command and one stable reporting contract while allowing the underlying scanner to be replaced.

The initial backend is [Tokscale](https://github.com/junhoyeo/tokscale), pinned to version 4.13.0. Tokscale remains separately attributed under its MIT license.

## Current behavior

- Scans local AI coding harness stores through the pinned backend.
- Presents the product as `skcounter` at the command and policy boundary.
- Defaults model, monthly, and hourly views to static output rather than the upstream TUI.
- Blocks social submission, autosubmit, upstream account operations, credential operations, remote synchronization, subprocess capture, LLM summarization, and unknown backend commands.
- Isolates backend settings beneath `~/.config/skcounter/providers/` so an existing Tokscale autosubmit setting is not inherited.
- Redirects the upstream social API to loopback as a second policy layer.

Normal local reports can fetch public pricing catalogs. The allowed reporting commands do not intentionally submit local session data. The fleet design adds an SK-owned aggregate reporting path rather than using Tokscale's public social service.

## Install from this checkout

Requirements are Node.js 20 or newer and npm.

```bash
npm ci
npm test
./scripts/install-user.sh
```

The installer places `skcounter` in `${SKCOUNTER_PREFIX:-$HOME/.local}/bin`. Add that directory to `PATH` if needed.

To remove the user-level command while preserving local configuration:

```bash
./scripts/uninstall-user.sh
```

## Usage

```bash
skcounter
skcounter models --json
skcounter monthly --since 2026-08-01
skcounter clients --json
skcounter report --full
skcounter doctor
skcounter backend
```

`skcounter report` always adds `--no-summarize`. A denied command exits with status 2. Backend failures exit with the backend status or status 1.

## Cluster design

Install the passive package on every cluster node that is allowed to host a coding harness. Activate collection once per authorized harness user, not once per machine as root. Nodes without local harness stores report healthy but empty discovery and consume negligible resources.

Usage data follows an edge-push design:

```text
local harness stores
        |
        v
per-user SKCounter collector
        |
        v
local durable outbox
        |
        v
CapAuth protected collector on chiap04
        |
        v
append-only observations and aggregate projections
```

SKGateway reports inference-observed usage through a separate adapter and measurement lane. Central projections must not blindly sum harness-reported and gateway-observed records because the same request can appear in both.

Central infrastructure pulls collector health and version state through SKCapstone Fleet. It does not pull session files over SSH, network mounts, or shared storage.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/ROLLOUT.md](docs/ROLLOUT.md), and [docs/SECURITY.md](docs/SECURITY.md).

## Provider replacement

Provider-specific resolution and execution live in `src/providers/`. A replacement backend must satisfy the facade tests and emit the normalized snapshot contract in `schemas/skcounter.snapshot.v1.schema.json`. Fleet callers do not invoke `tokscale` directly.

## Project status

Version 0.1.0 contains the local facade and Tokscale adapter. The signed push collector, central ingestion API, durable projection store, and fleet timers are architecture contracts for later governed tasks. They are not activated by this repository bootstrap.
