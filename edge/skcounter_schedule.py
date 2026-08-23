#!/usr/bin/env python3
"""Observable wrapper for the scheduled SKCounter edge job."""

from __future__ import annotations

import fcntl
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def _append_ledger(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    with path.open("a", encoding="utf-8") as ledger:
        os.fchmod(ledger.fileno(), 0o600)
        fcntl.flock(ledger, fcntl.LOCK_EX)
        ledger.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        ledger.flush()
        os.fsync(ledger.fileno())


def _capture_failure(host: str, completed: datetime, returncode: int, job: str) -> None:
    payload = {
        "message": "SKCounter scheduled collection failed",
        "host": host,
        "job": job,
        "exit": returncode,
        "completed_at": completed.isoformat().replace("+00:00", "Z"),
    }
    try:
        from skcapstone.sdk import alert

        alert("skcounter.critical", payload, level="critical", notify=True)
    except Exception:
        pass

    command = Path.home() / ".skenv" / "bin" / "skcapstone"
    if command.exists():
        source_ref = f"cron:{job}:{host}:{completed.strftime('%Y%m%d%H')}"
        subprocess.run(
            [
                str(command),
                "gtd",
                "capture",
                f"Investigate {job} collection failure on {host}, exit {returncode}",
                "--source",
                "manual",
                "--privacy",
                "private",
                "--context",
                "@computer",
                "--source-ref",
                source_ref,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )


def main(argv: list[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    if len(arguments) != 1:
        print("Usage: skcounter_schedule.py EDGE_CONFIG", file=sys.stderr)
        return 2
    config_path = Path(arguments[0])
    config = json.loads(config_path.read_text(encoding="utf-8"))
    state_dir = Path(config["state_dir"])
    runner_name = str(config.get("scheduled_runner", "skcounter_edge.py"))
    if runner_name not in {"skcounter_edge.py", "skcounter_gateway.py"}:
        print("SKCounter scheduled runner is not allowed", file=sys.stderr)
        return 2
    edge = Path(__file__).with_name(runner_name)
    job = str(config.get("scheduled_job", "skcounter-edge"))
    started = datetime.now(timezone.utc)
    monotonic_start = time.monotonic()
    stdout = ""
    try:
        result = subprocess.run(
            [sys.executable, str(edge), "run", "--config", str(config_path)],
            capture_output=True,
            text=True,
            timeout=int(config.get("scheduled_timeout_seconds", 300)),
            check=False,
        )
        returncode = result.returncode
        stdout = result.stdout
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        output = (exc.stdout or "") + (exc.stderr or "") + "\nSKCounter edge timed out"
    completed = datetime.now(timezone.utc)
    tail = output.strip()[-2048:]
    try:
        edge_status = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
    except (json.JSONDecodeError, IndexError):
        edge_status = {}
    fleet_error = None
    if config.get("fleet_object"):
        try:
            from skcounter_fleet import publish_fleet_status

            publish_fleet_status(config, edge_status, returncode)
        except Exception as exc:
            fleet_error = str(exc)
            if config.get("fleet_status_required", False):
                returncode = 75
                tail = (tail + f"\nSKCounter Fleet status error: {fleet_error}").strip()[-2048:]
    record = {
        "job": job,
        "host": socket.gethostname(),
        "start": started.isoformat().replace("+00:00", "Z"),
        "dur_s": round(time.monotonic() - monotonic_start, 3),
        "exit": returncode,
        "ok": returncode == 0,
        "tail": tail,
        "fleet_ok": fleet_error is None,
    }
    _append_ledger(state_dir / "run-ledger.jsonl", record)
    if returncode != 0:
        _capture_failure(record["host"], completed, returncode, job)
    if tail:
        print(tail)
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
