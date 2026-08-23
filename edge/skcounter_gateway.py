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
    parser.add_argument("command", choices=("run", "check-config", "status"))
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
