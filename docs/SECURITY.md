# Security model

## Protected local material

Harness stores can contain prompts, responses, tool calls, file paths, repository names, source code, credentials accidentally included in conversations, and protected Matter content. Treat every store as sensitive even when the requested output is only a token count.

## Local process boundary

Run SKCounter with the same operating-system identity that owns the harness store. Do not grant a central service SSH access, shared-home access, or root traversal to collect usage. Backend configuration is isolated beneath the SKCounter configuration root so existing upstream autosubmit state is not inherited.

Version 0.2.0 blocks known Tokscale commands that can submit usage, manage upstream accounts, manipulate credentials, perform remote synchronization, call subscription quota APIs, execute captured subprocesses, or launch the upstream TUI. It also redirects the upstream social API to loopback and fails closed on unknown top-level commands.

The pinned backend may fetch public pricing metadata during allowed reports. This is not a session-data submission path, but production egress policy should still constrain destinations at the host network layer.

## Report capability

A reporting identity receives only the capability to create a bounded SKCounter snapshot for its exact node and principal. It cannot query other nodes, retrieve observations, change policy, dispatch model requests, or upload raw artifacts.

The collector verifies:

- TLS transport and expected tailnet route.
- CapAuth issuer, subject, node, principal, purpose, action, expiry, and revocation.
- Supported schema version.
- Canonical payload hash and signature.
- Maximum payload and aggregate counts.
- Timestamp window and clock-skew limit.
- Idempotency key and replay state.
- Allowed measurement lane.
- Absence of prohibited raw-data fields.

The deployed collector additionally restricts v1 to `models`, `daily`, `hourly`, and `time_metrics`. It denies the schema's optional workspace, session, task, and agent drilldowns until a separate privacy review authorizes their exact local derivation.

## Storage

Local outboxes use user-only permissions. Central observations are append-only. Corrections create superseding observations and never rewrite accepted history. Derived projections use an outbox and can be rebuilt from observations.

Do not store prompts, responses, tool input, tool output, session identifiers, workspace paths, source file paths, raw capability tokens, API keys, cookies, or OAuth material.

The edge service key is a classical Ed25519 signing-only key in an isolated mode `0700` GnuPG home. It is noninteractive so the per-user timer can mint one-hour tokens. The collector stores only its public certificate and an exact issuer binding. See [crypto architecture](./crypto-architecture.md) for rotation and current T0 limitations.

## Supply chain

- Pin the facade backend to an exact version.
- Commit npm integrity metadata in `package-lock.json`.
- Run tests and `npm audit --omit=dev` before rollout.
- Record upstream repository revision, release, package integrity, and license.
- Promote version updates through a canary before fleet rollout.
- Keep the upstream license and third-party notice in distributions.
