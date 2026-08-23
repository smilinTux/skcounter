#!/usr/bin/env python3
"""Publish SKCounter edge health through the SKCapstone Fleet store."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from typing import Any


FLEET_KIND = "cronjob"


class FleetStatusError(RuntimeError):
    """Raised when the governed Fleet status projection cannot be written."""


def _timer_state() -> str:
    """Return a compact state for the user timer without changing it."""
    enabled = subprocess.run(
        ["systemctl", "--user", "is-enabled", "skcounter-edge.timer"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    ).stdout.strip()
    active = subprocess.run(
        ["systemctl", "--user", "is-active", "skcounter-edge.timer"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    ).stdout.strip()
    return f"{enabled or 'unknown'}/{active or 'unknown'}"


def publish_fleet_status(
    config: dict[str, Any],
    edge_status: dict[str, Any],
    returncode: int,
    *,
    timer_state: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Write one node-owned status for its declared Fleet CronJob object."""
    object_name = str(config.get("fleet_object", "")).strip()
    if not object_name:
        return False

    try:
        from skcapstone.fleet.paths import default_paths
        from skcapstone.fleet.store import (
            Writer,
            read_spec,
            read_status,
            write_status,
            writer_identity,
        )
    except Exception as exc:
        raise FleetStatusError("SKCapstone Fleet runtime is unavailable") from exc

    node = str(config["node_id"])
    job = str(config.get("scheduled_job", "skcounter-edge"))
    if job not in {"skcounter-edge", "skcounter-gateway"}:
        raise FleetStatusError("Fleet job is not allowed")
    expected_name = f"{job}-{node}"
    if object_name != expected_name:
        raise FleetStatusError("Fleet object does not match the canonical node identity")

    paths = default_paths()
    spec = read_spec(paths, FLEET_KIND, object_name)
    if spec is None:
        raise FleetStatusError("Fleet CronJob spec is unavailable")

    completed_at = str(edge_status.get("completed_at", ""))
    existing = read_status(paths, FLEET_KIND, object_name, node) or {}
    prior = existing.get("status", {}) if isinstance(existing, dict) else {}
    acknowledged = int(edge_status.get("acknowledged", 0))
    last_ack = completed_at if acknowledged > 0 else prior.get("lastAcknowledgedAt")
    observed_at = (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    collection_ok = bool(edge_status.get("collection_ok")) and returncode == 0
    pending = int(edge_status.get("pending", 0))
    timer = timer_state if timer_state is not None else _timer_state()
    status = {
        "backend": str(config.get("backend", "tokscale")),
        "backendVersion": str(config.get("backend_version", "4.13.0")),
        "collectionResult": "success" if collection_ok else "failure",
        "lastAcknowledgedAt": last_ack,
        "lastRun": completed_at or observed_at,
        "outboxDepth": pending,
        "packageVersion": str(config.get("package_version", "0.2.0")),
        "principalId": str(config["principal_id"]),
        "timer": timer,
    }
    conditions = [
        {
            "type": "Ready",
            "status": "True" if collection_ok else "False",
            "reason": "CollectionSucceeded" if collection_ok else "CollectionFailed",
            "message": "scheduled aggregate collection completed" if collection_ok else "scheduled aggregate collection failed",
            "lastTransition": observed_at,
        },
        {
            "type": "OutboxPending",
            "status": "True" if pending > 0 else "False",
            "reason": "PendingObservations" if pending > 0 else "OutboxDrained",
            "message": f"{pending} aggregate observations pending",
            "lastTransition": observed_at,
        },
    ]
    try:
        return write_status(
            paths,
            FLEET_KIND,
            object_name,
            node=node,
            status=status,
            conditions=conditions,
            observed_generation=int(spec["generation"]),
            writer=Writer(
                role="sknoded",
                node=node,
                identity=writer_identity(),
            ),
        )
    except Exception as exc:
        raise FleetStatusError("Fleet status write failed") from exc
