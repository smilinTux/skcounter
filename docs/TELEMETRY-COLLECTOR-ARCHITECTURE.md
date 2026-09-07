# SK telemetry and evidence collector architecture

Status: proposed

Card: `493f07b8`

Initial central host: `chiap08`

## Decision

Build the collection plane from standard observability components plus one narrow SK evidence service.

- OpenTelemetry Collector owns receipt, processing, batching, and export of metrics, traces, and logs.
- Prometheus-compatible storage owns numeric time series, retention, recording rules, alerts, and PromQL queries.
- The SK evidence service owns authenticated records that require CapAuth identity, immutable provenance, idempotency, acknowledgement, or workflow semantics.
- SKDashboard owns visualization, reports, AI-assisted interpretation, and action previews. It does not own ingestion or primary storage.
- Source products retain their own domain models. They publish through adapters and do not become subdomains of the collector.

This is not a custom replacement for OpenTelemetry or Prometheus. SKCounter is the first evidence producer, not the boundary of the collection plane.

## Goals and exclusions

The plane accepts fleet telemetry from SKCounter, SKGateway, model runtimes, agents, workers, hosts, caches, and CardStore. It continues collecting through network or central outages, preserves source identity and lineage, scales without changing producer contracts, and exposes explicit freshness and coverage.

It does not create a new time-series database or query language. It does not collect raw prompts, responses, transcripts, credentials, tool arguments, or arbitrary filesystem content. It does not create a universal product schema. It never authorizes workflow or external action.

## Bounded contexts

| Context | Owns | Does not own |
| --- | --- | --- |
| Source product | Native state, meaning, and instrumentation | Central retention or cross-product reports |
| Source adapter | Privacy-safe mapping to OTLP or an evidence envelope | Source truth, policy, presentation |
| OpenTelemetry pipeline | Receivers, processors, batching, filtering, enrichment, export | CapAuth, workflow state, approval |
| Metrics platform | Time series, histograms, PromQL, recording rules, alerts | Immutable evidence or workflow provenance |
| SK evidence service | CapAuth verification, schema allowlists, provenance, idempotency, durable acceptance, acknowledgements | General metrics storage or source business logic |
| Projection service | Rebuildable read models and report snapshots | Primary evidence or authorization |
| SKDashboard | Visualization, filters, AI briefs, drill-down, action previews | Primary ingestion, storage, silent remediation |
| CardStore and workflow products | Cards, transitions, approvals, outcomes, verified effects | Telemetry transport or metric calculation |
| CapAuth and policy gateway | Identity, capabilities, tenant and purpose policy, revocation | Telemetry processing or presentation |

No context writes another context's primary store. Integration occurs through versioned contracts.

## Data classes

### Numeric telemetry

Use OTLP metrics and Prometheus-compatible storage for traffic, errors, latency, time to first token, queue delay, saturation, tokens, cache hits, KV-cache occupancy, concurrency, and resource use. Prefer histograms for aggregatable distributions.

Labels are bounded. Request IDs, session IDs, card IDs, paths, prompt hashes, and user strings belong in traces or evidence, never metric labels.

### Traces and logs

Use OTLP traces for request paths across SKDashboard, SKGateway, model backends, agents, and tools. Use the OpenTelemetry log data model for structured operational events.

### Governed evidence

Use an SK evidence envelope when a record must prove who reported what, from which source revision, under which policy, and whether it was accepted. Examples include usage snapshots, card lifecycle evidence, run completion evidence, approvals, accepted outcomes, verified effects, migration receipts, and diagnostic submissions.

The envelope contains a version, event ID and type, schema ID and version, source product and revision, producer node and principal, tenant and scope when applicable, occurred and observed times, measurement lane, payload hash, idempotency key, optional trace ID, classification, retention class, non-secret capability proof reference, signature, and payload.

Unknown event types and schema versions fail closed for projection. Optional quarantine may retain bounded encrypted bytes, but quarantined data is not reportable evidence.

## Producer contracts

| Producer | Standard telemetry | Governed evidence |
| --- | --- | --- |
| SKCounter | Collection health, outbox depth, duration | Signed aggregate usage snapshots |
| SKGateway | Requests, latency, TTFT, queue, retries, fallback, tokens, cache, cost | Route decisions and linked outcomes |
| Model runtime | Generation latency, throughput, queue, KV cache, memory, errors | Model and revision readiness attestations |
| Fleet worker | State counts, duration, utilization, errors | Claimed card, result, artifact digest, acknowledgement |
| Agent runtime | Availability, workload, model use | Versioned profile and governed run records |
| Host adapter | CPU, memory, disk, network, accelerator | Optional signed inventory attestation |
| CardStore | Projection lag and transition counters | Append-only events, approvals, outcomes, effects |

Adapters remove prohibited fields locally. Central filtering is defense in depth, not the privacy boundary.

## Topologies

### Local

One host runs adapters, OpenTelemetry Collector, metric storage, evidence service, projections, and SKDashboard. Producers still use authenticated contracts over loopback so local mode does not create a second ingestion path.

### Remote

Fleet nodes run adapters and an OpenTelemetry agent or direct OTLP exporter. Governed evidence uses a user-scoped durable outbox and the remote evidence endpoint. Central services initially run on `chiap08`.

### Scaled

Stable logical service names front multiple stateless telemetry gateways and evidence ingress instances. Evidence uses a shared idempotency and append-only storage boundary. Partitioning uses tenant plus stable producer identity. Edge configuration does not change when capacity scales.

## Offline delivery

Numeric telemetry uses an OpenTelemetry sending queue and persistent storage where loss tolerance requires it. Governed evidence enters a mode `0700` outbox before transmission:

