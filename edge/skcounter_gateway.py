#!/usr/bin/env python3
"""Collect privacy-safe SKGateway aggregates into a separate SKCounter lane."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from .skcounter_edge import EdgeError, _private_dir, load_config, run_once
except ImportError:
    from skcounter_edge import EdgeError, _private_dir, load_config, run_once


MAX_GATEWAY_RESPONSE_BYTES = 2_097_152
DATE_BUCKET = re.compile(r"^\d{4}-\d{2}-\d{2}$")

OBSERVATION_V2_SCHEMA = "skcounter.gateway_observation.v2"
OBSERVATION_V2_PATHS = (
    "/api/stats",
    "/api/health",
    "/api/tokens",
    "/api/costs",
    "/api/events",
    "/api/activity",
)
MAX_LATENCY_KEYS = 256
MAX_BREAKDOWN_ENTRIES = 256
MAX_HEALTH_BACKENDS = 64
MAX_EVENT_TYPES = 32
RECENT_WINDOW_SECONDS = 300

# Provider and rail inference mirrors src/proxy/router.mjs in SKGateway so
# the collector and the gateway never disagree about a backend label.
def _infer_provider(backend_id: str) -> str | None:
    lowered = backend_id.lower()
    for provider in (
        "nvidia",
        "anthropic",
        "ollama",
        "openrouter",
        "zai",
        "codex",
    ):
        if provider in lowered:
            return provider
    if "chiap" in lowered or "ornith" in lowered or "qwen" in lowered or lowered == "local":
        return "local"
    return None


def _infer_rail(backend_id: str, provider: str | None) -> str | None:
    if provider == "local":
        return "local"
    if provider in {"nvidia", "anthropic", "openrouter", "zai"}:
        return "cloud"
    return "cloud" if provider else None


def _unavailable(reason: str) -> dict[str, str]:
    return {"unavailable": reason}


def _quantize(value: Any) -> Any:
    """Make numbers hash-identical across Python and JavaScript canonical JSON.

    Integral floats become integers so 3.0 serializes as 3 in both languages,
    and every float is pinned to six decimals before hashing.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        rounded = round(value, 6)
        return int(rounded) if rounded.is_integer() else rounded
    if isinstance(value, dict):
        return {key: _quantize(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_quantize(child) for child in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _integer(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise EdgeError("SKGateway aggregate contains an invalid integer") from exc
    if number < 0:
        raise EdgeError("SKGateway aggregate contains a negative integer")
    return number


def _bounded(value: Any, fallback: str, maximum: int) -> str:
    text = str(value or fallback)
    if not 1 <= len(text) <= maximum:
        raise EdgeError("SKGateway aggregate contains an invalid dimension")
    return text


def _gateway_url(config: dict[str, Any]) -> str:
    value = str(config.get("gateway_metrics_url", ""))
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise EdgeError("SKGateway metrics URL must use loopback HTTP")
    if not parsed.path.startswith("/api/tokens"):
        raise EdgeError("SKGateway metrics URL must use the token aggregate API")
    return value


def _surface_url(config: dict[str, Any], path: str) -> str:
    base = urllib.parse.urlsplit(_gateway_url(config))
    return urllib.parse.urlunsplit(("http", base.netloc, path, "", ""))


def _fetch_json(
    config: dict[str, Any],
    path: str,
    *,
    opener: Callable[..., Any],
) -> Any | None:
    """Fetch one bounded loopback surface. None means the surface is unavailable."""
    request = urllib.request.Request(
        _surface_url(config, path), headers={"Accept": "application/json"}
    )
    try:
        with opener(request, timeout=int(config.get("request_timeout_seconds", 15))) as response:
            body = response.read(MAX_GATEWAY_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    if len(body) > MAX_GATEWAY_RESPONSE_BYTES:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number < 0 or number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _dimension(value: Any, maximum: int = 256) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        return None
    return value


def _latency_entries(latency: Any) -> dict[str, Any] | None:
    if not isinstance(latency, dict) or not latency:
        return None
    entries: dict[str, Any] = {}
    for position, (key, raw) in enumerate(sorted(latency.items())):
        if position >= MAX_LATENCY_KEYS or not isinstance(raw, dict):
            continue
        entry: dict[str, float | int] = {}
        for field in ("p50", "p95", "p99", "mean"):
            number = _number_or_none(raw.get(field))
            if number is not None:
                entry[field] = round(number)
        count = _count(raw.get("count"))
        if count is not None:
            entry["count"] = count
        if entry:
            entries[str(key)[:256]] = entry
    return entries or None


def _health_entries(health: Any) -> dict[str, Any] | None:
    if not isinstance(health, dict) or not health:
        return None
    entries: dict[str, Any] = {}
    for position, (backend, raw) in enumerate(sorted(health.items())):
        if position >= MAX_HEALTH_BACKENDS or not isinstance(raw, dict):
            continue
        entry: dict[str, Any] = {}
        status = _dimension(raw.get("status"), 64)
        if status:
            entry["status"] = status
        for field in ("errorRate", "latencyP50", "totalRequests", "totalErrors"):
            number = _number_or_none(raw.get(field))
            if number is not None:
                entry[field] = number
        if entry:
            entries[str(backend)[:256]] = entry
    return entries or None


def _type_histogram(records: Any) -> dict[str, Any] | None:
    if not isinstance(records, list):
        return None
    histogram: dict[str, int] = {}
    total = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        total += 1
        kind = _dimension(record.get("type") or record.get("kind"), 64)
        if kind:
            histogram[kind] = histogram.get(kind, 0) + 1
            if len(histogram) > MAX_EVENT_TYPES:
                return None
    return {"count": total, "by_type": dict(sorted(histogram.items()))}


def collect_gateway_observation(
    config: dict[str, Any],
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Emit one bounded v2 gateway observation from the reviewed safe surfaces.

    Every fact is either an observed value or an explicit unavailable reason.
    Facts with no safe HTTP surface stay unavailable instead of becoming zero.
    """
    surfaces = {path: _fetch_json(config, path, opener=opener) for path in OBSERVATION_V2_PATHS}
    if all(surface is None for surface in surfaces.values()):
        raise EdgeError("no SKGateway observation surface is reachable")

    stats = surfaces["/api/stats"]
    if not isinstance(stats, dict):
        stats = None
    tokens = surfaces["/api/tokens"]
    token_rows = tokens.get("rows") if isinstance(tokens, dict) else None
    if not isinstance(token_rows, list):
        token_rows = None

    observed = now().astimezone(timezone.utc)
    observed_text = observed.isoformat().replace("+00:00", "Z")

    requests: dict[str, Any] = {}
    if stats is None:
        requests["total"] = _unavailable("gateway_stats_surface_unavailable")
        requests["active_concurrency"] = _unavailable("gateway_stats_surface_unavailable")
        requests["error_count"] = _unavailable("gateway_stats_surface_unavailable")
        requests["recent_requests_5m"] = _unavailable("gateway_stats_surface_unavailable")
        requests["recent_errors_5m"] = _unavailable("gateway_stats_surface_unavailable")
        requests["rate_5m_per_second"] = _unavailable("gateway_stats_surface_unavailable")
    else:
        for target, source in (
            ("total", "totalRequests"),
            ("active_concurrency", "activeRequests"),
            ("error_count", "errorCount"),
            ("recent_requests_5m", "recentRequests5m"),
            ("recent_errors_5m", "recentErrors5m"),
        ):
            value = _count(stats.get(source))
            requests[target] = (
                value if value is not None else _unavailable(f"{source}_unavailable")
            )
        recent = _count(stats.get("recentRequests5m"))
        requests["rate_5m_per_second"] = (
            round(recent / RECENT_WINDOW_SECONDS, 6)
            if recent is not None
            else _unavailable("recentRequests5m_unavailable")
        )

    tokens_facts: dict[str, Any]
    if stats is None:
        tokens_facts = {"input": _unavailable("gateway_stats_surface_unavailable")}
    else:
        tokens_facts = {}
        for target, source in (
            ("input", "totalInputTokens"),
            ("output", "totalOutputTokens"),
        ):
            value = _count(stats.get(source))
            tokens_facts[target] = (
                value if value is not None else _unavailable(f"{source}_unavailable")
            )
        recent_tokens = _count(stats.get("recentTokens5m"))
        tokens_facts["throughput_5m_per_second"] = (
            round(recent_tokens / RECENT_WINDOW_SECONDS, 6)
            if recent_tokens is not None
            else _unavailable("recentTokens5m_unavailable")
        )

    cost_facts: dict[str, Any]
    if stats is None:
        cost_facts = {"total_usd": _unavailable("gateway_stats_surface_unavailable")}
    else:
        cost_facts = {}
        total = _number_or_none(stats.get("totalCostUsd"))
        cost_facts["total_usd"] = total if total is not None else _unavailable("totalCostUsd_unavailable")
        unpriced = _count(stats.get("unpricedRequests"))
        cost_facts["unpriced_requests"] = (
            unpriced if unpriced is not None else _unavailable("unpricedRequests_unavailable")
        )
        if total is None:
            cost_facts["truth"] = "unknown"
        elif unpriced:
            cost_facts["truth"] = "partial"
        else:
            cost_facts["truth"] = "actual"

    latency = _latency_entries(stats.get("latency")) if stats else None
    health = _health_entries(surfaces["/api/health"])

    models: list[str] = []
    providers: list[str] = []
    nodes: list[str] = []
    clients: list[str] = []
    rails: list[str] = []
    daily_tokens: list[dict[str, Any]] = []
    if token_rows is not None:
        seen_models: set[str] = set()
        seen_providers: set[str] = set()
        seen_nodes: set[str] = set()
        seen_clients: set[str] = set()
        seen_rails: set[str] = set()
        for row in token_rows[:10_000]:
            if not isinstance(row, dict):
                continue
            bucket = row.get("bucket")
            model = _dimension(row.get("model"))
            backend = _dimension(row.get("backend"))
            agent = _dimension(row.get("agent_id"), 128)
            provider = _infer_provider(backend) if backend else None
            rail = _infer_rail(backend, provider) if backend else None
            for value, seen, collector in (
                (model, seen_models, models),
                (provider, seen_providers, providers),
                (backend, seen_nodes, nodes),
                (agent, seen_clients, clients),
                (rail, seen_rails, rails),
            ):
                if value and value not in seen and len(collector) < MAX_BREAKDOWN_ENTRIES:
                    seen.add(value)
                    collector.append(value)
            if isinstance(bucket, str) and DATE_BUCKET.fullmatch(bucket):
                entry: dict[str, Any] = {"bucket": bucket}
                for target, source in (
                    ("input_tokens", "input_tokens"),
                    ("output_tokens", "output_tokens"),
                    ("cache_read_tokens", "cache_read_tokens"),
                    ("cache_write_tokens", "cache_write_tokens"),
                    ("request_count", "request_count"),
                ):
                    value = _count(row.get(source))
                    if value is not None:
                        entry[target] = value
                if model:
                    entry["model"] = model
                if backend:
                    entry["backend"] = backend
                if agent:
                    entry["agent"] = agent
                if len(daily_tokens) < MAX_BREAKDOWN_ENTRIES:
                    daily_tokens.append(entry)

    events = _type_histogram(surfaces["/api/events"].get("events") if isinstance(surfaces["/api/events"], dict) else None)
    activity = _type_histogram(surfaces["/api/activity"].get("activity") if isinstance(surfaces["/api/activity"], dict) else None)

    uptime = _count(stats.get("uptime")) if stats else None
    base = {
        "schema_version": OBSERVATION_V2_SCHEMA,
        "measurement_lane": "gateway_observed",
        "node_id": str(config["node_id"]),
        "principal_id": str(config["principal_id"]),
        "collector": {
            "product": "skcounter",
            "facade_version": str(config.get("package_version", "0.2.0")),
            "backend": "skgateway",
            "backend_version": str(config.get("gateway_version", "unknown")),
        },
        "observed_at": observed_text,
        "bucket_timezone": str(config.get("bucket_timezone", "UTC")),
        "window": {
            "start": observed.replace(hour=0, minute=0, second=0, microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "end": observed_text,
        },
        "source_state_digest": _digest(surfaces),
        "facts": {
            "gateway": {
                "uptime_seconds": (
                    uptime if uptime is not None else _unavailable("gateway_stats_surface_unavailable")
                ),
                "backend_health": health or _unavailable("gateway_health_surface_unavailable"),
            },
            "requests": requests,
            "latency_ms": latency or _unavailable("latency_percentiles_unavailable"),
            "queue": {
                "wait_ms_percentiles": _unavailable(
                    "gateway_surface_does_not_expose_queue_telemetry"
                ),
                "admission_outcomes": _unavailable(
                    "gateway_surface_does_not_expose_queue_telemetry"
                ),
            },
            "rate_limits": {
                "http_429_count": _unavailable(
                    "gateway_surface_does_not_expose_rate_limit_counts"
                ),
            },
            "tokens": tokens_facts,
            "generation": {
                "throughput_tokens_per_second": _unavailable(
                    "gateway_stats_surface_does_not_expose_generation_throughput"
                ),
            },
            "cost": cost_facts,
            "breakdowns": {
                "models": sorted(models),
                "providers": sorted(providers),
                "nodes": sorted(nodes),
                "clients": sorted(clients),
                "apps": _unavailable(
                    "gateway_surface_does_not_expose_application_attribution"
                ),
                "rails": sorted(rails),
            },
            "daily_token_rows": daily_tokens,
            "events": events or _unavailable("gateway_events_surface_unavailable"),
            "activity": activity or _unavailable("gateway_activity_surface_unavailable"),
        },
    }
    quantized = _quantize(base)
    payload_hash = _digest(quantized)
    return {**quantized, "idempotency_key": payload_hash, "payload_hash": payload_hash}


def collect_gateway_snapshot(
    config: dict[str, Any],
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    request = urllib.request.Request(_gateway_url(config), headers={"Accept": "application/json"})
    try:
        with opener(request, timeout=int(config.get("request_timeout_seconds", 15))) as response:
            body = response.read(MAX_GATEWAY_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EdgeError("SKGateway aggregate collection failed") from exc
    if len(body) > MAX_GATEWAY_RESPONSE_BYTES:
        raise EdgeError("SKGateway aggregate response is too large")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise EdgeError("SKGateway aggregate response is malformed") from exc
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) > 10_000:
        raise EdgeError("SKGateway aggregate rows are invalid")

    selected_rows = []
    aggregates = []
    buckets = []
    for row in rows:
        if not isinstance(row, dict) or not DATE_BUCKET.fullmatch(str(row.get("bucket", ""))):
            raise EdgeError("SKGateway aggregate bucket is invalid")
        bucket = str(row["bucket"])
        buckets.append(bucket)
        selected = {
            "bucket": bucket,
            "agent": _bounded(row.get("agent_id"), "anonymous", 128),
            "model": _bounded(row.get("model"), "unknown", 256),
            "provider": _bounded(row.get("backend"), "unknown", 128),
            "input": _integer(row.get("input_tokens", 0)),
            "output": _integer(row.get("output_tokens", 0)),
            "cache_read": _integer(row.get("cache_read_tokens", 0)),
            "cache_write": _integer(row.get("cache_write_tokens", 0)),
            "messages": _integer(row.get("request_count", 0)),
        }
        selected_rows.append(selected)
        tokens = {
            "input": selected["input"],
            "output": selected["output"],
            "cache_read": selected["cache_read"],
            "cache_write": selected["cache_write"],
            "reasoning": 0,
            "total": selected["input"] + selected["output"] + selected["cache_read"] + selected["cache_write"],
        }
        aggregates.append(
            {
                "view": "models",
                "bucket_start": f"{bucket}T00:00:00Z",
                "client": "skgateway",
                "provider": selected["provider"],
                "model": selected["model"],
                "agent": selected["agent"],
                "tokens": tokens,
                "message_count": selected["messages"],
            }
        )

    observed = now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    today = observed[:10]
    start = min(buckets) if buckets else today
    end = max(buckets) if buckets else today
    base = {
        "schema_version": "skcounter.snapshot.v1",
        "measurement_lane": "gateway_observed",
        "node_id": str(config["node_id"]),
        "principal_id": str(config["principal_id"]),
        "collector": {
            "product": "skcounter",
            "facade_version": str(config.get("package_version", "0.2.0")),
            "backend": "skgateway",
            "backend_version": str(config.get("gateway_version", "unknown")),
        },
        "observed_at": observed,
        "bucket_timezone": str(config.get("bucket_timezone", "UTC")),
        "window": {"start": f"{start}T00:00:00Z", "end": f"{end}T23:59:59Z"},
        "source_state_digest": _digest(selected_rows),
        "aggregates": aggregates,
    }
    payload_hash = _digest(base)
    return {**base, "idempotency_key": payload_hash, "payload_hash": payload_hash}


def _write_outbox(config: dict[str, Any], snapshot: dict[str, Any]) -> Path:
    root = Path(config["state_dir"]) / "outbox" / str(config["node_id"]) / str(config["principal_id"])
    _private_dir(root)
    path = root / f"{snapshot['idempotency_key']}.json"
    body = _canonical_json(snapshot) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != body:
            raise EdgeError("SKGateway outbox conflict")
        return path
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(body, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skcounter-gateway")
    parser.add_argument("command", choices=("run", "observe", "check-config", "status"))
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        _gateway_url(config)
        if config.get("measurement_lane") != "gateway_observed":
            raise EdgeError("SKGateway adapter requires the gateway measurement lane")
        if args.command == "check-config":
            print("SKCounter gateway configuration valid")
            return 0
        if args.command == "status":
            status_path = Path(config["state_dir"]) / "status.json"
            print(status_path.read_text(encoding="utf-8").strip() if status_path.exists() else json.dumps({"status": "never_run"}))
            return 0
        if args.command == "observe":
            observation = collect_gateway_observation(config)
            _write_outbox(config, observation)
            status = run_once(config, collect=False)
            print(_canonical_json(status))
            return 0 if status["failed"] == 0 else 75
        snapshot = collect_gateway_snapshot(config)
        _write_outbox(config, snapshot)
        status = run_once(config, collect=False)
        print(_canonical_json(status))
        return 0 if status["failed"] == 0 else 75
    except (EdgeError, OSError, ValueError) as exc:
        print(f"SKCounter gateway error: {exc}", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
