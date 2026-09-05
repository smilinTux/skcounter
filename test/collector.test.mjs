import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { collectSnapshot } from "../src/snapshot.mjs";
import { canonicalJson } from "../src/snapshot.mjs";
import { authorize, CollectorStore, RequestError, validateGatewayObservation, validateSnapshot } from "../services/collector.mjs";

function snapshot() {
  const graph = {
    meta: { dateRange: { start: "2026-08-22", end: "2026-08-23" } },
    contributions: [{
      date: "2026-08-23",
      totals: { tokens: 3, cost: 0.1, messages: 1 },
      tokenBreakdown: { input: 1, output: 2 },
      clients: [{ client: "codex", providerId: "openai", modelId: "gpt-test", tokens: { input: 1, output: 2 }, cost: 0.1, messages: 1 }],
    }],
    timeMetrics: {},
  };
  const hourly = { entries: [] };
  const backend = {
    id: "tokscale",
    version: "4.13.0",
    capture(args) { return { code: 0, stdout: JSON.stringify(args[0] === "graph" ? graph : hourly) }; },
  };
  return collectSnapshot({
    backend,
    options: { since: "2026-08-22", until: "2026-08-23" },
    now: new Date("2026-08-23T12:30:00Z"),
    nodeId: "chiap08",
    principalId: "skuser01",
    bucketTimezone: "America/Chicago",
  });
}

function config() {
  return {
    max_token_lifetime_seconds: 3600,
    trusted_issuers: {
      ABCDEF: {
        enabled: true,
        node_id: "chiap08",
        principal_id: "skuser01",
        subject: "skcounter:chiap08:skuser01",
      },
    },
  };
}

function claims() {
  return {
    issuer: "abcdef",
    subject: "skcounter:chiap08:skuser01",
    audience: "skcounter",
    capabilities: ["skcounter.report.submit"],
    issued_at: "2026-08-23T12:00:00+00:00",
    expires_at: "2026-08-23T13:00:00+00:00",
    metadata: { node_id: "chiap08", principal_id: "skuser01" },
  };
}

test("collector validates the canonical privacy-safe snapshot", () => {
  assert.equal(validateSnapshot(snapshot()).schema_version, "skcounter.snapshot.v1");
});

test("collector keeps gateway observations on their dedicated scope and lane", () => {
  const gateway = snapshot();
  gateway.measurement_lane = "gateway_observed";
  gateway.principal_id = "skgateway";
  gateway.collector.backend = "skgateway";
  gateway.collector.backend_version = "0.1.0";
  const unsigned = structuredClone(gateway);
  delete unsigned.idempotency_key;
  delete unsigned.payload_hash;
  const hash = createHash("sha256").update(canonicalJson(unsigned)).digest("hex");
  gateway.idempotency_key = hash;
  gateway.payload_hash = hash;
  assert.equal(validateSnapshot(gateway).measurement_lane, "gateway_observed");

  const gatewayConfig = config();
  gatewayConfig.trusted_issuers.ABCDEF = {
    enabled: true,
    node_id: "chiap08",
    principal_id: "skgateway",
    subject: "skcounter:chiap08:skgateway",
    measurement_lane: "gateway_observed",
    scope: "skcounter.gateway.submit",
  };
  const gatewayClaims = claims();
  gatewayClaims.subject = "skcounter:chiap08:skgateway";
  gatewayClaims.capabilities = ["skcounter.gateway.submit"];
  gatewayClaims.metadata.principal_id = "skgateway";
  assert.doesNotThrow(() => authorize(gateway, gatewayClaims, gatewayConfig));

  const wrongLane = structuredClone(gateway);
  wrongLane.measurement_lane = "harness_reported";
  assert.throws(() => authorize(wrongLane, gatewayClaims, gatewayConfig), (error) => error.code === "lane_denied");
});

test("collector rejects unknown fields, unsupported views, and hash tampering", () => {
  const unknown = snapshot();
  unknown.prompt = "must never leave the edge";
  assert.throws(() => validateSnapshot(unknown), (error) => error instanceof RequestError && error.code === "invalid_schema");

  const unsupported = snapshot();
  unsupported.aggregates[0].view = "sessions";
  assert.throws(() => validateSnapshot(unsupported), (error) => error.code === "view_not_allowed");

  const tampered = snapshot();
  tampered.aggregates[0].tokens.total = 999;
  assert.throws(() => validateSnapshot(tampered), (error) => error.code === "payload_hash_mismatch");
});

test("collector binds CapAuth issuer, scope, subject, token, node, and principal", () => {
  assert.doesNotThrow(() => authorize(snapshot(), claims(), config()));
  for (const mutation of [
    (value) => { value.issuer = "untrusted"; },
    (value) => { value.subject = "other"; },
    (value) => { value.capabilities = ["*"]; },
    (value) => { value.metadata.node_id = "chiap04"; },
  ]) {
    const changed = structuredClone(claims());
    mutation(changed);
    assert.throws(() => authorize(snapshot(), changed, config()), RequestError);
  }
  const expiredLifetime = claims();
  expiredLifetime.expires_at = "2026-08-23T14:00:00+00:00";
  assert.throws(() => authorize(snapshot(), expiredLifetime, config()), (error) => error.code === "token_lifetime_denied");
});

