import json
import ssl
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from edge.skcounter_edge import EdgeError, load_config
from edge.skcounter_gateway import collect_gateway_snapshot


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit):
        return self.body[:limit]


class GatewayAdapterTest(unittest.TestCase):
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
            "node_id": "chiap01",
            "principal_id": "skgateway",
            "subject": "skcounter:chiap01:skgateway",
            "scope": "skcounter.gateway.submit",
            "measurement_lane": "gateway_observed",
            "gateway_metrics_url": "http://127.0.0.1:18791/api/tokens?period=30d",
            "gateway_version": "0.1.0",
        }

    def tearDown(self):
        self.temporary.cleanup()

    def test_normalizes_privacy_safe_gateway_rows(self):
        body = json.dumps(
            {
                "rows": [
                    {
                        "bucket": "2026-08-23",
                        "agent_id": "atlas",
                        "model": "sk-code",
                        "backend": "local",
                        "input_tokens": 10,
                        "output_tokens": 3,
                        "cache_read_tokens": 2,
                        "cache_write_tokens": 1,
                        "request_count": 4,
                    }
                ]
            }
        ).encode()
        snapshot = collect_gateway_snapshot(
            self.config,
            opener=lambda *_args, **_kwargs: FakeResponse(body),
            now=lambda: datetime(2026, 8, 23, 12, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(snapshot["measurement_lane"], "gateway_observed")
        self.assertEqual(snapshot["collector"]["backend"], "skgateway")
        self.assertEqual(snapshot["aggregates"][0]["agent"], "atlas")
        self.assertEqual(snapshot["aggregates"][0]["tokens"]["total"], 16)
        self.assertNotIn("prompt", json.dumps(snapshot))
        self.assertEqual(snapshot["idempotency_key"], snapshot["payload_hash"])

    def test_gateway_scope_cannot_collect_harness_lane(self):
        path = self.root / "gateway.json"
        path.write_text(json.dumps(self.config), encoding="utf-8")
        self.assertEqual(load_config(path)["measurement_lane"], "gateway_observed")
        changed = dict(self.config, measurement_lane="harness_reported")
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaises(EdgeError):
            load_config(path)

    def test_rejects_non_loopback_metrics_source(self):
        changed = dict(self.config, gateway_metrics_url="http://chiap01:18791/api/tokens")
        with self.assertRaises(EdgeError):
            collect_gateway_snapshot(
                changed,
                opener=lambda *_args, **_kwargs: FakeResponse(b'{"rows":[]}'),
            )


if __name__ == "__main__":
    unittest.main()
