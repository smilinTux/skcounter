#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { chmodSync, closeSync, existsSync, mkdirSync, openSync, readFileSync, readdirSync, renameSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { createServer } from "node:https";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { canonicalJson } from "../src/snapshot.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const HEX64 = /^[a-f0-9]{64}$/;
const SAFE_ID = /^[A-Za-z0-9._@-]{1,128}$/;
const REQUEST_ID = /^[a-f0-9-]{32,36}$/;
const SNAPSHOT_KEYS = new Set([
  "schema_version", "idempotency_key", "measurement_lane", "node_id", "principal_id",
  "collector", "observed_at", "bucket_timezone", "window", "source_state_digest",
  "aggregates", "payload_hash",
]);
const COLLECTOR_KEYS = new Set(["product", "facade_version", "backend", "backend_version"]);
const WINDOW_KEYS = new Set(["start", "end"]);
const AGGREGATE_KEYS = new Set([
  "view", "bucket_start", "client", "provider", "model", "agent", "workspace_key",
  "workspace_label", "session_key", "task_label", "tokens", "message_count", "cost",
  "performance", "activity",
]);
const TOKEN_KEYS = new Set(["input", "output", "cache_read", "cache_write", "reasoning", "total"]);
const COST_KEYS = new Set(["amount", "currency", "estimated", "pricing_revision"]);
const ACTIVITY_KEYS = new Set(["active_seconds", "longest_continuous_seconds", "max_concurrent"]);
const PERFORMANCE_KEYS = new Set(["duration_ms", "timed_tokens", "sample_count", "token_coverage", "ms_per_1k_tokens"]);
const ALL_VIEWS = new Set(["models", "daily", "hourly", "agents", "workspaces", "sessions", "tasks", "time_metrics"]);

export class RequestError extends Error {
  constructor(status, code, message = code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function object(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new RequestError(422, "invalid_schema", `${name} must be an object`);
  }
  return value;
}

function exactKeys(value, allowed, required, name) {
  object(value, name);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) throw new RequestError(422, "invalid_schema", `${name}.${key} is not allowed`);
  }
  for (const key of required) {
    if (!(key in value)) throw new RequestError(422, "invalid_schema", `${name}.${key} is required`);
  }
}

function boundedString(value, name, maximum = 128) {
  if (typeof value !== "string" || value.length < 1 || value.length > maximum) {
    throw new RequestError(422, "invalid_schema", `${name} is invalid`);
  }
}

function nonnegativeInteger(value, name) {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new RequestError(422, "invalid_schema", `${name} is invalid`);
  }
}

function dateTime(value, name) {
  boundedString(value, name, 64);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value) || !Number.isFinite(Date.parse(value))) {
    throw new RequestError(422, "invalid_schema", `${name} must be a UTC date-time`);
  }
}

function validateTokens(tokens, path) {
  exactKeys(tokens, TOKEN_KEYS, TOKEN_KEYS, path);
  for (const key of TOKEN_KEYS) nonnegativeInteger(tokens[key], `${path}.${key}`);
}

