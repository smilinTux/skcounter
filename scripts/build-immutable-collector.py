#!/usr/bin/env python3
"""Build a deterministic, content-addressed SKCounter collector bundle."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Iterable

SCHEMA = "skcounter.immutable-collector-bundle.v1"
SOURCE_MEMBERS = (
    "services/collector.mjs",
    "services/capauth_verify.py",
    "src/snapshot.mjs",
    "edge/__init__.py",
    "edge/run-edge.sh",
    "edge/skcounter_edge.py",
    "edge/skcounter_fleet.py",
    "edge/skcounter_schedule.py",
    "deploy/systemd/skcounter-collector.service",
    "deploy/systemd/skcounter-edge.service",
    "deploy/systemd/skcounter-edge.timer",
    "package.json",
    "package-lock.json",
)
EXECUTABLE_SOURCE = {
    "services/collector.mjs",
    "services/capauth_verify.py",
    "edge/run-edge.sh",
    "edge/skcounter_edge.py",
    "edge/skcounter_fleet.py",
    "edge/skcounter_schedule.py",
}
CONFIG_TEMPLATE = {
    "schema_version": "skcounter.collector.config.v1",
    "bind_host": "REPLACE_WITH_TAILNET_ADDRESS",
    "port": 9398,
    "state_dir": "REPLACE_WITH_STATE_DIRECTORY",
    "tls": {"cert_file": "REPLACE_WITH_CERTIFICATE", "key_file": "REPLACE_WITH_PRIVATE_KEY_PATH"},
    "capauth": {
        "home": "REPLACE_WITH_CAPAUTH_HOME",
        "gnupg_home": "REPLACE_WITH_PUBLIC_ONLY_GNUPG_HOME",
        "python": "runtime/python3",
        "verifier": "source/services/capauth_verify.py",
    },
    "trusted_issuers": {"REPLACE_WITH_FINGERPRINT": {"enabled": False}},
    "allowed_views": ["models", "daily", "hourly", "time_metrics"],
}


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def command(*argv: str) -> str:
    return subprocess.check_output(argv, text=True, stderr=subprocess.DEVNULL).strip()


def runtime_libraries(executable: Path) -> list[Path]:
    try:
        output = command("ldd", str(executable))
    except (OSError, subprocess.CalledProcessError):
        return []
    paths: set[Path] = set()
    for line in output.splitlines():
        fields = line.replace("=>", " ").split()
        for field in fields:
            candidate = Path(field)
            if field.startswith("/") and candidate.is_file():
                paths.add(candidate.resolve())
    return sorted(paths, key=str)


def capauth_files() -> list[Path]:
    try:
        distribution = importlib.metadata.distribution("capauth")
    except importlib.metadata.PackageNotFoundError:
        return []
    result = []
    for item in distribution.files or ():
        path = Path(distribution.locate_file(item))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            result.append(path.resolve())
    return sorted(set(result), key=str)


def add_file(files: dict[str, tuple[bytes, int, str]], name: str, path: Path, category: str, mode: int = 0o644) -> None:
    files[name] = (path.read_bytes(), mode, category)


def collect(repo: Path, source_ref: str, node: Path, python: Path, gpg: Path) -> tuple[dict[str, tuple[bytes, int, str]], dict[str, object]]:
    files: dict[str, tuple[bytes, int, str]] = {}
    for member in SOURCE_MEMBERS:
        mode = 0o755 if member in EXECUTABLE_SOURCE else 0o644
        add_file(files, f"source/{member}", repo / member, "source", mode)
    files["configuration/collector.template.json"] = (canonical(CONFIG_TEMPLATE), 0o600, "configuration")

    executables = {"node": node.resolve(), "python3": python.resolve(), "gpg": gpg.resolve()}
    for name, path in executables.items():
        add_file(files, f"runtime/{name}", path, "runtime", 0o755)
    libraries: set[Path] = set()
    for path in executables.values():
        libraries.update(runtime_libraries(path))
    for path in sorted(libraries, key=str):
        logical = f"runtime/libraries/{digest(str(path).encode())[:12]}-{path.name}"
        add_file(files, logical, path, "runtime_dependency", 0o755)

    cap_files = capauth_files()
    if not cap_files:
        raise SystemExit("capauth distribution is required for verifier runtime")
    roots = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()]
    for path in cap_files:
        relative = None
        for root in roots:
            try:
                relative = path.relative_to(root)
                break
            except ValueError:
                continue
        logical = relative.as_posix() if relative else f"external/{digest(str(path).encode())[:12]}-{path.name}"
        add_file(files, f"runtime/python-environment/{logical}", path, "python_dependency")

    commit = command("git", "-C", str(repo), "rev-parse", f"{source_ref}^{{commit}}")
    tree = command("git", "-C", str(repo), "rev-parse", f"{source_ref}^{{tree}}")
    dirty = command("git", "-C", str(repo), "status", "--porcelain")
    if dirty:
        raise SystemExit("source checkout must be clean")
    provenance = {
        "repository": command("git", "-C", str(repo), "remote", "get-url", "origin"),
        "source_ref": source_ref,
        "commit": commit,
        "tree": tree,
        "package_lock_sha256": digest((repo / "package-lock.json").read_bytes()),
    }
    environment = {
        "architecture": platform.machine(),
        "platform": sys.platform,
        "node_version": command(str(node), "--version"),
        "python_version": command(str(python), "--version"),
        "gpg_version": command(str(gpg), "--version").splitlines()[0],
        "capauth_version": importlib.metadata.version("capauth"),
    }
    return files, {"provenance": provenance, "environment": environment}


def build(repo: Path, output: Path, source_ref: str, node: Path, python: Path, gpg: Path) -> tuple[Path, str]:
    files, metadata = collect(repo, source_ref, node, python, gpg)
    members = [
        {"path": name, "sha256": digest(data), "size": len(data), "mode": f"{mode:04o}", "category": category}
        for name, (data, mode, category) in sorted(files.items())
    ]
    manifest = {
        "schema_version": SCHEMA,
        **metadata,
        "configuration_policy": "Template only. No key, credential, token, issuer certificate, or live path is bundled.",
        "members": members,
    }
    files["MANIFEST.json"] = (canonical(manifest), 0o644, "manifest")
    output.mkdir(parents=True, exist_ok=True)
    temporary = output / ".collector-bundle.tmp"
    with tarfile.open(temporary, "w", format=tarfile.GNU_FORMAT) as archive:
        for name, (data, mode, _category) in sorted(files.items()):
            info = tarfile.TarInfo(str(PurePosixPath(name)))
            info.size = len(data)
            info.mode = mode
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            import io
            archive.addfile(info, io.BytesIO(data))
    artifact_hash = digest(temporary.read_bytes())
    final = output / f"skcounter-collector-{artifact_hash}.tar"
    os.replace(temporary, final)
    (output / f"{final.name}.sha256").write_bytes(canonical({"artifact": final.name, "sha256": artifact_hash}))
    return final, artifact_hash


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-ref", default="HEAD")
    parser.add_argument("--node", type=Path, default=Path(shutil.which("node") or ""))
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--gpg", type=Path, default=Path(shutil.which("gpg") or ""))
    args = parser.parse_args()
    artifact, artifact_hash = build(args.repo.resolve(), args.output.resolve(), args.source_ref, args.node, args.python, args.gpg)
    print(json.dumps({"artifact": str(artifact), "sha256": artifact_hash}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
