import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { spawnSync } from "node:child_process";

import {
  LOCAL_ONLY_API_URL,
  TOKSCALE_VERSION,
} from "../constants.mjs";

const require = createRequire(import.meta.url);

export function resolveTokscaleEntrypoint(resolver = require.resolve) {
  const packagePath = resolver("@tokscale/cli/package.json");
  return join(dirname(packagePath), "bin.js");
}

export function createTokscaleEnvironment(environment = process.env) {
  const home = environment.HOME;
  if (!home) {
    throw new Error("HOME is required to locate local harness session stores");
  }

  return {
    ...environment,
    TOKSCALE_API_URL: LOCAL_ONLY_API_URL,
    XDG_CONFIG_HOME:
      environment.SKCOUNTER_BACKEND_CONFIG_HOME ??
      join(home, ".config", "skcounter", "providers"),
  };
}

export function createTokscaleBackend({
  resolver = require.resolve,
  spawn = spawnSync,
  environment = process.env,
} = {}) {
  function prepare() {
    return {
      entrypoint: resolveTokscaleEntrypoint(resolver),
      childEnvironment: createTokscaleEnvironment(environment),
    };
  }

  return {
    id: "tokscale",
    version: TOKSCALE_VERSION,
    source: "https://github.com/junhoyeo/tokscale",
    execute(args) {
      let entrypoint;
      let childEnvironment;
      try {
        ({ entrypoint, childEnvironment } = prepare());
      } catch (error) {
        return {
          code: 1,
          error: error instanceof Error ? error.message : String(error),
        };
      }

      const result = spawn(process.execPath, [entrypoint, ...args], {
        env: childEnvironment,
        stdio: "inherit",
      });

      if (result.error) {
        return { code: 1, error: result.error.message };
      }
      if (typeof result.status !== "number") {
        return { code: 1, error: "backend exited without a status code" };
      }
      return { code: result.status };
    },
    capture(args) {
      let entrypoint;
      let childEnvironment;
      try {
        ({ entrypoint, childEnvironment } = prepare());
      } catch (error) {
        return {
          code: 1,
          error: error instanceof Error ? error.message : String(error),
          stdout: "",
        };
      }

      const result = spawn(process.execPath, [entrypoint, ...args], {
        encoding: "utf8",
        env: childEnvironment,
        maxBuffer: 128 * 1024 * 1024,
      });
      if (result.error) {
        return { code: 1, error: result.error.message, stdout: "" };
      }
      if (typeof result.status !== "number") {
        return { code: 1, error: "backend exited without a status code", stdout: "" };
      }
      return {
        code: result.status,
        error: result.status === 0 ? undefined : (result.stderr || "backend collection failed").trim(),
        stdout: result.stdout || "",
      };
    },
  };
}