test("collector appends once, acknowledges duplicates, and blocks request replay", () => {
  const root = mkdtempSync(join(tmpdir(), "skcounter-collector-"));
  const store = new CollectorStore(root, () => new Date("2026-08-23T12:31:00Z"));
  const value = snapshot();
  const first = store.accept(value);
  const duplicate = store.accept(value);
  assert.equal(first.duplicate, false);
  assert.equal(duplicate.duplicate, true);
  assert.equal(store.readMetrics().accepted, 1);
  assert.equal(store.readMetrics().duplicated, 1);
  const observation = join(root, "observations", "chiap08", "skuser01", `${value.idempotency_key}.json`);
  const projection = join(root, "projection-outbox", `${value.idempotency_key}.json`);
  assert.equal(JSON.parse(readFileSync(observation, "utf8")).payload_hash, value.payload_hash);
  assert.equal(statSync(observation).mode & 0o777, 0o600);
  assert.equal(statSync(projection).mode & 0o777, 0o600);
  store.reserveReplay("11111111111111111111111111111111");
  assert.throws(() => store.reserveReplay("11111111111111111111111111111111"), /EEXIST/);
});

function observationV2() {
  const unsigned = {
    schema_version: "skcounter.gateway_observation.v2",
    measurement_lane: "gateway_observed",
    node_id: "chiap08",
    principal_id: "skgateway",
    collector: { product: "skcounter", facade_version: "0.2.0", backend: "skgateway", backend_version: "0.1.0" },
    observed_at: "2026-09-05T12:30:00Z",
    bucket_timezone: "UTC",
    window: { start: "2026-09-05T00:00:00Z", end: "2026-09-05T12:30:00Z" },
    source_state_digest: "a".repeat(64),
    facts: {
      gateway: { uptime_seconds: 1200, backend_health: { "chiap08-qwen38": { status: "ok", errorRate: 0 } } },
      requests: { total: 10, active_concurrency: 2, error_count: 1, recent_requests_5m: 30, recent_errors_5m: 0, rate_5m_per_second: 0.1 },
      latency_ms: { "chiap08-qwen38/qwen3.8": { p50: 120, p95: 400, p99: 900, mean: 200, count: 25 } },
      queue: { wait_ms_percentiles: { unavailable: "gateway_surface_does_not_expose_queue_telemetry" }, admission_outcomes: { unavailable: "gateway_surface_does_not_expose_queue_telemetry" } },
      rate_limits: { http_429_count: { unavailable: "gateway_surface_does_not_expose_rate_limit_counts" } },
      tokens: { input: 100, output: 50, throughput_5m_per_second: 0.5 },
      generation: { throughput_tokens_per_second: { unavailable: "gateway_stats_surface_does_not_expose_generation_throughput" } },
      cost: { total_usd: 0.25, unpriced_requests: 0, truth: "actual" },
      breakdowns: { models: ["qwen3.8"], providers: ["local"], nodes: ["chiap08-qwen38"], clients: ["atlas"], apps: { unavailable: "gateway_surface_does_not_expose_application_attribution" }, rails: ["local"] },
      daily_token_rows: [{ bucket: "2026-09-05", input_tokens: 100, output_tokens: 50, cache_read_tokens: 0, cache_write_tokens: 0, request_count: 10, model: "qwen3.8", backend: "chiap08-qwen38", agent: "atlas" }],
      events: { count: 2, by_type: { info: 2 } },
      activity: { count: 1, by_type: {} },
    },
  };
  const hash = createHash("sha256").update(canonicalJson(unsigned)).digest("hex");
  return { ...unsigned, idempotency_key: hash, payload_hash: hash };
}

test("collector accepts the bounded gateway observation v2 contract", () => {
  const observation = observationV2();
  assert.equal(validateGatewayObservation(observation).schema_version, "skcounter.gateway_observation.v2");
});

test("collector rejects v2 observations off the gateway lane or with prohibited fields", () => {
  const wrongLane = observationV2();
  wrongLane.measurement_lane = "harness_reported";
  wrongLane.payload_hash = "0".repeat(64);
  wrongLane.idempotency_key = "0".repeat(64);
  assert.throws(() => validateGatewayObservation(wrongLane), (error) => error.code === "lane_not_allowed");

  const prohibited = observationV2();
  prohibited.facts.requests.prompt = "must never leave the edge";
  assert.throws(() => validateGatewayObservation(prohibited), (error) => error.code === "prohibited_field");

  const tampered = observationV2();
  tampered.facts.requests.total = 999;
  assert.throws(() => validateGatewayObservation(tampered), (error) => error.code === "payload_hash_mismatch");

  const deep = observationV2();
  let node = deep.facts;
  for (let i = 0; i < 10; i += 1) node = node.requests = { nested: node.requests };
  assert.throws(() => validateGatewayObservation(deep), (error) => error.code === "invalid_schema");
});