```text
created -> locally_validated -> queued -> submitted -> accepted -> projected
```

Only an authenticated acknowledgement with matching event ID, payload hash, and idempotency key marks evidence accepted. Retries use bounded exponential backoff with jitter. Duplicate submissions return the original acceptance identity. Unacknowledged records are never silently deleted.

Backpressure drops or delays low-priority telemetry before governed evidence. Dashboard health separates queue depth, oldest queued age, dropped telemetry, rejected evidence, and projection lag.

## Projection and AI contract

Projections are deterministic, versioned, atomic, and rebuildable. Each source reports its measurement lane, observation and projection times, expected and reporting populations, freshness and age, schema and projection revisions, completeness, confidence, and explicit no-data reason.

The system preserves these distinctions:

```text
observation != reported claim != model inference != human decision
human decision != accepted outcome != verified external effect
```

AI may summarize cited evidence, identify anomalies, propose next steps, and prepare an action preview. It cannot alter source data, approve work, or execute an action.

## Security and policy

- CapAuth submit capabilities bind producer, node, tenant, purpose, event type, lane, and expiry.
- Transport uses mTLS or an equivalently authenticated private route. CA changes require explicit trust distribution and fingerprint verification.
- Identity, policy, schema, classification, tenant, and source-rights failures fail closed.
- Quotas cover bytes, events, requests, replay, label cardinality, and query cost.
- Payload size, nesting, attributes, strings, clock skew, compression ratio, and batch size are bounded before acceptance.
- Secrets and raw capability tokens are excluded from telemetry, evidence, logs, dead letters, and committed configuration.
- Evidence is append-only. Corrections supersede records without rewriting history.
- Metrics and evidence have independent retention, legal hold, and deletion policies.
- Administration and repair require capabilities separate from submission.

## Cardinality rules

Begin with service, environment, node class, model route, backend, status class, cache status, and bounded agent role. Add labels only for a demonstrated query. Export timestamps instead of continuously updated age gauges. Prefer native histograms where the complete path supports them, otherwise use reviewed classic buckets. Never average precomputed quantiles across workers.

Recording rules precompute expensive dashboard aggregates. Query and storage budgets are themselves observable.

## Setup and doctor boundary

The future operator interface declares topology instead of embedding hosts in scripts:

```text
sktelemetry setup local
sktelemetry setup edge --endpoint <logical-url> --ca-file <trusted-ca>
sktelemetry setup central
sktelemetry doctor --json
sktelemetry doctor --repair
sktelemetry doctor --submit-test
```

Names remain provisional until an implementation card decides whether commands temporarily live in SKCounter or a dedicated package.

Doctor checks artifact digests, topology, configuration, endpoint resolution, CA fingerprint and expiry, CapAuth identity and scope, service state, persistent queues, ownership and modes, central health, diagnostic acknowledgement, projection freshness, cardinality budgets, and clock skew.

Repair is idempotent and limited to deterministic local drift. It may reinstall reviewed units, restore safe permissions, rebuild projections, and install an explicitly supplied trusted CA. It may not replace identity, rotate trust, discard queues, overwrite evidence, or change routes implicitly.

## Failure semantics

| Failure | Required behavior |
| --- | --- |
| Source unavailable | Preserve last observation, mark stale, show source error |
| Central endpoint unavailable | Queue locally, back off, show oldest queued age |
| TLS or identity failure | Fail closed, retain queue, show exact repair evidence |
| Unsupported schema | Reject or quarantine, never project |
| Duplicate evidence | Return stable acknowledgement, never duplicate totals |
| Metrics backend unavailable | Evidence intake remains independent |
| Evidence store unavailable | Do not acknowledge; producers retain records |
| Projection failure | Keep evidence intact; mark projection stale |
| AI unavailable | Keep deterministic reports and show AI unavailable |

## Placement and migration

`chiap08` is the initial central host because it serves the current dashboard. Clients use a logical endpoint and pinned trust, not a hard-coded host address in product contracts. `chiap04` remains a temporary rollback collector until every reachable edge has acknowledged through `chiap08` and any unavailable node is migrated or explicitly excepted.

Migration proceeds by restoring verified state on `chiap08`, starting central services without client changes, canarying one producer through acknowledgement and projection, rotating clients to the logical endpoint, and retaining bounded rollback. Rollback restores the prior logical endpoint and trusted CA. Durable outboxes replay without deleting accepted state.

## Delivery gates

Architecture acceptance does not authorize implementation or deployment. Create separate child cards for:

1. Contracts and threat model.
2. Standard telemetry pipeline and metric storage qualification.
3. Evidence ingress, storage, acknowledgement, and projections.
4. Setup, doctor, migration, and rollback tooling.
5. Producer adapters and SKDashboard read models.

Each implementation requires unit tests, isolated end-to-end tests, load and cardinality tests, offline replay, duplicate replay, malformed payload, tenant isolation, TLS rotation, disaster recovery, and rollback evidence. Production activation requires an independently reviewed immutable artifact and a human-controlled release card.

## Standards basis

- OpenTelemetry Collector component model: <https://opentelemetry.io/docs/collector/components/>
- OpenTelemetry logs and events data model: <https://opentelemetry.io/docs/specs/otel/logs/data-model/>
- Prometheus instrumentation guidance: <https://prometheus.io/docs/practices/instrumentation/>
- Prometheus histogram guidance: <https://prometheus.io/docs/practices/histograms/>

## Superseded decision

The prior `chiap04` preferred-collector placement is superseded. Existing SKCounter privacy rules, lane separation, append-only observations, and provider-neutral adapter boundaries remain in force.
