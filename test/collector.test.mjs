import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { collectSnapshot } from "../src/snapshot.mjs";
import { authorize, CollectorStore, RequestError, validateSnapshot } from "../services/collector.mjs";

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