function validateAggregate(row, path, allowedViews) {
  exactKeys(row, AGGREGATE_KEYS, new Set(["view", "bucket_start", "tokens"]), path);
  if (!ALL_VIEWS.has(row.view) || !allowedViews.has(row.view)) {
    throw new RequestError(422, "view_not_allowed", `${path}.view is not allowed`);
  }
  dateTime(row.bucket_start, `${path}.bucket_start`);
  validateTokens(row.tokens, `${path}.tokens`);
  for (const key of ["client", "provider", "agent", "workspace_label"]) {
    if (key in row) boundedString(row[key], `${path}.${key}`, 128);
  }
  if ("model" in row) boundedString(row.model, `${path}.model`, 256);
  if ("task_label" in row) boundedString(row.task_label, `${path}.task_label`, 160);
  for (const key of ["workspace_key", "session_key"]) {
    if (key in row && !HEX64.test(row[key])) throw new RequestError(422, "invalid_schema", `${path}.${key} is invalid`);
  }
  if ("message_count" in row) nonnegativeInteger(row.message_count, `${path}.message_count`);
  if ("cost" in row) {
    exactKeys(row.cost, COST_KEYS, COST_KEYS, `${path}.cost`);
    if (!Number.isFinite(row.cost.amount) || row.cost.amount < 0) throw new RequestError(422, "invalid_schema", `${path}.cost.amount is invalid`);
    if (row.cost.currency !== "USD" || typeof row.cost.estimated !== "boolean") throw new RequestError(422, "invalid_schema", `${path}.cost is invalid`);
    boundedString(row.cost.pricing_revision, `${path}.cost.pricing_revision`, 256);
  }
  if ("activity" in row) {
    exactKeys(row.activity, ACTIVITY_KEYS, ACTIVITY_KEYS, `${path}.activity`);
    for (const key of ACTIVITY_KEYS) nonnegativeInteger(row.activity[key], `${path}.activity.${key}`);
  }
  if ("performance" in row) {
    exactKeys(row.performance, PERFORMANCE_KEYS, new Set(["duration_ms", "timed_tokens", "sample_count", "token_coverage"]), `${path}.performance`);
    for (const key of ["duration_ms", "timed_tokens", "sample_count"]) nonnegativeInteger(row.performance[key], `${path}.performance.${key}`);
    if (!Number.isFinite(row.performance.token_coverage) || row.performance.token_coverage < 0 || row.performance.token_coverage > 1) throw new RequestError(422, "invalid_schema", `${path}.performance.token_coverage is invalid`);
    if ("ms_per_1k_tokens" in row.performance && row.performance.ms_per_1k_tokens !== null && (!Number.isFinite(row.performance.ms_per_1k_tokens) || row.performance.ms_per_1k_tokens < 0)) throw new RequestError(422, "invalid_schema", `${path}.performance.ms_per_1k_tokens is invalid`);
  }
}

export function computePayloadHash(snapshot) {
  const unsigned = { ...snapshot };
  delete unsigned.idempotency_key;
  delete unsigned.payload_hash;
  return createHash("sha256").update(canonicalJson(unsigned)).digest("hex");
}

export function validateSnapshot(snapshot, { allowedViews = ["models", "daily", "hourly", "time_metrics"] } = {}) {
  const required = new Set(SNAPSHOT_KEYS);
  exactKeys(snapshot, SNAPSHOT_KEYS, required, "snapshot");
  if (snapshot.schema_version !== "skcounter.snapshot.v1") throw new RequestError(422, "unsupported_schema");
  if (!HEX64.test(snapshot.idempotency_key) || !HEX64.test(snapshot.payload_hash)) throw new RequestError(422, "invalid_schema", "snapshot hashes are invalid");
  if (snapshot.measurement_lane !== "harness_reported") throw new RequestError(422, "lane_not_allowed");
  if (!SAFE_ID.test(snapshot.node_id) || !SAFE_ID.test(snapshot.principal_id)) throw new RequestError(422, "invalid_schema", "node or principal is invalid");
  exactKeys(snapshot.collector, COLLECTOR_KEYS, COLLECTOR_KEYS, "snapshot.collector");
  if (snapshot.collector.product !== "skcounter") throw new RequestError(422, "invalid_schema", "collector product is invalid");
  boundedString(snapshot.collector.facade_version, "snapshot.collector.facade_version", 64);
  boundedString(snapshot.collector.backend, "snapshot.collector.backend", 64);
  boundedString(snapshot.collector.backend_version, "snapshot.collector.backend_version", 64);
  dateTime(snapshot.observed_at, "snapshot.observed_at");
  boundedString(snapshot.bucket_timezone, "snapshot.bucket_timezone", 64);
  exactKeys(snapshot.window, WINDOW_KEYS, WINDOW_KEYS, "snapshot.window");
  dateTime(snapshot.window.start, "snapshot.window.start");
  dateTime(snapshot.window.end, "snapshot.window.end");
  if (Date.parse(snapshot.window.start) > Date.parse(snapshot.window.end)) throw new RequestError(422, "invalid_schema", "snapshot window is reversed");
  if (!HEX64.test(snapshot.source_state_digest)) throw new RequestError(422, "invalid_schema", "source digest is invalid");
  if (!Array.isArray(snapshot.aggregates) || snapshot.aggregates.length > 10000) throw new RequestError(422, "invalid_schema", "snapshot aggregates are invalid");
  const views = new Set(allowedViews);
  snapshot.aggregates.forEach((row, index) => validateAggregate(row, `snapshot.aggregates[${index}]`, views));
  const computed = computePayloadHash(snapshot);
  if (snapshot.payload_hash !== computed || snapshot.idempotency_key !== computed) throw new RequestError(422, "payload_hash_mismatch");
  return snapshot;
}

