import json
import ssl
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import subprocess
from unittest import mock

from edge import skcounter_edge
from edge.skcounter_edge import EdgeError, load_config, run_once
from edge.skcounter_schedule import INCIDENT_FILE, _track_incident


class FakeResponse:
    status = 200

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.body


class EdgeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        ca_candidates = (
            ssl.get_default_verify_paths().cafile,
            "/etc/ssl/certs/ca-certificates.crt",
            "/etc/pki/tls/certs/ca-bundle.crt",
        )
        ca_file = next((candidate for candidate in ca_candidates if candidate and Path(candidate).is_file()), None)
        if not ca_file:
            self.skipTest("system CA bundle is unavailable")
        self.config = {
            "schema_version": "skcounter.edge.config.v1",
            "collector_url": "https://collector.test:9398/v1/observations",
            "ca_file": ca_file,
            "state_dir": str(self.root / "state"),
            "skcounter_bin": "/bin/false",
            "capauth_home": str(self.root / "capauth"),
            "gnupg_home": str(self.root / "gnupg"),
            "node_id": "chiap08",
            "principal_id": "skuser01",
            "subject": "skcounter:chiap08:skuser01",
            "retry_delays_seconds": [],
        }
    def tearDown(self):
        self.temporary.cleanup()

    def write_observation(self):
        path = self.root / "state" / "outbox" / "chiap08" / "skuser01" / "observation.json"
        path.parent.mkdir(parents=True, mode=0o700)
        value = {
            "schema_version": "skcounter.snapshot.v1",
            "measurement_lane": "harness_reported",
            "node_id": "chiap08",
            "principal_id": "skuser01",
            "idempotency_key": "a" * 64,
            "payload_hash": "a" * 64,
        }
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)
        return path, value

    def test_config_requires_https_and_exact_scope(self):
        path = self.root / "edge.json"
        path.write_text(json.dumps(self.config), encoding="utf-8")
        self.assertEqual(load_config(path)["node_id"], "chiap08")
        changed = dict(self.config, collector_url="http://collector.test")
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaises(EdgeError):
            load_config(path)

    def test_acknowledged_observation_moves_to_private_sent_archive(self):
        path, value = self.write_observation()
        ack = json.dumps({
            "schema_version": "skcounter.ack.v1",
            "idempotency_key": value["idempotency_key"],
            "payload_hash": value["payload_hash"],
        }).encode()

        def opener(_request, **_kwargs):
            return FakeResponse(ack)

        status = run_once(
            self.config,
            collect=False,
            token_minter=lambda _config: "signed-token",
            opener=opener,
            now=lambda: datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(status["acknowledged"], 1)
        self.assertEqual(status["pending"], 0)
        self.assertFalse(path.exists())
        archived = next((self.root / "state" / "sent").rglob("*.json"))
        self.assertEqual(archived.stat().st_mode & 0o777, 0o600)

    def test_outage_retains_private_outbox_for_retry(self):
        path, _value = self.write_observation()

        def opener(_request, **_kwargs):
            raise urllib.error.URLError("offline")

        status = run_once(
            self.config,
            collect=False,
            token_minter=lambda _config: "signed-token",
            opener=opener,
            now=lambda: datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(status["failed"], 1)
        self.assertEqual(status["pending"], 1)
        self.assertTrue(path.exists())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_truncated_observation_is_quarantined_not_retried_forever(self):
        # 2026-10-18 noroc2027: a zero-byte observation left by an interrupted
        # collect made every later run report failed=1 and exit 75 for 9 days.
        path, _value = self.write_observation()
        path.write_bytes(b"")
        good = path.with_name("good.json")
        value = {
            "schema_version": "skcounter.snapshot.v1",
            "measurement_lane": "harness_reported",
            "node_id": "chiap08",
            "principal_id": "skuser01",
            "idempotency_key": "b" * 64,
            "payload_hash": "b" * 64,
        }
        good.write_text(json.dumps(value), encoding="utf-8")
        good.chmod(0o600)
        ack = json.dumps({
            "schema_version": "skcounter.ack.v1",
            "idempotency_key": "b" * 64,
            "payload_hash": "b" * 64,
        }).encode()
        status = run_once(
            self.config,
            collect=False,
            token_minter=lambda _config: "signed-token",
            opener=lambda _request, **_kwargs: FakeResponse(ack),
            now=lambda: datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(status["failed"], 0)
        self.assertEqual(status["quarantined"], 1)
        self.assertEqual(status["acknowledged"], 1)
        self.assertEqual(status["pending"], 0)
        quarantined = self.root / "state" / "quarantine" / "chiap08" / "skuser01" / path.name
        self.assertTrue(quarantined.exists())
        self.assertEqual(quarantined.stat().st_mode & 0o777, 0o600)

    def test_identity_mismatch_is_quarantined(self):
        path, value = self.write_observation()
        path.write_text(json.dumps(dict(value, node_id="other")), encoding="utf-8")
        status = run_once(
            self.config,
            collect=False,
            token_minter=lambda _config: "signed-token",
            opener=lambda *_a, **_k: self.fail("must not post"),
            now=lambda: datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual((status["failed"], status["quarantined"], status["pending"]), (0, 1, 0))

    def test_collect_timeout_is_an_edge_error_not_a_traceback(self):
        with mock.patch.object(
            skcounter_edge.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd="skcounter", timeout=1),
        ):
            status = run_once(
                self.config,
                collect=True,
                token_minter=lambda _config: "signed-token",
                opener=lambda *_a, **_k: self.fail("nothing to post"),
                now=lambda: datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc),
            )
        self.assertFalse(status["collection_ok"])

    def test_unreachable_collector_stops_after_first_observation(self):
        outbox = self.root / "state" / "outbox" / "chiap08" / "skuser01"
        outbox.mkdir(parents=True, mode=0o700)
        for index in range(5):
            path = outbox / f"{index}.json"
            path.write_text(json.dumps({
                "schema_version": "skcounter.snapshot.v1",
                "measurement_lane": "harness_reported",
                "node_id": "chiap08",
                "principal_id": "skuser01",
                "idempotency_key": str(index) * 64,
                "payload_hash": str(index) * 64,
            }), encoding="utf-8")
            path.chmod(0o600)
        attempts = []

        def opener(_request, **_kwargs):
            attempts.append(1)
            raise urllib.error.URLError("offline")

        status = run_once(
            dict(self.config, retry_delays_seconds=[0, 0]),
            collect=False,
            token_minter=lambda _config: "signed-token",
            opener=opener,
            now=lambda: datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(len(attempts), 3)  # one file, its retries, then stop
        self.assertEqual((status["failed"], status["pending"]), (1, 5))

    def test_delivery_budget_defers_backlog_without_failing(self):
        path, _value = self.write_observation()
        status = run_once(
            dict(self.config, delivery_budget_seconds=-1),
            collect=False,
            token_minter=lambda _config: "signed-token",
            opener=lambda *_a, **_k: self.fail("budget spent, must not post"),
            now=lambda: datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual((status["failed"], status["pending"]), (0, 1))
        self.assertTrue(path.exists())


class IncidentTrackingTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.state = Path(self.temporary.name)
        self.captures = []

    def tearDown(self):
        self.temporary.cleanup()

    def run_job(self, returncode, minute, captured=True):
        def capture(host, incident, code, job):
            self.captures.append(dict(incident))
            return captured

        _track_incident(
            self.state,
            host="noroc2027",
            job="skcounter-edge",
            returncode=returncode,
            completed=datetime(2026, 10, 27, 3, minute, tzinfo=timezone.utc),
            capture_after_runs=3,
            capture=capture,
        )

    def test_one_capture_per_incident_and_reset_on_success(self):
        self.run_job(75, 0)
        self.run_job(75, 15)
        self.assertEqual(self.captures, [])  # a transient blip files nothing
        for minute in (30, 45, 50, 55):
            self.run_job(75, minute)
        self.assertEqual(len(self.captures), 1)
        self.assertEqual(self.captures[0]["started"], "2026-10-27T03:00:00Z")
        self.run_job(0, 59)
        self.assertFalse((self.state / INCIDENT_FILE).exists())
        for minute in (1, 2, 3):
            self.run_job(1, minute)
        self.assertEqual(len(self.captures), 2)

    def test_transient_failure_that_heals_files_nothing(self):
        self.run_job(75, 0)
        self.run_job(0, 15)
        self.run_job(75, 30)
        self.run_job(75, 45)
        self.assertEqual(self.captures, [])

    def test_failed_capture_is_retried_on_the_next_failure(self):
        for minute in (0, 15, 30):
            self.run_job(75, minute, captured=False)
        self.run_job(75, 45, captured=True)
        self.run_job(75, 50, captured=True)
        self.assertEqual(len(self.captures), 2)
        self.assertEqual({c["started"] for c in self.captures}, {"2026-10-27T03:00:00Z"})


if __name__ == "__main__":
    unittest.main()
