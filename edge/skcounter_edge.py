#!/usr/bin/env python3
"""Private edge collection and signed aggregate delivery for SKCounter."""

from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


MAX_OBSERVATION_BYTES = 1_048_576
CONFIG_SCHEMA = "skcounter.edge.config.v1"
SAFE_ID = re.compile(r"^[A-Za-z0-9._@-]{1,128}$")


class EdgeError(RuntimeError):
    """An expected edge collection or delivery failure."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema_version") != CONFIG_SCHEMA:
        raise EdgeError("unsupported edge config schema")
    required = (
        "collector_url",
        "ca_file",
        "state_dir",
        "skcounter_bin",
        "capauth_home",
        "gnupg_home",
        "node_id",
        "principal_id",
        "subject",
    )
    if any(not config.get(field) for field in required):
        raise EdgeError("edge config is missing a required field")
    if not str(config["collector_url"]).startswith("https://"):
        raise EdgeError("collector_url must use HTTPS")
    scope = config.get("scope", "skcounter.report.submit")
    lane = config.get("measurement_lane", "harness_reported")
    allowed_bindings = {
        "skcounter.report.submit": "harness_reported",
        "skcounter.gateway.submit": "gateway_observed",
    }
    if allowed_bindings.get(scope) != lane:
        raise EdgeError("edge scope and measurement lane are not allowed")
    if not SAFE_ID.fullmatch(str(config["node_id"])) or not SAFE_ID.fullmatch(str(config["principal_id"])):
        raise EdgeError("edge node or principal is invalid")
    if config["subject"] != f"skcounter:{config['node_id']}:{config['principal_id']}":
        raise EdgeError("edge subject does not match node and principal")
    return config


def _mint_wire_token(config: dict[str, Any]) -> str:
    os.environ["GNUPGHOME"] = str(config["gnupg_home"])
    try:
        from capauth.tokens import export_token, mint_audience_token

        token = mint_audience_token(
            Path(config["capauth_home"]),
            str(config["subject"]),
            "skcounter",
            [str(config.get("scope", "skcounter.report.submit"))],
            ttl_hours=1,
            metadata={
                "node_id": str(config["node_id"]),
                "principal_id": str(config["principal_id"]),
            },
            sign=True,
            store=False,
        )
        exported = export_token(token).encode("utf-8")
    except Exception as exc:
        raise EdgeError("CapAuth token mint failed") from exc
    return base64.urlsafe_b64encode(exported).rstrip(b"=").decode("ascii")


def _collect(config: dict[str, Any], outbox: Path) -> None:
    if config.get("measurement_lane", "harness_reported") != "harness_reported":
        raise EdgeError("local harness collection is not allowed for this lane")
    command = [str(config["skcounter_bin"]), "collect", "--output-dir", str(outbox)]
    if config.get("since"):
        command.extend(["--since", str(config["since"])])
    environment = {
        **os.environ,
        "SKCOUNTER_NODE_ID": str(config["node_id"]),
        "SKCOUNTER_PRINCIPAL_ID": str(config["principal_id"]),
    }
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=int(config.get("collection_timeout_seconds", 120)),
        env=environment,
    )
    if result.returncode != 0:
        raise EdgeError("local aggregate collection failed")


def _observation_files(outbox: Path) -> list[Path]:
    if not outbox.exists():
        return []
    return sorted(path for path in outbox.rglob("*.json") if path.is_file() and not path.is_symlink())


def _read_observation(path: Path) -> tuple[bytes, dict[str, Any]]:
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise EdgeError("outbox observation permissions are not private")
    size = path.stat().st_size
    if size < 2 or size > MAX_OBSERVATION_BYTES:
        raise EdgeError("outbox observation size is invalid")
    body = path.read_bytes()
    try:
        observation = json.loads(body)
    except json.JSONDecodeError as exc:
        raise EdgeError("outbox observation is malformed") from exc
    if observation.get("schema_version") != "skcounter.snapshot.v1":
        raise EdgeError("outbox observation schema is unsupported")
    return body, observation


def _post(
    config: dict[str, Any],
    body: bytes,
    observation: dict[str, Any],
    *,
    token_minter: Callable[[dict[str, Any]], str] = _mint_wire_token,
    opener: Callable[..., Any] = urllib.request.urlopen,
    now: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    if observation.get("node_id") != config["node_id"] or observation.get("principal_id") != config["principal_id"]:
        raise EdgeError("outbox observation identity does not match edge config")
    if observation.get("measurement_lane") != config.get("measurement_lane", "harness_reported"):
        raise EdgeError("outbox observation lane does not match edge config")
    idempotency_key = observation.get("idempotency_key")
    if not isinstance(idempotency_key, str) or len(idempotency_key) != 64:
        raise EdgeError("outbox observation idempotency key is invalid")
    request = urllib.request.Request(
        str(config["collector_url"]),
        data=body,
        method="POST",
        headers={
            "Authorization": f"CapAuth {token_minter(config)}",
            "Content-Type": "application/json",
            "X-SKCounter-Idempotency-Key": idempotency_key,
            "X-SKCounter-Request-Id": secrets.token_hex(16),
            "X-SKCounter-Sent-At": now().isoformat().replace("+00:00", "Z"),
        },
    )
    context = ssl.create_default_context(cafile=str(config["ca_file"]))
    try:
        with opener(request, timeout=int(config.get("request_timeout_seconds", 15)), context=context) as response:
            if response.status != 200:
                raise EdgeError("collector rejected observation")
            ack = json.loads(response.read(16_384))
    except (urllib.error.URLError, TimeoutError, ssl.SSLError, json.JSONDecodeError) as exc:
        raise EdgeError("collector delivery failed") from exc
    if (
        ack.get("schema_version") != "skcounter.ack.v1"
        or ack.get("idempotency_key") != idempotency_key
        or ack.get("payload_hash") != observation.get("payload_hash")
    ):
        raise EdgeError("collector acknowledgement is invalid")
    return ack


def _archive(path: Path, sent_root: Path, observation: dict[str, Any]) -> None:
    destination_dir = sent_root / str(observation["node_id"]) / str(observation["principal_id"])
    _private_dir(destination_dir)
    destination = destination_dir / path.name
    if destination.exists():
        if destination.read_bytes() != path.read_bytes():
            raise EdgeError("sent archive conflict")
        path.unlink()
    else:
        os.replace(path, destination)
        destination.chmod(0o600)
    return destination


def _update_latest_index(config: dict[str, Any], observation_path: Path, observation: dict[str, Any]) -> None:
    """Update the latest observation index after successful delivery."""
    index_path = Path(config["state_dir"]) / "latest-observation-index.jsonl"

    # Derive index keys from the observation
    keys = _derive_index_keys(observation)

    # Read current index
    current_index: dict[str, dict[str, Any]] = {}
    if index_path.exists():
        try:
            for line in index_path.read_text(encoding="utf-8").strip().split("\n"):
                if line:
                    entry = json.loads(line)
                    if entry.get("schema_version") == "skcounter.latest-observation-index.v1":
                        key = entry["key"]
                        # Keep only the latest entry per key
                        if key not in current_index or entry["observed_at"] > current_index[key]["observed_at"]:
                            current_index[key] = entry
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    # Update with new entries
    sent_dir = Path(config["state_dir"]) / "sent"
    for key in keys:
        entry = {
            "schema_version": "skcounter.latest-observation-index.v1",
            "key": key,
            "observation_path": str(observation_path.relative_to(sent_dir)),
            "observed_at": observation["observed_at"],
            "payload_hash": observation["payload_hash"],
            "idempotency_key": observation["idempotency_key"],
        }
        if key not in current_index or entry["observed_at"] > current_index[key]["observed_at"]:
            current_index[key] = entry

    # Enforce 10,000 entry limit
    if len(current_index) > 10000:
        # Keep most recent by observed_at
        sorted_entries = sorted(current_index.values(), key=lambda e: e["observed_at"], reverse=True)
        current_index = {e["key"]: e for e in sorted_entries[:10000]}

    # Write atomically
    _write_index_atomically(index_path, current_index)


def _derive_index_keys(observation: dict[str, Any]) -> list[str]:
    """Derive index keys for an observation's aggregates."""
    keys = []
    lane = observation.get("measurement_lane", "unknown")
    node = observation.get("node_id", "unknown")
    principal = observation.get("principal_id", "unknown")

    for agg in observation.get("aggregates", []):
        view = agg.get("view", "unknown")
        bucket = agg.get("bucket_start", "unknown")
        key = f"{lane}:{node}:{principal}:{view}:{bucket}"
        keys.append(key)

    return keys


