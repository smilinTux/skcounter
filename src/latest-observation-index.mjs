import { mkdirSync, readdirSync, readFileSync, renameSync, statSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";

import { canonicalJson, digest } from "./snapshot.mjs";

const MAX_INDEX_ENTRIES = 10000;
const INDEX_SCHEMA_VERSION = "skcounter.latest-observation-index.v1";
const INDEX_FILE_NAME = "latest-observation-index.jsonl";

/**
 * Safe path segment extraction, matching the constraint from storage.mjs
 */
function safeSegment(value) {
  const normalized = String(value).replace(/[^a-zA-Z0-9._-]/g, "_");
  if (!normalized || normalized === "." || normalized === "..") {
    throw new Error("node and principal identifiers must contain safe characters");
  }
  return normalized;
}

/**
 * Default index root, matching the observation storage pattern
 */
export function defaultIndexRoot(environment = process.env) {
  if (environment.SKCOUNTER_STATE_DIR) {
    return environment.SKCOUNTER_STATE_DIR;
  }
  if (environment.XDG_STATE_HOME) {
    return join(environment.XDG_STATE_HOME, "skcounter");
  }
  if (!environment.HOME) throw new Error("HOME is required for the SKCounter state directory");
  return join(environment.HOME, ".local", "state", "skcounter");
}

/**
 * Parse a single line from the append-only index file
 */
function parseIndexLine(line, lineNumber) {
  line = line.trim();
  if (!line) return null;

  try {
    const entry = JSON.parse(line);
    if (entry.schema_version !== INDEX_SCHEMA_VERSION) {
      throw new Error(`invalid schema version at line ${lineNumber}`);
    }
    if (!entry.key || !entry.observation_path || !entry.observed_at) {
      throw new Error(`missing required field at line ${lineNumber}`);
    }
    return entry;
  } catch (error) {
    throw new Error(`malformed index entry at line ${lineNumber}: ${error.message}`);
  }
}

/**
 * Read the current index state from disk, returning a Map of keys to entries
 */
export function readIndex(indexDir) {
  const indexMap = new Map();
  const indexPath = join(indexDir, INDEX_FILE_NAME);

  try {
    const content = readFileSync(indexPath, { encoding: "utf-8" });
    const lines = content.split("\n");

    for (let i = 0; i < lines.length; i++) {
      const entry = parseIndexLine(lines[i], i + 1);
      if (entry) {
        const existing = indexMap.get(entry.key);
        if (!existing || entry.observed_at > existing.observed_at) {
          indexMap.set(entry.key, entry);
        }
      }
    }
  } catch (error) {
    if (error.code !== "ENOENT") {
      throw error;
    }
  }

  return indexMap;
}

/**
 * Derive the index key from an observation
 */
function deriveKey(observation) {
  const parts = [
    String(observation.measurement_lane || "unknown"),
    String(observation.node_id || "unknown"),
    String(observation.principal_id || "unknown"),
  ];

  // For each aggregate, derive a view-specific key
  const keys = [];
  if (Array.isArray(observation.aggregates)) {
    for (const agg of observation.aggregates) {
      const viewKey = [
        ...parts,
        String(agg.view || "unknown"),
        String(agg.bucket_start || "unknown"),
      ].join(":");
      keys.push(viewKey);
    }
  }

  // If no aggregates, use a single key with no view/bucket
  if (keys.length === 0) {
    keys.push(parts.join(":") + "::");
  }

  return keys;
}

/**
 * Compute a unique key for a specific aggregate view
 */
function computeAggregateKey(observation, aggregate) {
  return [
    String(observation.measurement_lane || "unknown"),
    String(observation.node_id || "unknown"),
    String(observation.principal_id || "unknown"),
    String(aggregate.view || "unknown"),
    String(aggregate.bucket_start || "unknown"),
  ].join(":");
}

/**
 * Extract the relative observation path from a full path
 */
function extractRelativePath(fullPath, indexRoot) {
  const observationsRoot = join(indexRoot, "observations");
  if (fullPath.startsWith(observationsRoot)) {
    return fullPath.slice(observationsRoot.length + 1);
  }
  return null;
}

/**
 * Write an index entry using atomic write-then-rename
 */
function writeIndexAtomically(indexPath, lines) {
  const tmpPath = `${indexPath}.${process.pid}.tmp`;
  const content = lines.join("\n") + "\n";

  writeFileSync(tmpPath, content, { encoding: "utf-8", mode: 0o600 });
  renameSync(tmpPath, indexPath);
}

/**
 * Update the index with a new observation, atomically
 */
export function updateIndex(observation, observationPath, { indexRoot, indexDir } = {}) {
  const root = indexRoot || defaultIndexRoot();
  const dir = indexDir || root;

  mkdirSync(dir, { mode: 0o700, recursive: true });

  const indexPath = join(dir, INDEX_FILE_NAME);
  const currentMap = readIndex(dir);

  const newEntries = [];
  const keys = deriveKey(observation);

  for (let i = 0; i < keys.length; i++) {
    const key = keys[i];
    const existing = currentMap.get(key);

    // Only update if this observation is newer
    if (!existing || observation.observed_at > existing.observed_at) {
      const entry = {
        schema_version: INDEX_SCHEMA_VERSION,
        key: key,
        observation_path: extractRelativePath(observationPath, root) || observationPath,
        observed_at: observation.observed_at,
        payload_hash: observation.payload_hash,
        idempotency_key: observation.idempotency_key,
      };
      newEntries.push(entry);
      currentMap.set(key, entry);
    }
  }

  // Write all existing entries plus new ones
  const allEntries = Array.from(currentMap.values());
  allEntries.sort((a, b) => a.key.localeCompare(b.key));

  if (allEntries.length > MAX_INDEX_ENTRIES) {
    // Keep only the most recent entries by key
    const uniqueByKey = new Map();
    for (const entry of allEntries.reverse()) {
      if (!uniqueByKey.has(entry.key)) {
        uniqueByKey.set(entry.key, entry);
        if (uniqueByKey.size >= MAX_INDEX_ENTRIES) break;
      }
    }
    allEntries.length = 0;
    allEntries.push(...Array.from(uniqueByKey.values()).reverse());
  }

  const lines = allEntries.map((e) => canonicalJson(e));
  writeIndexAtomically(indexPath, lines);

  return { added: newEntries.length, total: allEntries.length };
}

/**
 * Find all observation files under the sent directory
 */
function findSentObservations(sentDir) {
  const observations = [];

  try {
    const nodes = readdirSync(sentDir, { withFileTypes: true });
    for (const node of nodes) {
      if (!node.isDirectory()) continue;

      const nodePath = join(sentDir, node.name);
      const principals = readdirSync(nodePath, { withFileTypes: true });

      for (const principal of principals) {
        if (!principal.isDirectory()) continue;

        const principalPath = join(nodePath, principal.name);
        const files = readdirSync(principalPath, { withFileTypes: true });

        for (const file of files) {
          if (!file.isFile() || !file.name.endsWith(".json")) continue;

          observations.push(join(principalPath, file.name));
        }
      }
    }
  } catch (error) {
    if (error.code !== "ENOENT") {
      throw error;
    }
  }

  return observations;
}

/**
 * Read and validate a single observation file
 */
function readObservation(path) {
  try {
    const content = readFileSync(path, { encoding: "utf-8" });
    const observation = JSON.parse(content);

    if (observation.schema_version !== "skcounter.snapshot.v1") {
      return null;
    }

    return observation;
  } catch (error) {
    return null;
  }
}

/**
 * Rebuild the index from the sent observation directory
 * This produces a deterministic index from existing observations
 */
export function rebuildIndex({ indexRoot, indexDir, sentDir } = {}) {
  const root = indexRoot || defaultIndexRoot();
  const dir = indexDir || root;
  const sent = sentDir || join(root, "sent");

  mkdirSync(dir, { mode: 0o700, recursive: true });

  const observations = findSentObservations(sent);
  const indexMap = new Map();
  let validCount = 0;
  let malformedCount = 0;

  // Process all observations in a deterministic order
  for (const path of observations.sort()) {
    const observation = readObservation(path);
    if (!observation) {
      malformedCount++;
      continue;
    }

    validCount++;

    try {
      const keys = deriveKey(observation);

      for (const key of keys) {
        const existing = indexMap.get(key);
        if (!existing || observation.observed_at > existing.observed_at) {
          const entry = {
            schema_version: INDEX_SCHEMA_VERSION,
            key: key,
            observation_path: extractRelativePath(path, root) || path,
            observed_at: observation.observed_at,
            payload_hash: observation.payload_hash,
            idempotency_key: observation.idempotency_key,
          };
          indexMap.set(key, entry);
        }
      }
    } catch (error) {
      malformedCount++;
      continue;
    }
  }

  // Enforce entry limit
  const allEntries = Array.from(indexMap.values());
  allEntries.sort((a, b) => a.key.localeCompare(b.key));

  if (allEntries.length > MAX_INDEX_ENTRIES) {
    const uniqueByKey = new Map();
    for (const entry of allEntries.reverse()) {
      if (!uniqueByKey.has(entry.key)) {
        uniqueByKey.set(entry.key, entry);
        if (uniqueByKey.size >= MAX_INDEX_ENTRIES) break;
      }
    }
    allEntries.length = 0;
    allEntries.push(...Array.from(uniqueByKey.values()).reverse());
  }

  const indexPath = join(dir, INDEX_FILE_NAME);
  const lines = allEntries.map((e) => canonicalJson(e));

  // Write atomically
  writeIndexAtomically(indexPath, lines);

  return {
    total_entries: allEntries.length,
    valid_observations: validCount,
    malformed_observations: malformedCount,
    total_files_processed: observations.length,
  };
}

/**
 * Query the index for the latest observation matching criteria
 */
export function queryLatestIndex({ indexRoot, indexDir, lane, node, principal, view, bucket } = {}) {
  const dir = indexDir || indexRoot || defaultIndexRoot();
  const indexMap = readIndex(dir);

  const results = [];

  for (const [key, entry] of indexMap) {
    const parts = key.split(":");

    if (lane && parts[0] !== lane) continue;
    if (node && parts[1] !== node) continue;
    if (principal && parts[2] !== principal) continue;
    if (view && parts[3] !== view) continue;
    if (bucket && parts[4] !== bucket) continue;

    results.push({ key, ...entry });
  }

  return results;
}

/**
 * Get the absolute path to an observation from the index
 */
export function resolveObservationPath(entry, { indexRoot } = {}) {
  const root = indexRoot || defaultIndexRoot();

  if (entry.observation_path.startsWith("/")) {
    return entry.observation_path;
  }

  return join(root, "observations", entry.observation_path);
}
