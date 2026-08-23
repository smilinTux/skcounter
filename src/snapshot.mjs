import { createHash } from "node:crypto";
import { hostname } from "node:os";

import { SKCOUNTER_VERSION } from "./constants.mjs";

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonicalize(value[key])]),
    );
  }
  return value;
}

export function canonicalJson(value) {
  return JSON.stringify(canonicalize(value));
}

export function digest(value) {
  return createHash("sha256").update(canonicalJson(value)).digest("hex");
}

function parseJsonReport(result, name) {
  if (result.code !== 0) {
    throw new Error(`${name} collection failed: ${result.error || `exit ${result.code}`}`);
  }
  try {
    const parsed = JSON.parse(result.stdout);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("report is not an object");
    }
    return parsed;
  } catch (error) {
    throw new Error(`${name} returned invalid JSON: ${error.message}`);
  }
}

function valueOrZero(value) {
  return Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) : 0;
}

function integerOrZero(value) {
  return Math.floor(valueOrZero(value));
}

function normalizedTokens(source, explicitTotal) {
  const tokens = {
    input: integerOrZero(source?.input),
    output: integerOrZero(source?.output),
    cache_read: integerOrZero(source?.cacheRead),
    cache_write: integerOrZero(source?.cacheWrite),
    reasoning: integerOrZero(source?.reasoning),
    total: 0,
  };
  const calculated =
    tokens.input + tokens.output + tokens.cache_read + tokens.cache_write + tokens.reasoning;
  tokens.total = explicitTotal == null ? calculated : integerOrZero(explicitTotal);
  return tokens;
}

function estimatedCost(amount, backendVersion) {
  return {
    amount: valueOrZero(amount),
    currency: "USD",
    estimated: true,
    pricing_revision: `tokscale-${backendVersion}-runtime-catalog-unpinned`,
  };
}

function dayStart(value) {
  if (!DATE_PATTERN.test(value || "")) throw new Error(`invalid contribution date '${value}'`);
  return `${value}T00:00:00Z`;
}

function hourStart(value) {
  if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(value || "")) {
    throw new Error(`invalid hourly bucket '${value}'`);
  }
  return `${value.replace(" ", "T")}:00Z`;
}

function graphAggregates(graph, backendVersion) {
  const contributions = Array.isArray(graph.contributions) ? graph.contributions : [];
  const rows = [];
  for (const contribution of contributions) {
    const bucket = dayStart(contribution.date);
    const breakdown = contribution.tokenBreakdown || {};
    rows.push({
      view: "daily",
      bucket_start: bucket,
      tokens: normalizedTokens(breakdown, contribution.totals?.tokens),
      message_count: integerOrZero(contribution.totals?.messages),
      cost: estimatedCost(contribution.totals?.cost, backendVersion),
    });

    for (const client of Array.isArray(contribution.clients) ? contribution.clients : []) {
      rows.push({
        view: "models",
        bucket_start: bucket,
        client: String(client.client || "unknown"),
        provider: String(client.providerId || "unknown"),
        model: String(client.modelId || "unknown"),
        tokens: normalizedTokens(client.tokens),
        message_count: integerOrZero(client.messages),
        cost: estimatedCost(client.cost, backendVersion),
      });
    }
  }

  const metrics = graph.timeMetrics || {};
  const firstDate = graph.meta?.dateRange?.start || new Date().toISOString().slice(0, 10);
  rows.push({
    view: "time_metrics",
    bucket_start: dayStart(firstDate),
    tokens: normalizedTokens({}),
    message_count: 0,
    activity: {
      active_seconds: Math.floor(valueOrZero(metrics.totalActiveTimeMs) / 1000),
      longest_continuous_seconds: Math.floor(valueOrZero(metrics.longestContinuousMs) / 1000),
      max_concurrent: integerOrZero(metrics.maxConcurrentSessions),
    },
  });
  return rows;
}

function hourlyAggregates(hourly, backendVersion) {
  return (Array.isArray(hourly.entries) ? hourly.entries : []).map((entry) => ({
    view: "hourly",
    bucket_start: hourStart(entry.hour),
    tokens: normalizedTokens(entry),
    message_count: integerOrZero(entry.messageCount),
    cost: estimatedCost(entry.cost, backendVersion),
  }));
}

function dateArguments(options) {
  const args = [];
  if (options.since) args.push("--since", options.since);
  if (options.until) args.push("--until", options.until);
  return args;
}

export function parseCollectionOptions(argv, now = new Date()) {
  const options = { since: "", until: "", outputDir: "" };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    const mapping = { "--since": "since", "--until": "until", "--output-dir": "outputDir" };
    if (!(argument in mapping)) throw new Error(`unknown collection option '${argument}'`);
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`${argument} requires a value`);
    options[mapping[argument]] = value;
    index += 1;
  }
  if (!options.since) {
    const start = new Date(now);
    start.setUTCDate(start.getUTCDate() - 29);
    options.since = start.toISOString().slice(0, 10);
  }
  for (const field of ["since", "until"]) {
    if (options[field] && !DATE_PATTERN.test(options[field])) {
      throw new Error(`--${field} must use YYYY-MM-DD`);
    }
  }
  if (options.until && options.since > options.until) {
    throw new Error("--since cannot be after --until");
  }
  return options;
}

export function collectSnapshot({
  backend,
  options,
  now = new Date(),
  nodeId = hostname(),
  principalId = process.env.SKCOUNTER_PRINCIPAL_ID || process.env.USER || process.env.LOGNAME,
  bucketTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
}) {
  if (!backend?.capture) throw new Error("selected backend does not implement governed collection");
  if (!principalId) throw new Error("SKCOUNTER_PRINCIPAL_ID or USER is required");

  const rangeArgs = dateArguments(options);
  const graph = parseJsonReport(
    backend.capture(["graph", ...rangeArgs, "--no-spinner"]),
    "graph",
  );
  const hourly = parseJsonReport(
    backend.capture(["hourly", "--json", ...rangeArgs, "--no-spinner"]),
    "hourly",
  );
  const observedAt = new Date(now).toISOString();
  const start = graph.meta?.dateRange?.start || options.since;
  const end = graph.meta?.dateRange?.end || options.until || observedAt.slice(0, 10);
  if (!DATE_PATTERN.test(start) || !DATE_PATTERN.test(end)) {
    throw new Error("backend returned an invalid collection window");
  }

  const sourceStateDigest = digest({ graph, hourly });
  const base = {
    schema_version: "skcounter.snapshot.v1",
    measurement_lane: "harness_reported",
    node_id: String(nodeId),
    principal_id: String(principalId),
    collector: {
      product: "skcounter",
      facade_version: SKCOUNTER_VERSION,
      backend: backend.id,
      backend_version: backend.version,
    },
    observed_at: observedAt,
    bucket_timezone: bucketTimezone,
    window: {
      start: `${start}T00:00:00Z`,
      end: `${end}T23:59:59Z`,
    },
    source_state_digest: sourceStateDigest,
    aggregates: [
      ...graphAggregates(graph, backend.version),
      ...hourlyAggregates(hourly, backend.version),
    ],
  };
  const payloadHash = digest(base);
  return {
    ...base,
    idempotency_key: payloadHash,
    payload_hash: payloadHash,
  };
}
