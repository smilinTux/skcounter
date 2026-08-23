import json
import ssl
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from edge.skcounter_edge import EdgeError, load_config, run_once


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


if __name__ == "__main__":
    unittest.main()
