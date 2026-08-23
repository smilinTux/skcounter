import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from edge.skcounter_fleet import FleetStatusError, publish_fleet_status


class FleetStatusTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = {
            "node_id": "chiap08",
            "principal_id": "skuser01",
            "fleet_object": "skcounter-edge-chiap08",
            "package_version": "0.2.0",
            "backend": "tokscale",
            "backend_version": "4.13.0",
        }
        self.edge_status = {
            "completed_at": "2026-08-23T12:00:00Z",
            "acknowledged": 1,
            "pending": 0,
            "collection_ok": True,
        }

    def tearDown(self):
        self.temporary.cleanup()

    def _modules(self, *, spec=True, existing=None):
        captured = {}

        class Writer:
            def __init__(self, **values):
                self.values = values

        store = types.ModuleType("skcapstone.fleet.store")
        store.Writer = Writer
        store.read_spec = lambda *_args: {"generation": 4} if spec else None
        store.read_status = lambda *_args: existing

        def write_status(*args, **kwargs):
            captured.update({"args": args, **kwargs})
            return True

        store.write_status = write_status
        store.writer_identity = lambda: "capauth:test@example"
        paths = types.ModuleType("skcapstone.fleet.paths")
        paths.default_paths = lambda: self.root
        return {
            "skcapstone": types.ModuleType("skcapstone"),
            "skcapstone.fleet": types.ModuleType("skcapstone.fleet"),
            "skcapstone.fleet.paths": paths,
            "skcapstone.fleet.store": store,
        }, captured

    def test_publishes_exact_node_owned_health(self):
        modules, captured = self._modules()
        with patch.dict(sys.modules, modules):
            changed = publish_fleet_status(
                self.config,
                self.edge_status,
                0,
                timer_state="enabled/active",
                now=datetime(2026, 8, 23, 12, 1, tzinfo=timezone.utc),
            )
        self.assertTrue(changed)
        self.assertEqual(captured["node"], "chiap08")
        self.assertEqual(captured["observed_generation"], 4)
        self.assertEqual(captured["status"]["collectionResult"], "success")
        self.assertEqual(captured["status"]["outboxDepth"], 0)
        self.assertEqual(captured["status"]["lastAcknowledgedAt"], "2026-08-23T12:00:00Z")
        self.assertEqual(captured["writer"].values["role"], "sknoded")

    def test_carries_last_ack_when_no_observation_was_acknowledged(self):
        existing = {"status": {"lastAcknowledgedAt": "2026-08-23T11:45:00Z"}}
        modules, captured = self._modules(existing=existing)
        current = dict(self.edge_status, acknowledged=0)
        with patch.dict(sys.modules, modules):
            publish_fleet_status(self.config, current, 0, timer_state="enabled/active")
        self.assertEqual(captured["status"]["lastAcknowledgedAt"], "2026-08-23T11:45:00Z")

    def test_rejects_cross_node_object_name(self):
        changed = dict(self.config, fleet_object="skcounter-edge-chiap04")
        with self.assertRaises(FleetStatusError):
            publish_fleet_status(changed, self.edge_status, 0, timer_state="enabled/active")

    def test_accepts_separate_gateway_job(self):
        changed = dict(
            self.config,
            scheduled_job="skcounter-gateway",
            fleet_object="skcounter-gateway-chiap08",
            backend="skgateway",
            backend_version="0.1.0",
        )
        modules, captured = self._modules()
        with patch.dict(sys.modules, modules):
            publish_fleet_status(changed, self.edge_status, 0, timer_state="enabled/active")
        self.assertEqual(captured["status"]["backend"], "skgateway")
        self.assertEqual(captured["status"]["backendVersion"], "0.1.0")

    def test_requires_declared_fleet_spec(self):
        modules, _captured = self._modules(spec=False)
        with patch.dict(sys.modules, modules):
            with self.assertRaises(FleetStatusError):
                publish_fleet_status(self.config, self.edge_status, 0, timer_state="enabled/active")


if __name__ == "__main__":
    unittest.main()
