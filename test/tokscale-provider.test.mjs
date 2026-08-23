import assert from "node:assert/strict";
import test from "node:test";

import {
  createTokscaleBackend,
  createTokscaleEnvironment,
  resolveTokscaleEntrypoint,
} from "../src/providers/tokscale.mjs";

test("entrypoint is resolved relative to the pinned package", () => {
  const result = resolveTokscaleEntrypoint(() => "/opt/provider/package.json");
  assert.equal(result, "/opt/provider/bin.js");
});

test("backend environment isolates configuration and disables the social API", () => {
  const result = createTokscaleEnvironment({ HOME: "/home/alice", PATH: "/bin" });
  assert.equal(result.TOKSCALE_API_URL, "http://127.0.0.1:9");
  assert.equal(
    result.XDG_CONFIG_HOME,
    "/home/alice/.config/skcounter/providers",
  );
  assert.equal(result.PATH, "/bin");
});

test("backend forwards arguments and returns provider status", () => {
  let invocation;
  const backend = createTokscaleBackend({
    environment: { HOME: "/home/alice", PATH: "/bin" },
    resolver: () => "/provider/package.json",
    spawn: (executable, args, options) => {
      invocation = { executable, args, options };
      return { status: 7 };
    },
  });

  const result = backend.execute(["models", "--json"]);
  assert.equal(result.code, 7);
  assert.equal(invocation.executable, process.execPath);
  assert.deepEqual(invocation.args, [
    "/provider/bin.js",
    "models",
    "--json",
  ]);
  assert.equal(invocation.options.stdio, "inherit");
  assert.equal(invocation.options.env.TOKSCALE_API_URL, "http://127.0.0.1:9");
});

test("backend captures JSON reports without inheriting stdio", () => {
  let options;
  const backend = createTokscaleBackend({
    environment: { HOME: "/home/alice", PATH: "/bin" },
    resolver: () => "/provider/package.json",
    spawn: (_executable, _args, receivedOptions) => {
      options = receivedOptions;
      return { status: 0, stdout: "{\"entries\":[]}", stderr: "" };
    },
  });

  const result = backend.capture(["hourly", "--json"]);
  assert.equal(result.code, 0);
  assert.equal(result.stdout, "{\"entries\":[]}");
  assert.equal(options.encoding, "utf8");
  assert.equal(options.stdio, undefined);
});

test("missing HOME fails without executing provider", () => {
  let executed = false;
  const backend = createTokscaleBackend({
    environment: {},
    resolver: () => "/provider/package.json",
    spawn: () => {
      executed = true;
      return { status: 0 };
    },
  });

  const result = backend.execute([]);
  assert.equal(result.code, 1);
  assert.match(result.error, /HOME is required/);
  assert.equal(executed, false);
});
