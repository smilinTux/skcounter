from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import ssl
import subprocess
import sys
import tarfile
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "scripts" / "build-immutable-collector.py"
SPEC = importlib.util.spec_from_file_location(
    "immutable_collector_builder", BUILDER_PATH
)
assert SPEC and SPEC.loader
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)
PROMOTER_PATH = ROOT / "scripts" / "promote-immutable-collector.py"
PROMOTER_SPEC = importlib.util.spec_from_file_location(
    "immutable_collector_promoter", PROMOTER_PATH
)
assert PROMOTER_SPEC and PROMOTER_SPEC.loader
PROMOTER = importlib.util.module_from_spec(PROMOTER_SPEC)
PROMOTER_SPEC.loader.exec_module(PROMOTER)


class ImmutableCollectorBundleTests(unittest.TestCase):
    def build(self, output: Path) -> tuple[Path, str]:
        return BUILDER.build(
            ROOT,
            output,
            "HEAD",
            BUILDER.runtime_executable("node"),
            BUILDER.runtime_executable("python3", Path(sys.executable)),
            BUILDER.runtime_executable("gpg"),
        )

    def test_runtime_discovery_ignores_path_wrappers_and_rejects_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            wrapper = Path(temporary) / "gpg"
            wrapper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            wrapper.chmod(0o755)
            with mock.patch.dict("os.environ", {"PATH": temporary}):
                resolved = BUILDER.runtime_executable("gpg")
            self.assertNotEqual(resolved, wrapper)
            self.assertEqual(resolved.read_bytes()[:4], b"\x7fELF")
            with self.assertRaisesRegex(SystemExit, "must be an ELF binary"):
                BUILDER.runtime_executable("gpg", wrapper)

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
                self.assertIn("runtime/bin/node", declared)
                self.assertIn("runtime/bin/python3", declared)
                self.assertIn("runtime/lib/ld-linux-x86-64.so.2", declared)
                self.assertTrue(
                    any(name.startswith("runtime/python/lib/") for name in declared)
                )
                self.assertIn("units/skcounter-collector.service", declared)
                self.assertEqual(
                    manifest["provenance"]["commit"],
                    subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                    ).strip(),
                )
                for member in manifest["members"]:
                    payload = archive.extractfile(member["path"]).read()
                    self.assertEqual(
                        member["sha256"], hashlib.sha256(payload).hexdigest()
                    )
            runtime_manifest = json.loads(
                (first.parent / f"{first.name}.runtime.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                set(runtime_manifest),
                {
                    "schema",
                    "service",
                    "repository",
                    "commit",
                    "tree",
                    "runtime_kind",
                    "artifacts",
                    "dependency_lock",
                    "configuration_digest",
                    "unit_digest",
                    "host",
                    "health_probe",
                    "rollback_artifact",
                    "data_refs",
                    "credential_refs",
                },
            )
            self.assertEqual(runtime_manifest["schema"], "skfleet-service-runtime/v1")
            self.assertEqual(runtime_manifest["runtime_kind"], "node-bundle")
            self.assertEqual(
                runtime_manifest["rollback_artifact"], f"sha256:{first_hash}"
            )
            self.assertEqual(
                runtime_manifest["artifacts"][0]["digest"], f"sha256:{first_hash}"
            )

    @unittest.skipUnless(
        importlib.util.find_spec("capauth") is not None,
        "artifact replay qualification requires the CapAuth verifier dependency",
    )
    def test_isolated_replay_and_9398_health_compatibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shadow = root / "shadow"
            shadow.mkdir()
            (shadow / "gpg").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (shadow / "gpg").chmod(0o755)
            with mock.patch.dict(
                "os.environ", {"PATH": f"{shadow}:{BUILDER.os.defpath}"}
            ):
                artifact, _ = self.build(root / "build")
            runtime_manifest = artifact.parent / f"{artifact.name}.runtime.json"
            runtime_root = root / "runtime-root"
            bundle, previous = PROMOTER.promote(
                artifact, runtime_manifest, runtime_root
            )
            self.assertIsNone(previous)
            current = runtime_root / "skcounter-collector" / "current"
            self.assertEqual(current.resolve(), bundle)
            self.assertFalse(bundle.stat().st_mode & 0o222)
            self.assertFalse((bundle / "bin/collector").stat().st_mode & 0o222)
            with self.assertRaises(PermissionError):
                (bundle / "bin/collector").open("ab")
            unit = (bundle / "units/skcounter-collector.service").read_text(
                encoding="utf-8"
            )
            self.assertIn("skcounter-collector/current/bin/collector", unit)
            self.assertNotIn(".local/lib/skcounter/services", unit)
            environment = {
                "HOME": str(root / "home"),
                "PATH": f"{current / 'runtime/bin'}:/usr/bin:/bin",
                "LANG": "C.UTF-8",
            }
            python_probe = subprocess.run(
                [
                    str(current / "runtime/bin/python3"),
                    "-c",
                    "import capauth.tokens,sys; assert not any('.local' in p for p in sys.path)",
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(python_probe.returncode, 0, python_probe.stderr)
            gpg_probe = subprocess.run(
                [str(current / "runtime/bin/gpg"), "--version"],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(gpg_probe.returncode, 0, gpg_probe.stderr)
            self.assertIn("GnuPG", gpg_probe.stdout)
            verifier_probe = subprocess.run(
                [
                    str(current / "runtime/bin/python3"),
                    str(current / "source/services/capauth_verify.py"),
                ],
                cwd=root,
                env={**environment, "SKCOUNTER_CAPAUTH_HOME": str(root / "capauth")},
                input="e30",
                capture_output=True,
                text=True,
            )
            self.assertEqual(verifier_probe.returncode, 1)
            self.assertEqual(
                json.loads(verifier_probe.stdout)["reason"], "token_format"
            )
            state = root / "state"
            tls = root / "tls"
            tls.mkdir()
            cert = tls / "collector.crt"
            key = tls / "collector.key"
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(key),
                    "-out",
                    str(cert),
                    "-days",
                    "1",
                    "-subj",
                    "/CN=127.0.0.1",
                    "-addext",
                    "subjectAltName=IP:127.0.0.1",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            config = {
                "schema_version": "skcounter.collector.config.v1",
                "bind_host": "127.0.0.1",
                "port": 9398,
                "state_dir": str(state),
                "tls": {"cert_file": str(cert), "key_file": str(key)},
                "capauth": {
                    "home": str(root / "capauth"),
                    "gnupg_home": str(root / "gnupg"),
                    "python": str(current / "runtime/bin/python3"),
                    "verifier": str(current / "source/services/capauth_verify.py"),
                },
                "trusted_issuers": {"TEST": {"enabled": False}},
                "allowed_views": ["models", "daily", "hourly", "time_metrics"],
            }
            config_path = root / "collector.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            process = subprocess.Popen(
                [str(current / "bin/collector"), "serve", "--config", str(config_path)],
                cwd=root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            context = ssl.create_default_context(cafile=str(cert))
            try:
                for _ in range(50):
                    try:
                        with urllib.request.urlopen(
                            "https://127.0.0.1:9398/healthz",
                            context=context,
                            timeout=0.2,
                        ) as response:
                            health = json.load(response)
                        break
                    except OSError:
                        import time

                        time.sleep(0.05)
                else:
                    stdout, stderr = process.communicate(timeout=1)
                    self.fail(
                        f"collector did not become healthy on 9398: "
                        f"exit={process.returncode} stdout={stdout} stderr={stderr}"
                    )
                self.assertEqual(health["status"], "ok")
                self.assertEqual(health["schema_version"], "skcounter.health.v1")
                replay_script = """
import { CollectorStore } from './services/collector.mjs';
const store = new CollectorStore(process.argv[2], () => new Date('2026-09-01T00:00:00Z'));
store.reserveReplay('11111111111111111111111111111111');
try { store.reserveReplay('11111111111111111111111111111111'); process.exit(3); }
catch (error) { if (error.code !== 'EEXIST') throw error; }
"""
                replay_script = replay_script.replace(
                    "./services/collector.mjs",
                    (current / "source/services/collector.mjs").resolve().as_uri(),
                )
                replay = subprocess.run(
                    [
                        str(current / "runtime/bin/node"),
                        "--input-type=module",
                        "-",
                        str(state),
                    ],
                    cwd=root,
                    env=environment,
                    input=replay_script,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(replay.returncode, 0, replay.stderr)
            finally:
                process.terminate()
                process.wait(timeout=5)
                process.communicate()

            prior = runtime_root / "skcounter-collector" / "versions" / "prior"
            prior.mkdir()
            current.unlink()
            current.symlink_to("versions/prior")

            def fail_qualification(_promoted: Path) -> None:
                raise RuntimeError("forced qualification failure")

            with self.assertRaisesRegex(RuntimeError, "forced qualification failure"):
                PROMOTER.promote(
                    artifact, runtime_manifest, runtime_root, fail_qualification
                )
            self.assertEqual(current.resolve(), prior)
            self.assertEqual(current.readlink(), Path("versions/prior"))


if __name__ == "__main__":
    unittest.main()
