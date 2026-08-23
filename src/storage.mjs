import { chmodSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { canonicalJson } from "./snapshot.mjs";

function safeSegment(value) {
  const normalized = String(value).replace(/[^a-zA-Z0-9._-]/g, "_");
  if (!normalized || normalized === "." || normalized === "..") {
    throw new Error("node and principal identifiers must contain safe characters");
  }
  return normalized;
}

export function defaultObservationRoot(environment = process.env) {
  if (environment.SKCOUNTER_STATE_DIR) {
    return join(environment.SKCOUNTER_STATE_DIR, "observations");
  }
  if (environment.XDG_STATE_HOME) {
    return join(environment.XDG_STATE_HOME, "skcounter", "observations");
  }
  if (!environment.HOME) throw new Error("HOME is required for the SKCounter state directory");
  return join(environment.HOME, ".local", "state", "skcounter", "observations");
}

export function writeObservation(snapshot, { outputDir, environment = process.env } = {}) {
  const root = outputDir || defaultObservationRoot(environment);
  const directory = join(root, safeSegment(snapshot.node_id), safeSegment(snapshot.principal_id));
  mkdirSync(directory, { mode: 0o700, recursive: true });
  chmodSync(directory, 0o700);
  const timestamp = snapshot.observed_at.replace(/[:.]/g, "");
  const path = join(directory, `${timestamp}-${snapshot.idempotency_key}.json`);
  writeFileSync(path, `${canonicalJson(snapshot)}\n`, { encoding: "utf8", flag: "wx", mode: 0o600 });
  return path;
}
