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


class FakeOpener:
    """Serve per-path bounded responses; missing paths raise like a dead surface."""

    def __init__(self, surfaces):
        self.surfaces = surfaces

    def __call__(self, request, **_kwargs):
        path = request.full_url.split("/api", 1)[1]
        path = "/api" + path.split("?")[0]
        body = self.surfaces.get(path)
        if body is None:
            raise OSError("surface unavailable")
        return FakeResponse(body if isinstance(body, bytes) else json.dumps(body).encode())


STATS = {
    "totalRequests": 10,
    "activeRequests": 2,
    "errorCount": 1,
    "recentRequests5m": 30,
    "recentErrors5m": 0,
    "recentTokens5m": 150,
    "totalInputTokens": 100,
    "totalOutputTokens": 50,
    "totalCostUsd": 0.25,
    "unpricedRequests": 0,
    "latency": {"chiap08-qwen38/qwen3.8": {"p50": 120, "p95": 400, "p99": 900, "mean": 200, "count": 25}},
    "uptime": 1200,
}
TOKENS = {
    "rows": [
        {
            "bucket": "2026-09-05",
            "agent_id": "atlas",
            "model": "qwen3.8",
            "backend": "chiap08-qwen38",
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "request_count": 10,
        }
    ]
}
HEALTH = {"chiap08-qwen38": {"status": "ok", "errorRate": 0, "latencyP50": 110, "totalRequests": 9, "totalErrors": 0}}


class GatewayObservationV2Test(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = {
            "node_id": "chiap08",
            "principal_id": "skgateway",
            "measurement_lane": "gateway_observed",
            "gateway_metrics_url": "http://127.0.0.1:18791/api/tokens?period=30d",
            "gateway_version": "0.1.0",
            "package_version": "0.2.0",
            "state_dir": str(self.root / "state"),
        }

    def tearDown(self):
        self.temporary.cleanup()

    def observation(self, surfaces, when=None):
        from edge.skcounter_gateway import collect_gateway_observation

        return collect_gateway_observation(
            self.config,
            opener=FakeOpener(surfaces),
            now=when or (lambda: datetime(2026, 9, 5, 12, 30, tzinfo=timezone.utc)),
        )

    def test_projects_reviewed_surfaces_into_v2_facts(self):
        observation = self.observation(
            {
                "/api/stats": STATS,
                "/api/health": HEALTH,
                "/api/tokens": TOKENS,
                "/api/costs": {"rows": []},
                "/api/events": {"events": [{"type": "info"}, {"type": "info"}]},
                "/api/activity": {"activity": [{"kind": "request"}]},
            }
        )
        self.assertEqual(observation["schema_version"], "skcounter.gateway_observation.v2")
        self.assertEqual(observation["measurement_lane"], "gateway_observed")
        facts = observation["facts"]
        self.assertEqual(facts["requests"]["total"], 10)
        self.assertEqual(facts["requests"]["active_concurrency"], 2)
        self.assertEqual(facts["requests"]["rate_5m_per_second"], 0.1)
        self.assertEqual(facts["latency_ms"]["chiap08-qwen38/qwen3.8"]["p95"], 400)
        self.assertEqual(facts["tokens"]["throughput_5m_per_second"], 0.5)
        self.assertEqual(facts["cost"]["truth"], "actual")
        self.assertEqual(facts["gateway"]["backend_health"]["chiap08-qwen38"]["status"], "ok")
        self.assertEqual(facts["breakdowns"]["models"], ["qwen3.8"])
        self.assertEqual(facts["breakdowns"]["providers"], ["local"])
        self.assertEqual(facts["breakdowns"]["nodes"], ["chiap08-qwen38"])
        self.assertEqual(facts["breakdowns"]["clients"], ["atlas"])
        self.assertEqual(facts["breakdowns"]["rails"], ["local"])
        self.assertEqual(facts["daily_token_rows"][0]["request_count"], 10)
        self.assertEqual(facts["events"], {"count": 2, "by_type": {"info": 2}})
        self.assertEqual(observation["idempotency_key"], observation["payload_hash"])

    def test_unavailable_source_facts_stay_unavailable(self):
        observation = self.observation({"/api/tokens": TOKENS})
        facts = observation["facts"]
        self.assertEqual(
            facts["requests"]["total"],
            {"unavailable": "gateway_stats_surface_unavailable"},
        )
        self.assertNotEqual(facts["requests"]["total"], 0)
        self.assertEqual(facts["queue"]["wait_ms_percentiles"]["unavailable"], "gateway_surface_does_not_expose_queue_telemetry")
        self.assertEqual(facts["rate_limits"]["http_429_count"]["unavailable"], "gateway_surface_does_not_expose_rate_limit_counts")
        self.assertEqual(facts["breakdowns"]["apps"]["unavailable"], "gateway_surface_does_not_expose_application_attribution")
        self.assertEqual(
            facts["generation"]["throughput_tokens_per_second"]["unavailable"],
            "gateway_stats_surface_does_not_expose_generation_throughput",
        )

    def test_all_surfaces_down_fails_closed(self):
        from edge.skcounter_gateway import EdgeError

        with self.assertRaises(EdgeError):
            self.observation({})

    def test_hash_is_deterministic_and_cross_language_safe(self):
        surfaces = {"/api/stats": STATS, "/api/tokens": TOKENS}
        first = self.observation(surfaces)
        second = self.observation(surfaces)
        self.assertEqual(first["idempotency_key"], second["idempotency_key"])
        self.assertIn('"rate_5m_per_second": 0.1}', json.dumps(first))

        def walk(value):
            if isinstance(value, bool):
                return
            if isinstance(value, float):
                self.assertNotEqual(value, int(value), "integral float would hash differently in JavaScript")
            elif isinstance(value, dict):
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(first)
        changed = dict(STATS)
        changed["totalRequests"] = 11
        third = self.observation({**surfaces, "/api/stats": changed})
        self.assertNotEqual(first["idempotency_key"], third["idempotency_key"])

    def test_prohibited_material_never_enters_the_observation(self):
        noisy = dict(STATS)
        noisy["prompt"] = "secret"
        noisy["credential"] = "secret"
        observation = self.observation({"/api/stats": noisy, "/api/tokens": TOKENS})
        serialized = json.dumps(observation)
        for word in ("secret", "prompt", "credential"):
            self.assertNotIn(word, serialized)

    def test_latency_map_is_bounded(self):
        noisy = dict(STATS)
        noisy["latency"] = {
            f"backend/model-{index}": {"p50": index, "count": 1} for index in range(500)
        }
        observation = self.observation({"/api/stats": noisy})
        self.assertLessEqual(len(observation["facts"]["latency_ms"]), 256)