function safePathSegment(value) {
  if (!SAFE_ID.test(value)) throw new RequestError(422, "invalid_identity");
  return value;
}

function privateDirectory(path) {
  mkdirSync(path, { recursive: true, mode: 0o700 });
  chmodSync(path, 0o700);
}

function exclusiveJson(path, value) {
  privateDirectory(dirname(path));
  const fd = openSync(path, "wx", 0o600);
  try {
    writeFileSync(fd, `${canonicalJson(value)}\n`, "utf8");
  } finally {
    closeSync(fd);
  }
}

function atomicJson(path, value) {
  privateDirectory(dirname(path));
  const temporary = `${path}.${process.pid}.${randomUUID()}.tmp`;
  writeFileSync(temporary, `${canonicalJson(value)}\n`, { mode: 0o600 });
  chmodSync(temporary, 0o600);
  renameSync(temporary, path);
}

export class CollectorStore {
  constructor(stateDir, now = () => new Date()) {
    this.stateDir = resolve(stateDir);
    this.now = now;
    privateDirectory(this.stateDir);
    this.metricsPath = join(this.stateDir, "metrics.json");
  }

  readMetrics() {
    try {
      return JSON.parse(readFileSync(this.metricsPath, "utf8"));
    } catch {
      return { accepted: 0, duplicated: 0, rejected: 0, replayed: 0, invalid: 0, delayed: 0, last_accepted_at: null };
    }
  }

  increment(name, observedAt = null) {
    const metrics = this.readMetrics();
    metrics[name] = (metrics[name] || 0) + 1;
    if (name === "accepted") metrics.last_accepted_at = this.now().toISOString();
    if (observedAt && this.now().getTime() - Date.parse(observedAt) > 30 * 60 * 1000) metrics.delayed = (metrics.delayed || 0) + 1;
    atomicJson(this.metricsPath, metrics);
  }

  reserveReplay(requestId) {
    const day = this.now().toISOString().slice(0, 10);
    exclusiveJson(join(this.stateDir, "replay", day, `${requestId}.json`), { request_id: requestId, received_at: this.now().toISOString() });
  }

  accept(snapshot) {
    const id = snapshot.idempotency_key;
    const ackPath = join(this.stateDir, "acks", `${id}.json`);
    if (existsSync(ackPath)) {
      const prior = JSON.parse(readFileSync(ackPath, "utf8"));
      if (prior.payload_hash !== snapshot.payload_hash) throw new RequestError(409, "idempotency_conflict");
      this.increment("duplicated", snapshot.observed_at);
      return { ...prior, duplicate: true };
    }

    const observationPath = join(this.stateDir, "observations", safePathSegment(snapshot.node_id), safePathSegment(snapshot.principal_id), `${id}.json`);
    if (!existsSync(observationPath)) exclusiveJson(observationPath, snapshot);
    else if (JSON.parse(readFileSync(observationPath, "utf8")).payload_hash !== snapshot.payload_hash) throw new RequestError(409, "idempotency_conflict");

    const projectionPath = join(this.stateDir, "projection-outbox", `${id}.json`);
    if (!existsSync(projectionPath)) exclusiveJson(projectionPath, snapshot);

    const ack = {
      schema_version: "skcounter.ack.v1",
      idempotency_key: id,
      payload_hash: snapshot.payload_hash,
      acknowledged_at: this.now().toISOString(),
      duplicate: false,
    };
    exclusiveJson(ackPath, ack);
    this.increment("accepted", snapshot.observed_at);
    return ack;
  }
}

export function loadConfig(path) {
  const config = JSON.parse(readFileSync(path, "utf8"));
  if (config.schema_version !== "skcounter.collector.config.v1") throw new Error("unsupported collector config schema");
  if (!config.bind_host || !Number.isInteger(config.port) || !config.state_dir) throw new Error("collector bind and state configuration is required");
  if (!config.tls?.cert_file || !config.tls?.key_file) throw new Error("collector TLS configuration is required");
  if (!config.capauth?.home || !config.capauth?.gnupg_home) throw new Error("collector CapAuth configuration is required");
  if (!config.trusted_issuers || Object.keys(config.trusted_issuers).length < 1) throw new Error("at least one trusted issuer is required");
  return config;
}

