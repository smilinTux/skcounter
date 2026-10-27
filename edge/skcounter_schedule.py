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
from typing import Callable


def _append_ledger(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    with path.open("a", encoding="utf-8") as ledger:
        os.fchmod(ledger.fileno(), 0o600)
        fcntl.flock(ledger, fcntl.LOCK_EX)
        ledger.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        ledger.flush()
        os.fsync(ledger.fileno())


INCIDENT_FILE = "failure-incident.json"
DEFAULT_CAPTURE_AFTER_RUNS = 3


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _read_incident(path: Path) -> dict | None:
    try:
        incident = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return incident if isinstance(incident, dict) and incident.get("started") else None


def _write_incident(path: Path, incident: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(incident, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _track_incident(
    state_dir: Path,
    *,
    host: str,
    job: str,
    returncode: int,
    completed: datetime,
    capture_after_runs: int,
    capture: Callable[[str, dict, int, str], bool],
) -> None:
    """Report one GTD item per failure incident, not one per run or per hour.

    An incident starts at the first failed run and ends at the next successful
    one. It is reported once, after ``capture_after_runs`` consecutive failures,
    so a single transient blip (a collector restart, a slow network) that heals
    on the next run never files anything. The GTD dedupe key is the incident
    start, so a failure that lasts for days is still exactly one item.
    """
    path = state_dir / INCIDENT_FILE
    incident = _read_incident(path)
    if returncode == 0:
        if incident is not None:
            path.unlink(missing_ok=True)
        return
    if incident is None:
        incident = {"started": _iso(completed), "runs": 0, "captured": False}
    incident["runs"] = int(incident.get("runs", 0)) + 1
    incident["last_exit"] = returncode
    incident["last_failed_at"] = _iso(completed)
    if not incident.get("captured") and incident["runs"] >= max(1, capture_after_runs):
        incident["captured"] = capture(host, incident, returncode, job)
    _write_incident(path, incident)


def _capture_failure(host: str, incident: dict, returncode: int, job: str) -> bool:
    """File the incident in GTD; True once the capture is known to have landed."""
    payload = {
        "message": "SKCounter scheduled collection failing",
        "host": host,
        "job": job,
        "exit": returncode,
        "incident_started": incident["started"],
        "consecutive_failures": incident["runs"],
    }
    try:
        from skcapstone.sdk import alert

        alert("skcounter.critical", payload, level="critical", notify=True)
    except Exception:
        pass

    command = Path.home() / ".skenv" / "bin" / "skcapstone"
    if not command.exists():
        return True
    started = incident["started"].replace("-", "").replace(":", "")[:13]
    source_ref = f"cron:{job}:{host}:incident-{started}"
    try:
        result = subprocess.run(
            [
                str(command),
                "gtd",
                "capture",
                f"Investigate {job} collection failure on {host}, exit {returncode}, "
                f"failing since {incident['started']} ({incident['runs']} consecutive runs)",
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
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


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
    try:
        _track_incident(
            state_dir,
            host=record["host"],
            job=job,
            returncode=returncode,
            completed=completed,
            capture_after_runs=int(
                config.get("failure_capture_after_runs", DEFAULT_CAPTURE_AFTER_RUNS)
            ),
            capture=_capture_failure,
        )
    except OSError as exc:
        print(f"SKCounter incident tracking error: {exc}", file=sys.stderr)
    if tail:
        print(tail)
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
