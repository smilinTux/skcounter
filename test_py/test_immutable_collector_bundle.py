from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import shutil
import ssl
import subprocess
import tarfile
import tempfile
import unittest
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "scripts" / "build-immutable-collector.py"
SPEC = importlib.util.spec_from_file_location("immutable_collector_builder", BUILDER_PATH)
assert SPEC and SPEC.loader
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class ImmutableCollectorBundleTests(unittest.TestCase):
    def build(self, output: Path) -> tuple[Path, str]:
        return BUILDER.build(
            ROOT,
            output,
            "HEAD",
            Path(shutil.which("node") or "node"),
            Path(shutil.which("python3") or "python3"),
            Path(shutil.which("gpg") or "gpg"),
        )

    @unittest.skipUnless(
        importlib.util.find_spec("capauth") is not None,
        "full bundle qualification requires the CapAuth verifier dependency",
    )
    def test_two_clean_builds_are_byte_identical_and_manifest_is_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, first_hash = self.build(root / "one")
            second, second_hash = self.build(root / "two")
            self.assertEqual(first_hash, second_hash)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first_hash, hashlib.sha256(first.read_bytes()).hexdigest())
            with tarfile.open(first) as archive:
                names = set(archive.getnames())
                manifest = json.load(archive.extractfile("MANIFEST.json"))
                declared = {member["path"] for member in manifest["members"]}
                self.assertEqual(declared | {"MANIFEST.json"}, names)
                self.assertIn("source/services/collector.mjs", declared)
                self.assertIn("source/services/capauth_verify.py", declared)
                self.assertIn("source/edge/skcounter_edge.py", declared)
                self.assertIn("source/package-lock.json", declared)
                self.assertIn("runtime/node", declared)
                self.assertIn("runtime/python3", declared)
                self.assertTrue(any(name.startswith("runtime/python-environment/") for name in declared))
                self.assertEqual(manifest["provenance"]["commit"], subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip())
                for member in manifest["members"]:
                    payload = archive.extractfile(member["path"]).read()
                    self.assertEqual(member["sha256"], hashlib.sha256(payload).hexdigest())

    def test_isolated_replay_and_9398_health_compatibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            tls = root / "tls"
            tls.mkdir()
            cert = tls / "collector.crt"
            key = tls / "collector.key"
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(key), "-out", str(cert), "-days", "1",
                "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            config = {
                "schema_version": "skcounter.collector.config.v1",
                "bind_host": "127.0.0.1",
                "port": 9398,
                "state_dir": str(state),
                "tls": {"cert_file": str(cert), "key_file": str(key)},
                "capauth": {"home": str(root / "capauth"), "gnupg_home": str(root / "gnupg")},
                "trusted_issuers": {"TEST": {"enabled": False}},
                "allowed_views": ["models", "daily", "hourly", "time_metrics"],
            }
            config_path = root / "collector.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            process = subprocess.Popen(
                ["node", str(ROOT / "services" / "collector.mjs"), "serve", "--config", str(config_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            context = ssl.create_default_context(cafile=str(cert))
            try:
                for _ in range(50):
                    try:
                        with urllib.request.urlopen("https://127.0.0.1:9398/healthz", context=context, timeout=0.2) as response:
                            health = json.load(response)
                        break
                    except OSError:
                        import time
                        time.sleep(0.05)
                else:
                    self.fail("collector did not become healthy on 9398")
                self.assertEqual(health["status"], "ok")
                self.assertEqual(health["schema_version"], "skcounter.health.v1")
                replay_script = """
import { CollectorStore } from './services/collector.mjs';
const store = new CollectorStore(process.argv[2], () => new Date('2026-09-01T00:00:00Z'));
store.reserveReplay('11111111111111111111111111111111');
try { store.reserveReplay('11111111111111111111111111111111'); process.exit(3); }
catch (error) { if (error.code !== 'EEXIST') throw error; }
"""
                replay = subprocess.run(["node", "--input-type=module", "-", str(state)], cwd=ROOT, input=replay_script, text=True, capture_output=True)
                self.assertEqual(replay.returncode, 0, replay.stderr)
            finally:
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
