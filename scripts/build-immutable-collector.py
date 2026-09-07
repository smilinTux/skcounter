#!/usr/bin/env python3
"""Build a deterministic, content-addressed SKCounter collector bundle."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tarfile
from pathlib import Path, PurePosixPath

from packaging.requirements import Requirement

BUNDLE_SCHEMA = "skcounter.immutable-collector-bundle.v2"
RUNTIME_SCHEMA = "skfleet-service-runtime/v1"
SOURCE_MEMBERS = (
    "services/collector.mjs",
    "services/capauth_verify.py",
    "src/snapshot.mjs",
    "edge/__init__.py",
    "edge/run-edge.sh",
    "edge/skcounter_edge.py",
    "edge/skcounter_fleet.py",
    "edge/skcounter_schedule.py",
    "package.json",
    "package-lock.json",
)
EXECUTABLE_SOURCE = frozenset(
    {
        "services/collector.mjs",
        "services/capauth_verify.py",
        "edge/run-edge.sh",
        "edge/skcounter_edge.py",
        "edge/skcounter_fleet.py",
        "edge/skcounter_schedule.py",
    }
)
RUNTIME_CURRENT = "%h/.local/lib/skcounter-runtime/current"
COLLECTOR_UNIT = f"""[Unit]
Description=SKCounter central aggregate collector
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=30min
StartLimitBurst=5

[Service]
Type=simple
ExecStart={RUNTIME_CURRENT}/bin/collector serve --config %h/.config/skcounter/collector.json
Restart=on-failure
RestartSec=5
RestartSteps=8
RestartMaxDelaySec=5min
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=%h/.local/state/skcounter-collector
UMask=0077

[Install]
WantedBy=default.target
"""
EDGE_UNIT = f"""[Unit]
Description=SKCounter private edge aggregate collection
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={RUNTIME_CURRENT}/bin/edge
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=%h/.local/state/skcounter -%h/.skcapstone/fleet -%h/.skcapstone/pubsub -%h/.skcapstone/coordination/gtd -%h/.skcapstone/notifications
UMask=0077
"""
EDGE_TIMER = """[Unit]
Description=Run SKCounter every 15 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min
RandomizedDelaySec=2min
Persistent=true
AccuracySec=30s