function verifyCapAuth(wire, config) {
  const verifier = config.capauth.verifier || join(HERE, "capauth_verify.py");
  const result = spawnSync(config.capauth.python || "python3", [verifier], {
    input: wire,
    encoding: "utf8",
    timeout: 20000,
    maxBuffer: 16384,
    env: {
      ...process.env,
      GNUPGHOME: config.capauth.gnupg_home,
      SKCOUNTER_CAPAUTH_HOME: config.capauth.home,
    },
  });
  let parsed;
  try { parsed = JSON.parse(result.stdout); } catch { throw new RequestError(401, "capauth_unavailable"); }
  if (result.status !== 0 || !parsed.ok) throw new RequestError(401, parsed.reason || "capauth_denied");
  return parsed;
}

export function authorize(snapshot, claims, config) {
  const issuer = String(claims.issuer || "").toUpperCase();
  const trust = config.trusted_issuers[issuer];
  if (!trust || trust.enabled === false) throw new RequestError(403, "issuer_not_trusted");
  if (claims.audience !== "skcounter") throw new RequestError(403, "audience_denied");
  if (!Array.isArray(claims.capabilities) || claims.capabilities.length !== 1 || claims.capabilities[0] !== "skcounter.report.submit") throw new RequestError(403, "scope_denied");
  if (claims.subject !== trust.subject) throw new RequestError(403, "subject_mismatch");
  if (snapshot.node_id !== trust.node_id || snapshot.principal_id !== trust.principal_id) throw new RequestError(403, "identity_mismatch");
  if (claims.metadata?.node_id !== trust.node_id || claims.metadata?.principal_id !== trust.principal_id) throw new RequestError(403, "token_binding_mismatch");
  const issued = Date.parse(claims.issued_at);
  const expires = Date.parse(claims.expires_at);
  const maximum = (config.max_token_lifetime_seconds || 3600) * 1000;
  if (!Number.isFinite(issued) || !Number.isFinite(expires) || expires <= issued || expires - issued > maximum + 1000) throw new RequestError(403, "token_lifetime_denied");
}

function sendJson(response, status, value) {
  const body = `${JSON.stringify(value)}\n`;
  response.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(body), "cache-control": "no-store" });
  response.end(body);
}

function readBody(request, maximum) {
  return new Promise((resolveBody, reject) => {
    const chunks = [];
    let size = 0;
    request.on("data", (chunk) => {
      size += chunk.length;
      if (size > maximum) {
        reject(new RequestError(413, "payload_too_large"));
      } else chunks.push(chunk);
    });
    request.on("end", () => resolveBody(Buffer.concat(chunks)));
    request.on("error", reject);
  });
}

