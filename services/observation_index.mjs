import { createHash, randomUUID } from "node:crypto";
import {
  chmodSync,
  closeSync,
  existsSync,
  mkdirSync,
  openSync,
  readFileSync,
  readdirSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";

import { canonicalJson } from "../src/snapshot.mjs";

const INDEX_SCHEMA = "skcounter.latest-observation-index.v1";

function privateDirectory(path) {
  mkdirSync(path, { recursive: true, mode: 0o700 });
  chmodSync(path, 0o700);
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function logicalKey(entry) {
  return [
    entry.measurement_lane,
    entry.node_id,
    entry.principal_id,
    entry.view,
    entry.bucket_start,
  ].join("\u0000");
}

function isLater(candidate, current) {
  const candidateTime = Date.parse(candidate.observed_at);
  const currentTime = Date.parse(current.observed_at);
  if (candidateTime !== currentTime) {
    return candidateTime > currentTime;
  }
  return candidate.idempotency_key > current.idempotency_key;
}

function validateIndexableSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== "object" || Array.isArray(snapshot)) {
    throw new Error("observation must be an object");
  }
  for (const field of [
    "measurement_lane",
    "node_id",
    "principal_id",
    "observed_at",
    "idempotency_key",
    "payload_hash",
  ]) {
    if (typeof snapshot[field] !== "string" || snapshot[field].length === 0) {
      throw new Error(`observation ${field} is invalid`);
    }
  }
  if (!Array.isArray(snapshot.aggregates)) {
    throw new Error("observation aggregates are invalid");
  }
  for (const row of snapshot.aggregates) {
    if (!row || typeof row.view !== "string" || typeof row.bucket_start !== "string") {
      throw new Error("observation aggregate key is invalid");
    }
  }
}

function makeDocument(entries) {
  const unsigned = {
    schema_version: INDEX_SCHEMA,
    entries: [...entries].sort((left, right) => {
      const leftKey = logicalKey(left);
      const rightKey = logicalKey(right);
      return leftKey < rightKey ? -1 : leftKey > rightKey ? 1 : 0;
    }),
  };
  return { ...unsigned, index_sha256: sha256(canonicalJson(unsigned)) };
}

function validateDocument(document) {
  if (
    !document
    || document.schema_version !== INDEX_SCHEMA
    || !Array.isArray(document.entries)
    || typeof document.index_sha256 !== "string"
  ) {
    throw new Error("latest observation index is malformed");
  }
  const expected = sha256(canonicalJson({
    schema_version: document.schema_version,
    entries: document.entries,
  }));
  if (expected !== document.index_sha256) {
    throw new Error("latest observation index hash mismatch");
  }
  const seen = new Set();
  for (const entry of document.entries) {
    const key = logicalKey(entry);
    if (seen.has(key)) throw new Error("latest observation index contains duplicate keys");
    seen.add(key);
  }
  return document;
}

function readCurrent(stateDir) {
  const path = join(stateDir, "observation-index", "latest.json");
  if (!existsSync(path)) return makeDocument([]);
  return validateDocument(JSON.parse(readFileSync(path, "utf8")));
}

function exclusiveBytes(path, bytes) {
  privateDirectory(dirname(path));
  let fd;
  try {
    fd = openSync(path, "wx", 0o600);
    writeFileSync(fd, bytes);
  } catch (error) {
    if (error?.code !== "EEXIST") throw error;
    if (readFileSync(path, "utf8") !== bytes) {
      throw new Error("content-addressed index generation mismatch");
    }
  } finally {
    if (fd !== undefined) closeSync(fd);
  }
}

function persist(stateDir, document, { beforeRename } = {}) {
  validateDocument(document);
  const root = join(stateDir, "observation-index");
  privateDirectory(root);
  const bytes = `${canonicalJson(document)}\n`;
  exclusiveBytes(join(root, "generations", `${document.index_sha256}.json`), bytes);
  const latest = join(root, "latest.json");
  const temporary = `${latest}.${process.pid}.${randomUUID()}.tmp`;
  writeFileSync(temporary, bytes, { encoding: "utf8", flag: "wx", mode: 0o600 });
  chmodSync(temporary, 0o600);
  if (beforeRename) beforeRename({ latest, temporary });
  renameSync(temporary, latest);
  return document;
}

function entriesForSnapshot(snapshot, sourcePath, sourceSha256) {
  validateIndexableSnapshot(snapshot);
  const keys = new Map();
  for (const row of snapshot.aggregates) {
    const entry = {
      measurement_lane: snapshot.measurement_lane,
      node_id: snapshot.node_id,
      principal_id: snapshot.principal_id,
      view: row.view,
      bucket_start: row.bucket_start,
      observed_at: snapshot.observed_at,
      idempotency_key: snapshot.idempotency_key,
      payload_hash: snapshot.payload_hash,
      source_path: sourcePath,
      source_sha256: sourceSha256,
    };
    keys.set(logicalKey(entry), entry);
  }
  return [...keys.values()];
}

function mergeEntries(current, additions) {
  const winners = new Map(current.map((entry) => [logicalKey(entry), entry]));
  for (const candidate of additions) {
    const key = logicalKey(candidate);
    const prior = winners.get(key);
    if (!prior || isLater(candidate, prior)) winners.set(key, candidate);
  }
  return [...winners.values()];
}

function observationFiles(root) {
  const files = [];
  const walk = (directory) => {
    let children;
    try {
      children = readdirSync(directory, { withFileTypes: true });
    } catch (error) {
      if (error?.code === "ENOENT") return;
      throw error;
    }
    for (const child of children) {
      const path = join(directory, child.name);
      if (child.isDirectory()) walk(path);
      else if (child.isFile() && child.name.endsWith(".json")) files.push(path);
    }
  };
  walk(root);
  return files.sort();
}

/**
 * Update the content-addressed latest index after one accepted observation.
 */
export function updateLatestObservationIndex(stateDir, snapshot, observationPath, options = {}) {
  const root = resolve(stateDir);
  const source = resolve(observationPath);
  const observationsRoot = join(root, "observations");
  if (source !== observationsRoot && !source.startsWith(`${observationsRoot}${sep}`)) {
    throw new Error("observation source is outside the collector store");
  }
  const bytes = readFileSync(source);
  const sourcePath = relative(root, source).split(sep).join("/");
  const current = readCurrent(root);
  const entries = mergeEntries(
    current.entries,
    entriesForSnapshot(snapshot, sourcePath, sha256(bytes)),
  );
  return persist(root, makeDocument(entries), options);
}

/**
 * Rebuild the same latest index from every append-only observation.
 */
export function rebuildLatestObservationIndex(stateDir, { validate, ...options } = {}) {
  const root = resolve(stateDir);
  const entries = [];
  for (const path of observationFiles(join(root, "observations"))) {
    const bytes = readFileSync(path);
    let snapshot;
    try {
      snapshot = JSON.parse(bytes.toString("utf8"));
    } catch {
      throw new Error(`observation is malformed: ${relative(root, path)}`);
    }
    if (validate) validate(snapshot);
    validateIndexableSnapshot(snapshot);
    entries.push(...entriesForSnapshot(
      snapshot,
      relative(root, path).split(sep).join("/"),
      sha256(bytes),
    ));
  }
  return persist(root, makeDocument(mergeEntries([], entries)), options);
}

/**
 * Read and verify the bounded latest-observation index.
 */
export function readLatestObservationIndex(stateDir) {
  return readCurrent(resolve(stateDir));
}