def _write_index_atomically(index_path: Path, index_data: dict[str, dict[str, Any]]) -> None:
    """Write index atomically using temp file then rename."""
    tmp_path = index_path.with_name(f".{index_path.name}.{os.getpid()}.tmp")

    lines = [json.dumps(entry, sort_keys=True, separators=(",", ":")) for entry in sorted(index_data.values(), key=lambda e: e["key"])]
    tmp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp_path.chmod(0o600)
    os.replace(tmp_path, index_path)


def _prune(root: Path, days: int, now: datetime) -> int:
    if not root.exists():
        return 0
    cutoff = now.timestamp() - days * 86400
    removed = 0
    for path in root.rglob("*.json"):
        if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    return removed


def _write_status(path: Path, status: dict[str, Any]) -> None:
    _private_dir(path.parent)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(status, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def run_once(
    config: dict[str, Any],
    *,
    collect: bool = True,
    token_minter: Callable[[dict[str, Any]], str] = _mint_wire_token,
    opener: Callable[..., Any] = urllib.request.urlopen,
    now: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    state_dir = Path(config["state_dir"])
    outbox = state_dir / "outbox"
    sent = state_dir / "sent"
    _private_dir(state_dir)
    _private_dir(outbox)
    _private_dir(sent)
    lock_path = state_dir / "edge.lock"
    lock_path.touch(mode=0o600, exist_ok=True)
    lock_path.chmod(0o600)
    with lock_path.open("r+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise EdgeError("another edge collection is running") from exc

        collection_error = None
        if collect:
            try:
                _collect(config, outbox)
            except EdgeError as exc:
                collection_error = str(exc)

        acknowledged = 0
        failed = 0
        for path in _observation_files(outbox):
            try:
                body, observation = _read_observation(path)
                delays = list(config.get("retry_delays_seconds", [1, 2]))
                while True:
                    try:
                        _post(config, body, observation, token_minter=token_minter, opener=opener, now=now)
                        archived_path = _archive(path, sent, observation)
                        acknowledged += 1
                        # Successfully delivered and archived, now update index
                        try:
                            _update_latest_index(config, archived_path, observation)
                        except Exception:
                            # Index update failure is non-fatal; log but continue
                            pass
                        break
                    except EdgeError:
                        if not delays:
                            raise
                        time.sleep(float(delays.pop(0)))
            except EdgeError:
                failed += 1

        pruned = _prune(sent, int(config.get("sent_retention_days", 7)), now())
        status = {
            "schema_version": "skcounter.edge.status.v1",
            "completed_at": now().isoformat().replace("+00:00", "Z"),
            "node_id": config["node_id"],
            "principal_id": config["principal_id"],
            "acknowledged": acknowledged,
            "failed": failed,
            "pending": len(_observation_files(outbox)),
            "sent_pruned": pruned,
            "collection_ok": collection_error is None,
        }
        _write_status(state_dir / "status.json", status)
        return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skcounter-edge")
    parser.add_argument("command", choices=("run", "drain", "check-config", "status"))
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "check-config":
            print("SKCounter edge configuration valid")
            return 0
        if args.command == "status":
            status_path = Path(config["state_dir"]) / "status.json"
            print(status_path.read_text(encoding="utf-8").strip() if status_path.exists() else json.dumps({"status": "never_run"}))
            return 0
        status = run_once(config, collect=args.command == "run")
        print(json.dumps(status, sort_keys=True, separators=(",", ":")))
        return 0 if status["failed"] == 0 and status["collection_ok"] else 75
    except (EdgeError, OSError, ValueError) as exc:
        print(f"SKCounter edge error: {exc}", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
