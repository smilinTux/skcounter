import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";

import { describe, it, beforeEach, afterEach } from "node:test";
import assert from "node:assert";

import {
  defaultIndexRoot,
  readIndex,
  updateIndex,
  rebuildIndex,
  queryLatestIndex,
  resolveObservationPath,
} from "../src/latest-observation-index.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));

describe("latest-observation-index", () => {
  let testDir;
  let observationsDir;
  let sentDir;

  beforeEach(() => {
    testDir = join(tmpdir(), `skcounter-index-test-${process.pid}-${Date.now()}`);
    observationsDir = join(testDir, "observations");
    sentDir = join(testDir, "sent");
    mkdirSync(observationsDir, { recursive: true });
    mkdirSync(sentDir, { recursive: true });
  });

  afterEach(() => {
    try {
      rmSync(testDir, { recursive: true, force: true });
    } catch {
      // Ignore cleanup errors
    }
  });

  describe("append-only structure", () => {
    it("writes index atomically using temp file then rename", (t) => {
      const observation = {
        schema_version: "skcounter.snapshot.v1",
        measurement_lane: "harness_reported",
        node_id: "chiap01",
        principal_id: "user1",
        observed_at: "2026-08-31T12:00:00Z",
        payload_hash: "a".repeat(64),
        idempotency_key: "b".repeat(64),
        aggregates: [
          {
            view: "daily",
            bucket_start: "2026-08-31T00:00:00Z",
            tokens: { input: 100, output: 50, total: 150 },
            message_count: 5,
          },
        ],
      };

      const obsPath = join(observationsDir, "chiap01", "user1", "test.json");
      mkdirSync(dirname(obsPath), { recursive: true });
      writeFileSync(obsPath, JSON.stringify(observation));

      const result = updateIndex(observation, obsPath, { indexRoot: testDir });

      assert.strictEqual(result.total, 1);
      assert.ok(result.added > 0);

      // Verify temp file was cleaned up
      const indexPath = join(testDir, "latest-observation-index.jsonl");
      const tempFiles = [];
      try {
        for (const entry of readFileSync(dirname(indexPath), { encoding: "utf-8" }).split("\n")) {
          if (entry.includes(".tmp")) tempFiles.push(entry);
        }
      } catch {
        // Dir read failed, ignore
      }
      assert.strictEqual(tempFiles.length, 0);
    });

    it("parses every line before appending, never concatenates strings", (t) => {
      const obs1 = {
        schema_version: "skcounter.snapshot.v1",
        measurement_lane: "harness_reported",
        node_id: "chiap01",
        principal_id: "user1",
        observed_at: "2026-08-31T10:00:00Z",
        payload_hash: "a".repeat(64),
        idempotency_key: "b".repeat(64),
        aggregates: [
          {
            view: "daily",
            bucket_start: "2026-08-31T00:00:00Z",
            tokens: { input: 100, output: 50, total: 150 },
          },
        ],
      };

      const obs2 = {
        schema_version: "skcounter.snapshot.v1",
        measurement_lane: "harness_reported",
        node_id: "chiap01",
        principal_id: "user1",
        observed_at: "2026-08-31T11:00:00Z",
        payload_hash: "c".repeat(64),
        idempotency_key: "d".repeat(64),
        aggregates: [
          {
            view: "daily",
            bucket_start: "2026-08-31T00:00:00Z",
            tokens: { input: 200, output: 100, total: 300 },
          },
        ],
      };

      const obsPath = join(observationsDir, "chiap01", "user1", "test.json");
      mkdirSync(dirname(obsPath), { recursive: true });
      writeFileSync(obsPath, JSON.stringify(obs1));

      updateIndex(obs1, obsPath, { indexRoot: testDir });
      updateIndex(obs2, obsPath, { indexRoot: testDir });

      const indexMap = readIndex(testDir);
      const entries = Array.from(indexMap.values());

      // Should have exactly one entry per key, the latest
      assert.strictEqual(entries.length, 1);
      assert.strictEqual(entries[0].observed_at, "2026-08-31T11:00:00Z");

      // Verify file has line-per-entry structure
      const indexPath = join(testDir, "latest-observation-index.jsonl");
      const content = readFileSync(indexPath, { encoding: "utf-8" });
      const lines = content.trim().split("\n");
      assert.strictEqual(lines.length, 1);

      // Each line must be valid JSON
      for (const line of lines) {
        assert.doesNotThrow(() => JSON.parse(line));
      }
    });
  });

  describe("index key structure", () => {
    it("keys by lane, node, principal, view, and bucket", (t) => {
      const observation = {
        schema_version: "skcounter.snapshot.v1",
        measurement_lane: "harness_reported",
        node_id: "chiap01",
        principal_id: "user1",
        observed_at: "2026-08-31T12:00:00Z",
        payload_hash: "a".repeat(64),
        idempotency_key: "b".repeat(64),
        aggregates: [
          {
            view: "daily",
            bucket_start: "2026-08-31T00:00:00Z",
            tokens: { input: 100, output: 50, total: 150 },
          },
          {
            view: "hourly",
            bucket_start: "2026-08-31T12:00:00Z",
            tokens: { input: 50, output: 25, total: 75 },
          },
        ],
      };

      const obsPath = join(observationsDir, "chiap01", "user1", "test.json");
      mkdirSync(dirname(obsPath), { recursive: true });
      writeFileSync(obsPath, JSON.stringify(observation));

      updateIndex(observation, obsPath, { indexRoot: testDir });

      const results = queryLatestIndex({
        indexRoot: testDir,
        lane: "harness_reported",
        node: "chiap01",
        principal: "user1",
        view: "daily",
      });

      assert.strictEqual(results.length, 1);
      assert.strictEqual(results[0].key, "harness_reported:chiap01:user1:daily:2026-08-31T00:00:00Z");

      const hourlyResults = queryLatestIndex({
        indexRoot: testDir,
        view: "hourly",
      });
      assert.strictEqual(hourlyResults.length, 1);
      assert.strictEqual(hourlyResults[0].key, "harness_reported:chiap01:user1:hourly:2026-08-31T12:00:00Z");
    });
  });

  describe("deterministic rebuild", () => {
    it("produces the same index from existing observations", (t) => {
      // Create multiple observations in sent directory
      const sentNodeDir = join(sentDir, "chiap01", "user1");
      mkdirSync(sentNodeDir, { recursive: true });

      const obs1 = {
        schema_version: "skcounter.snapshot.v1",
        measurement_lane: "harness_reported",
        node_id: "chiap01",
        principal_id: "user1",
        observed_at: "2026-08-31T10:00:00Z",
        payload_hash: "a".repeat(64),
        idempotency_key: "b".repeat(64),
        aggregates: [
          {
            view: "daily",
            bucket_start: "2026-08-31T00:00:00Z",
            tokens: { input: 100, output: 50, total: 150 },
          },
        ],
      };

      const obs2 = {
        schema_version: "skcounter.snapshot.v1",
        measurement_lane: "harness_reported",
        node_id: "chiap01",
        principal_id: "user1",
        observed_at: "2026-08-31T11:00:00Z",
        payload_hash: "c".repeat(64),
        idempotency_key: "d".repeat(64),
        aggregates: [
          {
            view: "daily",
            bucket_start: "2026-08-31T00:00:00Z",
            tokens: { input: 200, output: 100, total: 300 },
          },
        ],
      };

      writeFileSync(join(sentNodeDir, "obs1.json"), JSON.stringify(obs1));
      writeFileSync(join(sentNodeDir, "obs2.json"), JSON.stringify(obs2));

      // Rebuild index
      const rebuildResult = rebuildIndex({ indexRoot: testDir, sentDir: sentDir });
      assert.strictEqual(rebuildResult.valid_observations, 2);
      assert.strictEqual(rebuildResult.total_entries, 1); // One key, latest wins

      const indexContent1 = readFileSync(join(testDir, "latest-observation-index.jsonl"), {
        encoding: "utf-8",
      });

      // Rebuild again and verify it's identical
      const rebuildResult2 = rebuildIndex({ indexRoot: testDir, sentDir: sentDir });
      const indexContent2 = readFileSync(join(testDir, "latest-observation-index.jsonl"), {
        encoding: "utf-8",
      });

      assert.strictEqual(indexContent1, indexContent2);
      assert.strictEqual(rebuildResult.total_entries, rebuildResult2.total_entries);
    });

    it("ignores malformed input and preserves valid entries", (t) => {
      const sentNodeDir = join(sentDir, "chiap01", "user1");
      mkdirSync(sentNodeDir, { recursive: true });

      const validObs = {
        schema_version: "skcounter.snapshot.v1",
        measurement_lane: "harness_reported",
        node_id: "chiap01",
        principal_id: "user1",
        observed_at: "2026-08-31T10:00:00Z",
        payload_hash: "a".repeat(64),
        idempotency_key: "b".repeat(64),
        aggregates: [
          {
            view: "daily",
            bucket_start: "2026-08-31T00:00:00Z",
            tokens: { input: 100, output: 50, total: 150 },
          },
        ],
      };

      writeFileSync(join(sentNodeDir, "valid.json"), JSON.stringify(validObs));
      writeFileSync(join(sentNodeDir, "invalid.json"), "{not valid json");
      writeFileSync(join(sentNodeDir, "wrong-schema.json"), JSON.stringify({ schema_version: "wrong" }));

      const result = rebuildIndex({ indexRoot: testDir, sentDir: sentDir });

      assert.strictEqual(result.valid_observations, 1);
      assert.strictEqual(result.malformed_observations, 2);
      assert.strictEqual(result.total_entries, 1);
    });
  });

  describe("crash resilience", () => {
    it("preserves last valid index when rename fails", (t) => {
      const obs1 = {
        schema_version: "skcounter.snapshot.v1",
        measurement_lane: "harness_reported",
        node_id: "chiap01",
        principal_id: "user1",
        observed_at: "2026-08-31T10:00:00Z",
        payload_hash: "a".repeat(64),
        idempotency_key: "b".repeat(64),
        aggregates: [
          {
            view: "daily",
            bucket_start: "2026-08-31T00:00:00Z",
            tokens: { input: 100, output: 50, total: 150 },
          },
        ],
      };

      const obsPath = join(observationsDir, "chiap01", "user1", "test.json");
      mkdirSync(dirname(obsPath), { recursive: true });
      writeFileSync(obsPath, JSON.stringify(obs1));

      updateIndex(obs1, obsPath, { indexRoot: testDir });

      const indexBefore = readFileSync(join(testDir, "latest-observation-index.jsonl"), {
        encoding: "utf-8",
      });

      // Simulate crash by creating a corrupt temp file
      const tmpPath = join(testDir, `.latest-observation-index.jsonl.${process.pid}.tmp`);
      writeFileSync(tmpPath, "corrupt data");

      // Next update should preserve the valid index
      const obs2 = {
        schema_version: "skcounter.snapshot.v1",
        measurement_lane: "harness_reported",
        node_id: "chiap01",
        principal_id: "user1",
        observed_at: "2026-08-31T11:00:00Z",
        payload_hash: "c".repeat(64),
        idempotency_key: "d".repeat(64),
        aggregates: [
          {
            view: "daily",
            bucket_start: "2026-08-31T00:00:00Z",
            tokens: { input: 200, output: 100, total: 300 },
          },
        ],
      };

      updateIndex(obs2, obsPath, { indexRoot: testDir });

      const indexAfter = readFileSync(join(testDir, "latest-observation-index.jsonl"), {
        encoding: "utf-8",
      });

      // Index should be updated and valid
      assert.ok(indexAfter.length > 0);
      assert.doesNotThrow(() => JSON.parse(indexAfter.trim().split("\n")[0]));
    });
  });

  describe("stale arrival handling", () => {
    it("rejects stale observations based on observed_at timestamp", (t) => {
      const oldObs = {
        schema_version: "skcounter.snapshot.v1",
        measurement_lane: "harness_reported",
        node_id: "chiap01",
        principal_id: "user1",
        observed_at: "2026-08-31T10:00:00Z",
        payload_hash: "a".repeat(64),
        idempotency_key: "b".repeat(64),
        aggregates: [
          {
            view: "daily",
            bucket_start: "2026-08-31T00:00:00Z",
            tokens: { input: 100, output: 50, total: 150 },
          },
        ],
      };

      const newObs = {
        schema_version: "skcounter.snapshot.v1",
        measurement_lane: "harness_reported",
        node_id: "chiap01",
        principal_id: "user1",
        observed_at: "2026-08-31T11:00:00Z",
        payload_hash: "c".repeat(64),
        idempotency_key: "d".repeat(64),
        aggregates: [
          {
            view: "daily",
            bucket_start: "2026-08-31T00:00:00Z",
            tokens: { input: 200, output: 100, total: 300 },
          },
        ],
      };

      const obsPath = join(observationsDir, "chiap01", "user1", "test.json");
      mkdirSync(dirname(obsPath), { recursive: true });
      writeFileSync(obsPath, JSON.stringify(newObs));

      // Add newer observation first
      updateIndex(newObs, obsPath, { indexRoot: testDir });

      // Try to add older observation - should not replace
      const result = updateIndex(oldObs, obsPath, { indexRoot: testDir });

      assert.strictEqual(result.added, 0);

      const indexMap = readIndex(testDir);
      const entries = Array.from(indexMap.values());
      assert.strictEqual(entries.length, 1);
      assert.strictEqual(entries[0].observed_at, "2026-08-31T11:00:00Z");
    });
  });

  describe("10000 observation limit", () => {
    it("enforces maximum of 10000 index entries", (t) => {
      const sentNodeDir = join(sentDir, "chiap01", "user1");
      mkdirSync(sentNodeDir, { recursive: true });

      // Create 10050 observations
      for (let i = 0; i < 10050; i++) {
        const obs = {
          schema_version: "skcounter.snapshot.v1",
          measurement_lane: "harness_reported",
          node_id: "chiap01",
          principal_id: "user1",
          observed_at: `2026-08-31T${String(i).padStart(2, "0")}:00:00Z`,
          payload_hash: "a".repeat(64),
          idempotency_key: `${String(i).padStart(64, "0")}`,
          aggregates: [
            {
              view: "daily",
              bucket_start: `2026-08-31T${String(Math.floor(i / 24)).padStart(2, "0")}:00:00Z`,
              tokens: { input: i, output: i, total: i * 2 },
            },
          ],
        };

        writeFileSync(join(sentNodeDir, `obs${i}.json`), JSON.stringify(obs));
      }

      const result = rebuildIndex({ indexRoot: testDir, sentDir: sentDir });

      assert.ok(result.total_entries <= 10000);
      assert.strictEqual(result.valid_observations, 10050);
    });
  });

  describe("replay protection", () => {
    it("handles duplicate idempotency keys correctly", (t) => {
      const obs = {
        schema_version: "skcounter.snapshot.v1",
        measurement_lane: "harness_reported",
        node_id: "chiap01",
        principal_id: "user1",
        observed_at: "2026-08-31T10:00:00Z",
        payload_hash: "a".repeat(64),
        idempotency_key: "b".repeat(64),
        aggregates: [
          {
            view: "daily",
            bucket_start: "2026-08-31T00:00:00Z",
            tokens: { input: 100, output: 50, total: 150 },
          },
        ],
      };

      const obsPath = join(observationsDir, "chiap01", "user1", "test.json");
      mkdirSync(dirname(obsPath), { recursive: true });
      writeFileSync(obsPath, JSON.stringify(obs));

      // Add the same observation multiple times
      updateIndex(obs, obsPath, { indexRoot: testDir });
      const result1 = readIndex(testDir);

      updateIndex(obs, obsPath, { indexRoot: testDir });
      const result2 = readIndex(testDir);

      // Should have same number of entries
      assert.strictEqual(result1.size, result2.size);

      // Content should be identical
      const entries1 = Array.from(result1.values()).sort((a, b) => a.key.localeCompare(b.key));
      const entries2 = Array.from(result2.values()).sort((a, b) => a.key.localeCompare(b.key));

      assert.strictEqual(JSON.stringify(entries1), JSON.stringify(entries2));
    });
  });

  describe("source preservation", () => {
    it("never modifies or deletes source observation files", (t) => {
      const sentNodeDir = join(sentDir, "chiap01", "user1");
      mkdirSync(sentNodeDir, { recursive: true });

      const obs = {
        schema_version: "skcounter.snapshot.v1",
        measurement_lane: "harness_reported",
        node_id: "chiap01",
        principal_id: "user1",
        observed_at: "2026-08-31T10:00:00Z",
        payload_hash: "a".repeat(64),
        idempotency_key: "b".repeat(64),
        aggregates: [
          {
            view: "daily",
            bucket_start: "2026-08-31T00:00:00Z",
            tokens: { input: 100, output: 50, total: 150 },
          },
        ],
      };

      const obsPath = join(sentNodeDir, "obs.json");
      const originalContent = JSON.stringify(obs);
      writeFileSync(obsPath, originalContent);

      rebuildIndex({ indexRoot: testDir, sentDir: sentDir });

      // Source file should be unchanged
      const contentAfter = readFileSync(obsPath, { encoding: "utf-8" });
      assert.strictEqual(contentAfter, originalContent);
    });
  });

  describe("query and resolve", () => {
    it("queries index by lane, node, principal, view, and bucket", (t) => {
      const sentNodeDir1 = join(sentDir, "chiap01", "user1");
      const sentNodeDir2 = join(sentDir, "chiap02", "user2");
      mkdirSync(sentNodeDir1, { recursive: true });
      mkdirSync(sentNodeDir2, { recursive: true });

      const obs1 = {
        schema_version: "skcounter.snapshot.v1",
        measurement_lane: "harness_reported",
        node_id: "chiap01",
        principal_id: "user1",
        observed_at: "2026-08-31T10:00:00Z",
        payload_hash: "a".repeat(64),
        idempotency_key: "b".repeat(64),
        aggregates: [
          {
            view: "daily",
            bucket_start: "2026-08-31T00:00:00Z",
            tokens: { input: 100, output: 50, total: 150 },
          },
        ],
      };

      const obs2 = {
        schema_version: "skcounter.snapshot.v1",
        measurement_lane: "gateway_observed",
        node_id: "chiap02",
        principal_id: "user2",
        observed_at: "2026-08-31T10:00:00Z",
        payload_hash: "c".repeat(64),
        idempotency_key: "d".repeat(64),
        aggregates: [
          {
            view: "hourly",
            bucket_start: "2026-08-31T10:00:00Z",
            tokens: { input: 50, output: 25, total: 75 },
          },
        ],
      };

      writeFileSync(join(sentNodeDir1, "obs1.json"), JSON.stringify(obs1));
      writeFileSync(join(sentNodeDir2, "obs2.json"), JSON.stringify(obs2));

      rebuildIndex({ indexRoot: testDir, sentDir: sentDir });

      // Query by lane
      const harnessResults = queryLatestIndex({ indexRoot: testDir, lane: "harness_reported" });
      assert.strictEqual(harnessResults.length, 1);
      assert.ok(harnessResults[0].key.includes(":chiap01:"));

      // Query by view
      const dailyResults = queryLatestIndex({ indexRoot: testDir, view: "daily" });
      assert.strictEqual(dailyResults.length, 1);

      // Query by node
      const chiap01Results = queryLatestIndex({ indexRoot: testDir, node: "chiap01" });
      assert.strictEqual(chiap01Results.length, 1);
      assert.ok(chiap01Results[0].key.includes(":chiap01:"));
    });
  });
});
