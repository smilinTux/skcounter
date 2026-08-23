# SKCounter cluster architecture

## Decision

Use edge collection with aggregate push. Install SKCounter on every harness-capable cluster node, run it once per authorized harness principal, and push normalized snapshots to one governed collector. Use central pull only for health, version, and rollout status.

Do not centrally pull raw Codex, Claude, OpenCode, or other harness stores. A pull design would require broad remote filesystem credentials, fail when nodes are offline, cross user isolation boundaries, and concentrate raw transcript access in one service.

## Placement

| Node role | Package | Active local collector | Central role |
| --- | --- | --- | --- |
| Harness-capable Linux or WSL node | Install | One timer per authorized harness user | None |
| `chiap08` development coordinator and model host | Install | Yes for local harness users | None |
| `chiap04` operator control plane | Install | Yes if harness stores exist | Preferred central collector |
| `chiap01` application worker and SKGateway host | Install | Only for local harness users | SKGateway adapter only, not the collector API |
| Pure model-serving node | Passive install is acceptable | No unless a harness later runs there | Model runtime telemetry stays separate |
| Native Windows workstation | Install native package when a harness runs in Windows | Per Windows user | None |
| WSL workstation | Install inside each harness-owning distribution | Per Linux user | None |

Installing a passive package fleet-wide prevents a coverage gap when a Codex harness moves. Activation remains identity-scoped. A root service must never scan all home directories.

## Data flow

1. A user-level timer discovers only supported session roots in its own home.
2. The selected backend parses sessions locally.
3. The SKCounter adapter removes source paths, workspace paths, session identifiers, prompt text, response text, tool arguments, and credentials.
4. The adapter creates a canonical snapshot using `skcounter.snapshot.v1`.
5. The node appends the canonical payload to a local outbox before transmission.
6. The node mints a one-hour CapAuth token with only `skcounter.report.submit` authority.
7. The node pushes over a TLS tailnet route to the central collector.
8. The collector verifies capability, node identity, schema, payload hash, size, time window, and replay state.
9. The collector appends the observation and acknowledges its idempotency key.
10. The node removes an acknowledged outbox entry according to retention policy.
11. Central projections select the latest valid snapshot for each measurement key.

## Snapshot semantics

Tokscale primarily produces cumulative views over time windows. Treat edge reports as observations, not blindly additive events.

Every aggregate row declares its source view, such as `models`, `daily`, `hourly`, `agents`, `workspaces`, `sessions`, `tasks`, or `time_metrics`. Views can describe overlapping usage and are never combined with one another. The logical observation key within a view is:

```text
measurement_lane + view + node_id + principal_id + bucket_start + client + provider + model + privacy-safe detail keys
```

Store every accepted observation append-only. The current projection selects the latest observation for a logical key. The idempotency key is the SHA-256 digest of the canonical unsigned payload. Retries are safe and do not duplicate totals.

Each cost value records whether it is estimated and the exact pricing revision. Token counts remain usable when pricing cannot be resolved.

Optional session and workspace drilldowns use node-scoped HMAC keys, never raw identifiers. A workspace label is emitted only from an operator-approved alias map. Task labels require a separately approved local classification path and must not be copied from prompt content by default.

## Measurement lanes

`harness_reported` is derived from local coding-harness session stores. `gateway_observed` is derived from SKGateway request and provider accounting. A third lane can be added for provider billing reconciliation.

Keep lanes separately queryable. The same Codex harness request can appear at the harness and gateway. Cross-lane totals require a documented correlation rule and must never be summed by default.

## SKDashboard Economy workspace

SKDashboard presents SKCounter under `Economy > AI Usage`, beside `Economy > Autopilot` and `Economy > Joule`.

- AI Usage shows tokens, messages, activity, cache efficiency, provider-estimated cost, models, providers, clients, nodes, agents, time buckets, collector health, versions, and freshness.
- Autopilot shows governed autonomous-work cost and settlement activity.
- Joule shows balances, supply, levels, and the sovereign value ledger.

These are related views, not interchangeable units. SKCounter does not mint or spend Joules. Any future rule that allocates USD or token usage to a Joule budget must be versioned, auditable, and shown as an explicit derived projection.

Tokscale subscription quota views require provider account calls and credentials. They are not part of the local scanner lane. If added later, they use separate service-scoped provider connectors with their own approval and freshness state.

## Central collector

Place the first central collector on `chiap04`, which is designated as the operator control plane. Keep it separate from SKGateway so observability ingestion cannot affect inference routing. A later high-availability task can move the collector behind a fleet service identity without changing node clients.

The collector requires:

- A narrow tailnet listener with TLS.
- CapAuth verification and fail-closed policy decisions.
- A payload size limit and supported-schema allowlist.
- Append-only observation storage.
- An idempotency index.
- An outbox for derived projections.
- Retention and legal-hold policy appropriate to operational metadata.
- Metrics for accepted, duplicated, rejected, delayed, and invalid reports.
- No endpoint that accepts raw transcript uploads.

## Scheduling and offline operation

Use a user-level systemd timer on Linux and WSL, with a 15 minute interval and up to two minutes of randomized delay. Use a per-user scheduled task on native Windows. The edge collector should finish quickly and exit rather than remain resident.

When the central service is unavailable, retain signed payloads in a mode 0700 user-state outbox. Retry with bounded exponential backoff. Start with a seven-day operational retention target, then confirm it through a dedicated data-retention decision before production activation.

## Fleet control

SKCapstone Fleet owns package version, eligibility labels, timer presence, last collection result, outbox depth, and last acknowledged report time. Fleet health polling does not grant access to session content.

Recommended eligibility labels are:

```text
skcounter.package=present
skcounter.harness-capable=true
skcounter.collector=per-user
skcounter.schema=v1
```

## Provider replacement

The facade contract is provider-neutral. A provider adapter must implement local discovery, governed execution, normalization, version reporting, and deterministic failure. A replacement may use a future native SK parser, another open-source scanner, or direct harness event hooks without changing timers or the central ingestion schema.