[Install]
WantedBy=timers.target
"""
CONFIG_TEMPLATE = {
    "schema_version": "skcounter.collector.config.v1",
    "bind_host": "REPLACE_WITH_TAILNET_ADDRESS",
    "port": 9398,
    "state_dir": "REPLACE_WITH_STATE_DIRECTORY",
    "tls": {
        "cert_file": "REPLACE_WITH_CERTIFICATE",
        "key_file": "REPLACE_WITH_PRIVATE_KEY_PATH",
    },
    "capauth": {
        "home": "REPLACE_WITH_CAPAUTH_HOME",
        "gnupg_home": "REPLACE_WITH_PUBLIC_ONLY_GNUPG_HOME",
        "python": f"{RUNTIME_CURRENT}/runtime/bin/python3",
        "verifier": f"{RUNTIME_CURRENT}/source/services/capauth_verify.py",
    },
    "trusted_issuers": {"REPLACE_WITH_FINGERPRINT": {"enabled": False}},
    "allowed_views": ["models", "daily", "hourly", "time_metrics"],
}


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode()


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
        for field in line.replace("=>", " ").split():
            candidate = Path(field)
            if field.startswith("/") and candidate.is_file():
                paths.add(candidate.resolve())
    return sorted(paths, key=str)


def add_bytes(
    files: dict[str, tuple[bytes, int, str]],
    name: str,
    data: bytes,
    category: str,
    mode: int = 0o644,
) -> None:
    files[name] = (data, mode, category)


def add_file(
    files: dict[str, tuple[bytes, int, str]],
    name: str,
    path: Path,
    category: str,
    mode: int = 0o644,
) -> None:
    add_bytes(files, name, path.read_bytes(), category, mode)


def python_distributions() -> list[importlib.metadata.Distribution]:
    """Return the closed core dependency set required by CapAuth."""

    pending = ["capauth"]
    found: dict[str, importlib.metadata.Distribution] = {}
    while pending:
        name = pending.pop()
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in found:
            continue
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise SystemExit(f"required Python distribution is missing: {name}") from exc
        found[key] = distribution
        for requirement in distribution.requires or ():
            parsed = Requirement(requirement)
            if parsed.marker and not parsed.marker.evaluate():
                continue
            pending.append(parsed.name)
    return [found[key] for key in sorted(found)]


def python_runtime_files() -> tuple[list[tuple[str, Path]], list[Path]]:
    """Collect a private stdlib and package closure without user-site imports."""

    result: list[tuple[str, Path]] = []
    native: list[Path] = []
    stdlib = Path(sysconfig.get_path("stdlib")).resolve()
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    for path in sorted(stdlib.rglob("*"), key=str):
        if not path.is_file() or path.suffix == ".pyc" or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(stdlib)
        if relative.parts and relative.parts[0] in {"site-packages", "dist-packages"}:
            continue
        result.append((f"runtime/python/lib/{version}/{relative.as_posix()}", path))
        if path.suffix == ".so":
            native.append(path)

    roots = [Path(path).resolve() for path in sys.path if path and Path(path).is_dir()]
    for distribution in python_distributions():
        for item in distribution.files or ():
            path = Path(distribution.locate_file(item)).resolve()
            if not path.is_file() or path.suffix == ".pyc" or "__pycache__" in path.parts:
                continue
            relative = next(
                (path.relative_to(root) for root in roots if path.is_relative_to(root)),
                None,
            )
            if relative is None:
                continue
            logical = f"runtime/python/lib/{version}/site-packages/{relative.as_posix()}"
            result.append((logical, path))
            if path.suffix == ".so":
                native.append(path)
    return result, native


def runtime_wrapper(real_name: str, *, python: bool = False) -> bytes:
    setup = "export PYTHONHOME=\"$root/python\" PYTHONNOUSERSITE=1\n" if python else ""
    isolated = " -s" if python else ""
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        "root=$(CDPATH= cd -- \"$(dirname -- \"$0\")/..\" && pwd)\n"
        f"{setup}"
        "exec \"$root/lib/ld-linux-x86-64.so.2\" --library-path \"$root/lib\" "
        f"\"$root/bin/{real_name}\"{isolated} \"$@\"\n"
    ).encode()


def collect(
    repo: Path, source_ref: str, node: Path, python: Path, gpg: Path
) -> tuple[dict[str, tuple[bytes, int, str]], dict[str, object]]:
    files: dict[str, tuple[bytes, int, str]] = {}
    for member in SOURCE_MEMBERS:
        mode = 0o755 if member in EXECUTABLE_SOURCE else 0o644
        add_file(files, f"source/{member}", repo / member, "source", mode)

    configuration = canonical(CONFIG_TEMPLATE)
    add_bytes(files, "configuration/collector.template.json", configuration, "configuration", 0o600)
    add_bytes(files, "units/skcounter-collector.service", COLLECTOR_UNIT.encode(), "unit")
    add_bytes(files, "units/skcounter-edge.service", EDGE_UNIT.encode(), "unit")
    add_bytes(files, "units/skcounter-edge.timer", EDGE_TIMER.encode(), "unit")
    add_bytes(files, "bin/collector", b'#!/bin/sh\nset -eu\nroot=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\nexec "$root/runtime/bin/node" "$root/source/services/collector.mjs" "$@"\n', "entrypoint", 0o755)
    add_bytes(files, "bin/edge", b'#!/bin/sh\nset -eu\nroot=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\nexec "$root/runtime/bin/python3" "$root/source/edge/skcounter_edge.py" "$@"\n', "entrypoint", 0o755)

    executables = {
        "node.real": node.resolve(),
        "python3.real": python.resolve(),
        "gpg.real": gpg.resolve(),
    }
    for name, path in executables.items():
        add_file(files, f"runtime/bin/{name}", path, "runtime", 0o755)
    add_bytes(files, "runtime/bin/node", runtime_wrapper("node.real"), "runtime", 0o755)
    add_bytes(files, "runtime/bin/python3", runtime_wrapper("python3.real", python=True), "runtime", 0o755)
    add_bytes(files, "runtime/bin/gpg", runtime_wrapper("gpg.real"), "runtime", 0o755)

    python_files, python_native = python_runtime_files()
    for logical, path in python_files:
        add_file(files, logical, path, "python_dependency")

    libraries: set[Path] = set()
    for path in [*executables.values(), *python_native]:
        libraries.update(runtime_libraries(path))
    by_name: dict[str, bytes] = {}
    for path in sorted(libraries, key=str):
        data = path.read_bytes()
        previous = by_name.setdefault(path.name, data)
        if previous != data:
            raise SystemExit(f"runtime library basename collision: {path.name}")
    for name, data in sorted(by_name.items()):
        add_bytes(files, f"runtime/lib/{name}", data, "runtime_dependency", 0o755)

    commit = command("git", "-C", str(repo), "rev-parse", f"{source_ref}^{{commit}}")
    tree = command("git", "-C", str(repo), "rev-parse", f"{source_ref}^{{tree}}")
    if command("git", "-C", str(repo), "status", "--porcelain"):
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
    return files, {
        "provenance": provenance,
        "environment": environment,
        "configuration_digest": digest(configuration),
        "unit_digest": digest(COLLECTOR_UNIT.encode()),
    }


def build(
    repo: Path, output: Path, source_ref: str, node: Path, python: Path, gpg: Path
) -> tuple[Path, str]:
    files, metadata = collect(repo, source_ref, node, python, gpg)
    members = [
        {
            "path": name,
            "sha256": digest(data),
            "size": len(data),
            "mode": f"{mode:04o}",
            "category": category,
        }
        for name, (data, mode, category) in sorted(files.items())
    ]
    inner = {
        "schema_version": BUNDLE_SCHEMA,
        "provenance": metadata["provenance"],
        "environment": metadata["environment"],
        "configuration_policy": "Template only. No key, credential, token, issuer certificate, or live path is bundled.",
        "members": members,
    }
    files["MANIFEST.json"] = (canonical(inner), 0o644, "manifest")
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
            archive.addfile(info, io.BytesIO(data))
    artifact_hash = digest(temporary.read_bytes())
    final = output / f"skcounter-collector-{artifact_hash}.tar"
    os.replace(temporary, final)
    (output / f"{final.name}.sha256").write_bytes(
        canonical({"artifact": final.name, "sha256": artifact_hash})
    )
    provenance = metadata["provenance"]
    runtime_manifest = {
        "schema": RUNTIME_SCHEMA,
        "service": "skcounter-collector",
        "repository": provenance["repository"],
        "commit": provenance["commit"],
        "tree": provenance["tree"],
        "runtime_kind": "node-bundle",
        "artifacts": [
            {
                "name": final.name,
                "path": f"/srv/skfleet/artifacts/sha256/{artifact_hash}/{final.name}",
                "digest": f"sha256:{artifact_hash}",
            }
        ],
        "dependency_lock": f"sha256:{provenance['package_lock_sha256']}",
        "configuration_digest": f"sha256:{metadata['configuration_digest']}",
        "unit_digest": f"sha256:{metadata['unit_digest']}",
        "host": "chiap04",
        "health_probe": {
            "kind": "http",
            "target": "https://127.0.0.1:9398/healthz",
            "timeout_s": 30,
        },
        "rollback_artifact": f"sha256:{artifact_hash}",
        "data_refs": ["data:skcounter-collector"],
        "credential_refs": ["credential:skcounter-verification-public"],
    }
    (output / f"{final.name}.runtime.json").write_bytes(canonical(runtime_manifest))
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
    artifact, artifact_hash = build(
        args.repo.resolve(), args.output.resolve(), args.source_ref, args.node, args.python, args.gpg
    )
    print(json.dumps({"artifact": str(artifact), "sha256": artifact_hash}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
