import assert from "node:assert/strict";
import test from "node:test";

import { run } from "../src/cli.mjs";

function fakeBackend(overrides = {}) {
  return {
    id: "fake",
    version: "1.2.3",
    source: "https://example.invalid/fake",
    execute: () => ({ code: 0 }),
    ...overrides,
  };
}

const collectionGraph = {
  meta: { dateRange: { start: "2026-08-23", end: "2026-08-23" } },
  contributions: [],
  timeMetrics: {},
};

function collectionBackend() {
  return fakeBackend({
    capture: (args) => ({
      code: 0,
      stdout: JSON.stringify(args[0] === "graph" ? collectionGraph : { entries: [] }),
    }),
  });
}

test("version identifies facade and backend", async () => {
  let stdout = "";
  const code = await run(["--version"], {
    backend: fakeBackend(),
    output: (value) => {
      stdout += value;
    },
  });

  assert.equal(code, 0);
  assert.match(stdout, /^skcounter 0\.1\.0 \(backend fake 1\.2\.3\)/);
});

test("governed arguments reach the selected backend", async () => {
  let received;
  const code = await run(["models", "--json"], {
    backend: fakeBackend({
      execute: (args) => {
        received = args;
        return { code: 0 };
      },
    }),
  });

  assert.equal(code, 0);
  assert.deepEqual(received, ["models", "--json", "--no-spinner"]);
});

test("policy denial does not execute the backend", async () => {
  let executed = false;
  let stderr = "";
  const code = await run(["submit"], {
    backend: fakeBackend({
      execute: () => {
        executed = true;
        return { code: 0 };
      },
    }),
    errorOutput: (value) => {
      stderr += value;
    },
  });

  assert.equal(code, 2);
  assert.equal(executed, false);
  assert.match(stderr, /SKCounter policy denied submit/);
});

test("backend failure is returned to the caller", async () => {
  let stderr = "";
  const code = await run(["clients", "--json"], {
    backend: fakeBackend({
      execute: () => ({ code: 1, error: "provider unavailable" }),
    }),
    errorOutput: (value) => {
      stderr += value;
    },
  });

  assert.equal(code, 1);
  assert.match(stderr, /provider unavailable/);
});

test("snapshot emits the provider-neutral aggregate contract", async () => {
  let stdout = "";
  const code = await run(["snapshot", "--since", "2026-08-23"], {
    backend: collectionBackend(),
    now: new Date("2026-08-23T12:00:00Z"),
    output: (value) => {
      stdout += value;
    },
  });

  assert.equal(code, 0);
  const document = JSON.parse(stdout);
  assert.equal(document.schema_version, "skcounter.snapshot.v1");
  assert.equal(document.collector.backend, "fake");
});

test("collect delegates to the durable observation writer", async () => {
  let received;
  let stdout = "";
  const code = await run(["collect", "--since", "2026-08-23", "--output-dir", "/safe"], {
    backend: collectionBackend(),
    now: new Date("2026-08-23T12:00:00Z"),
    observationWriter: (snapshot, options) => {
      received = { snapshot, options };
      return "/safe/observation.json";
    },
    output: (value) => {
      stdout += value;
    },
  });

  assert.equal(code, 0);
  assert.equal(received.options.outputDir, "/safe");
  assert.equal(received.snapshot.measurement_lane, "harness_reported");
  assert.match(stdout, /observation written/);
});
