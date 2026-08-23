import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  collectSnapshot,
  parseCollectionOptions,
} from "../src/snapshot.mjs";
import { writeObservation } from "../src/storage.mjs";

const graph = {
  meta: {
    dateRange: { start: "2026-08-22", end: "2026-08-23" },
  },
  contributions: [
    {
      date: "2026-08-23",
      totals: { tokens: 150, cost: 1.5, messages: 3 },
      tokenBreakdown: {
        input: 20,
        output: 10,
        cacheRead: 100,
        cacheWrite: 0,
        reasoning: 20,
      },
      clients: [
        {
          client: "codex",
          providerId: "openai",
          modelId: "gpt-test",
          tokens: {
            input: 20,
            output: 10,
            cacheRead: 100,
            cacheWrite: 0,
            reasoning: 20,
          },
          cost: 1.5,
          messages: 3,
        },
      ],
    },
  ],
  timeMetrics: {
    totalActiveTimeMs: 125000,
    longestContinuousMs: 60000,
    maxConcurrentSessions: 2,
    sessionCount: 1,
  },
};

const hourly = {
  entries: [
    {
      hour: "2026-08-23 12:00",
      input: 20,
      output: 10,
      cacheRead: 100,
      cacheWrite: 0,
      messageCount: 3,
      cost: 1.5,
    },
  ],
};

function backend() {
  return {
    id: "tokscale",
    version: "4.13.0",
    capture(args) {
      return {
        code: 0,
        stdout: JSON.stringify(args[0] === "graph" ? graph : hourly),
      };
    },
  };
}

test("collection options default to a bounded rolling window", () => {
  const result = parseCollectionOptions([], new Date("2026-08-23T12:00:00Z"));
  assert.equal(result.since, "2026-07-25");
  assert.equal(result.until, "");
});

test("snapshot normalizes graph and hourly views without raw paths", () => {
  const snapshot = collectSnapshot({
    backend: backend(),
    options: { since: "2026-08-22", until: "2026-08-23" },
    now: new Date("2026-08-23T12:30:00Z"),
    nodeId: "chiap08",
    principalId: "jarvis",
    bucketTimezone: "America/Chicago",
  });

  assert.equal(snapshot.schema_version, "skcounter.snapshot.v1");
  assert.equal(snapshot.measurement_lane, "harness_reported");
  assert.equal(snapshot.idempotency_key.length, 64);
  assert.equal(snapshot.payload_hash, snapshot.idempotency_key);
  assert.deepEqual(
    snapshot.aggregates.map((row) => row.view),
    ["daily", "models", "time_metrics", "hourly"],
  );
  assert.equal(snapshot.aggregates[1].tokens.total, 150);
  assert.equal(snapshot.aggregates[2].activity.active_seconds, 125);
  assert.equal(snapshot.aggregates[3].tokens.total, 130);
  assert.doesNotMatch(JSON.stringify(snapshot), /sessionsPath|workspace_path|prompt|response/);
});

test("observation writer creates a private append-only file", () => {
  const root = mkdtempSync(join(tmpdir(), "skcounter-observation-"));
  const snapshot = collectSnapshot({
    backend: backend(),
    options: { since: "2026-08-22", until: "2026-08-23" },
    now: new Date("2026-08-23T12:30:00Z"),
    nodeId: "chiap08",
    principalId: "jarvis",
    bucketTimezone: "America/Chicago",
  });

  const path = writeObservation(snapshot, { outputDir: root });
  const written = JSON.parse(readFileSync(path, "utf8"));

  assert.equal(written.payload_hash, snapshot.payload_hash);
  assert.equal(statSync(path).mode & 0o777, 0o600);
  assert.equal(statSync(join(root, "chiap08", "jarvis")).mode & 0o777, 0o700);
  assert.throws(() => writeObservation(snapshot, { outputDir: root }), /EEXIST/);
});

test("invalid or reversed collection dates fail closed", () => {
  assert.throws(() => parseCollectionOptions(["--since", "today"]), /YYYY-MM-DD/);
  assert.throws(
    () => parseCollectionOptions(["--since", "2026-08-24", "--until", "2026-08-23"]),
    /cannot be after/,
  );
  assert.throws(() => parseCollectionOptions(["--remote"]), /unknown collection option/);
});
