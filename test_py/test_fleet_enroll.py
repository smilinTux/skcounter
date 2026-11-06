import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "fleet-enroll.py"
spec = importlib.util.spec_from_file_location("fleet_enroll", SCRIPT)
fleet_enroll = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fleet_enroll)


class NotYetTrustedTest(unittest.TestCase):
    NODES = [{"node_id": "a"}, {"node_id": "b"}, {"node_id": "c"}]

    def test_returns_only_nodes_without_an_enabled_issuer(self):
        config = {
            "trusted_issuers": {
                "F1": {"enabled": True, "node_id": "a"},
                "F2": {"enabled": False, "node_id": "b"},
            }
        }
        self.assertEqual(
            [node["node_id"] for node in fleet_enroll.not_yet_trusted(self.NODES, config)],
            ["b", "c"],
        )

    def test_no_collector_config_means_every_node_is_missing(self):
        self.assertEqual(fleet_enroll.not_yet_trusted(self.NODES, {}), self.NODES)


class EdgeConfigTest(unittest.TestCase):
    def test_edge_config_declares_its_canonical_fleet_object(self):
        # publish_fleet_status rejects anything but skcounter-edge-<node_id>.
        self.assertIn('"fleet_object": "skcounter-edge-$node_id"', fleet_enroll.EDGE_SCRIPT)


if __name__ == "__main__":
    unittest.main()