export function createCollectorServer(config, { now = () => new Date(), authVerifier = verifyCapAuth } = {}) {
  const store = new CollectorStore(config.state_dir, now);
  const server = createServer({ cert: readFileSync(config.tls.cert_file), key: readFileSync(config.tls.key_file), minVersion: "TLSv1.2" }, async (request, response) => {
    try {
      if (request.method === "GET" && request.url === "/healthz") {
        const metrics = store.readMetrics();
        const latest = metrics.last_accepted_at ? Math.max(0, Math.floor((now().getTime() - Date.parse(metrics.last_accepted_at)) / 1000)) : null;
        sendJson(response, 200, { status: "ok", schema_version: "skcounter.health.v1", configured_principals: Object.keys(config.trusted_issuers).length, last_accepted_at: metrics.last_accepted_at, last_accepted_age_seconds: latest, counters: metrics });
        return;
      }
      if (request.method === "GET" && request.url === "/metrics") {
        const metrics = store.readMetrics();
        const lines = Object.entries(metrics).filter(([, value]) => Number.isFinite(value)).map(([name, value]) => `skcounter_collector_${name}_total ${value}`);
        const body = `${lines.join("\n")}\n`;
        response.writeHead(200, { "content-type": "text/plain; version=0.0.4", "content-length": Buffer.byteLength(body), "cache-control": "no-store" });
        response.end(body);
        return;
      }
      if (request.method !== "POST" || request.url !== "/v1/observations") throw new RequestError(404, "not_found");
      if (!String(request.headers["content-type"] || "").toLowerCase().startsWith("application/json")) throw new RequestError(415, "content_type_denied");
      const authorization = String(request.headers.authorization || "");
      if (!authorization.startsWith("CapAuth ")) throw new RequestError(401, "capauth_required");
      const wire = authorization.slice(8);
      const requestId = String(request.headers["x-skcounter-request-id"] || "");
      if (!REQUEST_ID.test(requestId)) throw new RequestError(400, "invalid_request_id");
      const sentAt = String(request.headers["x-skcounter-sent-at"] || "");
      const sentTime = Date.parse(sentAt);
      const skew = (config.allowed_clock_skew_seconds || 300) * 1000;
      if (!Number.isFinite(sentTime) || Math.abs(now().getTime() - sentTime) > skew) throw new RequestError(400, "clock_skew");
      const body = await readBody(request, config.max_body_bytes || 1048576);
      let snapshot;
      try { snapshot = JSON.parse(body.toString("utf8")); } catch { throw new RequestError(400, "malformed_json"); }
      validateSnapshot(snapshot, { allowedViews: config.allowed_views });
      const observedTime = Date.parse(snapshot.observed_at);
      const maximumAge = (config.max_observation_age_seconds || 604800) * 1000;
      if (observedTime > now().getTime() + skew) throw new RequestError(422, "observation_in_future");
      if (observedTime < now().getTime() - maximumAge) throw new RequestError(422, "observation_stale");
      if (Date.parse(snapshot.window.end) - Date.parse(snapshot.window.start) > (config.max_window_days || 366) * 86400000) throw new RequestError(422, "window_too_large");
      if (Date.parse(snapshot.window.end) > observedTime + 86400000) throw new RequestError(422, "window_in_future");
      if (request.headers["x-skcounter-idempotency-key"] !== snapshot.idempotency_key) throw new RequestError(400, "idempotency_header_mismatch");
      const claims = authVerifier(wire, config);
      authorize(snapshot, claims, config);
      try { store.reserveReplay(requestId); } catch (error) {
        if (error?.code === "EEXIST") {
          store.increment("replayed");
          throw new RequestError(409, "request_replay");
        }
        throw error;
      }
      const ack = store.accept(snapshot);
      sendJson(response, 200, ack);
    } catch (error) {
      const requestError = error instanceof RequestError ? error : new RequestError(500, "internal_error");
      if (requestError.status >= 400 && requestError.status < 500) store.increment(requestError.status === 422 ? "invalid" : "rejected");
      sendJson(response, requestError.status, { error: requestError.code });
    }
  });
  return { server, store };
}

function usage() {
  process.stderr.write("Usage: collector.mjs serve|check-config|retention --config PATH\n");
}

function retention(config, now = new Date()) {
  const policies = [
    ["replay", config.retention?.replays_days ?? 8],
    ["acks", config.retention?.acks_days ?? 30],
    ["projection-outbox", config.retention?.projection_outbox_days ?? 30],
    ["observations", config.retention?.observations_days ?? 90],
  ];
  let removed = 0;
  const walk = (path, cutoff) => {
    let entries;
    try { entries = readdirSync(path, { withFileTypes: true }); } catch { return; }
    for (const entry of entries) {
      const candidate = join(path, entry.name);
      if (entry.isDirectory()) walk(candidate, cutoff);
      else if (entry.isFile() && statSync(candidate).mtimeMs < cutoff) { unlinkSync(candidate); removed += 1; }
    }
  };
  for (const [directory, days] of policies) walk(join(config.state_dir, directory), now.getTime() - days * 86400000);
  return removed;
}

async function main(argv) {
  const command = argv[0];
  const configIndex = argv.indexOf("--config");
  if (!command || configIndex < 0 || !argv[configIndex + 1]) { usage(); return 2; }
  const config = loadConfig(argv[configIndex + 1]);
  if (command === "check-config") { process.stdout.write("SKCounter collector configuration valid\n"); return 0; }
  if (command === "retention") { process.stdout.write(`SKCounter collector retention removed ${retention(config)} files\n`); return 0; }
  if (command !== "serve") { usage(); return 2; }
  retention(config);
  const { server } = createCollectorServer(config);
  server.listen(config.port, config.bind_host, () => process.stdout.write(`SKCounter collector listening on ${config.bind_host}:${config.port}\n`));
  return new Promise((resolveMain) => {
    const stop = () => server.close(() => resolveMain(0));
    process.once("SIGTERM", stop);
    process.once("SIGINT", stop);
  });
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = await main(process.argv.slice(2));
}
