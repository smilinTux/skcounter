#!/usr/bin/env python3
"""Verify, extract, and atomically promote an SKCounter collector bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

BUNDLE_SCHEMA = "skcounter.immutable-collector-bundle.v2"
RUNTIME_SCHEMA = "skfleet-service-runtime/v1"


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _safe_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    names: set[str] = set()
    for member in members:
        path = PurePosixPath(member.name)
        if (
            not member.isfile()
            or path.is_absolute()
            or ".." in path.parts
            or member.name in names
        ):
            raise ValueError(f"unsafe bundle member: {member.name}")
        names.add(member.name)
    return members


def _verify_extracted(
    root: Path, manifest: dict[str, object], *, sealed: bool = False
) -> None:
    if manifest.get("schema_version") != BUNDLE_SCHEMA:
        raise ValueError("unsupported bundle schema")
    declared = manifest.get("members")
    if not isinstance(declared, list):
        raise ValueError("bundle members are missing")
    expected = {"MANIFEST.json"}
    for item in declared:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError("invalid bundle member record")
        path = root / item["path"]
        expected.add(item["path"])
        expected_mode = int(str(item.get("mode")), 8)
        if sealed:
            expected_mode &= ~0o222
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != item.get("size")
            or sha256(path) != item.get("sha256")
            or path.stat().st_mode & 0o777 != expected_mode
        ):
            raise ValueError(f"bundle member mismatch: {item['path']}")
    actual = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }
    if actual != expected:
        raise ValueError("bundle member inventory mismatch")
    if sealed and any(path.stat().st_mode & 0o222 for path in root.rglob("*")):
        raise ValueError("promoted runtime is writable")


def _seal(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            path.chmod(path.stat().st_mode & 0o555)
    (root / "MANIFEST.json").chmod(0o444)
    for path in sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda value: len(value.parts),
        reverse=True,
    ):
        path.chmod(0o555)
    root.chmod(0o555)


def _replace_link(link: Path, target: Path | None) -> None:
    temporary = link.with_name(f".{link.name}.tmp-{os.getpid()}")
    temporary.unlink(missing_ok=True)
    if target is None:
        link.unlink(missing_ok=True)
        return
    temporary.symlink_to(target)
    os.replace(temporary, link)


def promote(
    artifact: Path,
    runtime_manifest_path: Path,
    root: Path,
    qualify: Callable[[Path], None] = lambda _current: None,
) -> tuple[Path, Path | None]:
    runtime = json.loads(runtime_manifest_path.read_text(encoding="utf-8"))
    if runtime.get("schema") != RUNTIME_SCHEMA:
        raise ValueError("unsupported runtime schema")
    if runtime.get("service") != "skcounter-collector":
        raise ValueError("unexpected service")
    artifacts = runtime.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 1:
        raise ValueError("runtime must name exactly one artifact")
    expected = artifacts[0]
    artifact_hash = sha256(artifact)
    if (
        expected.get("name") != artifact.name
        or expected.get("digest") != f"sha256:{artifact_hash}"
    ):
        raise ValueError("artifact identity mismatch")

    service_root = root / runtime["service"]
    versions = service_root / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    version = versions / artifact_hash
    if not version.exists():
        with tempfile.TemporaryDirectory(prefix=".extract-", dir=versions) as temporary:
            stage = Path(temporary)
            with tarfile.open(artifact) as archive:
                members = _safe_members(archive)
                archive.extractall(stage, members=members, filter="data")
            manifest = json.loads((stage / "MANIFEST.json").read_text(encoding="utf-8"))
            _verify_extracted(stage, manifest)
            _seal(stage)
            _verify_extracted(stage, manifest, sealed=True)
            os.replace(stage, version)
    else:
        manifest = json.loads((version / "MANIFEST.json").read_text(encoding="utf-8"))
        _verify_extracted(version, manifest, sealed=True)

    current = service_root / "current"
    previous = Path(os.readlink(current)) if current.is_symlink() else None
    _replace_link(current, version)
    try:
        qualify(current)
    except BaseException:
        _replace_link(current, previous)
        raise
    return version, previous


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.home() / ".local/lib")
    args = parser.parse_args()
    version, previous = promote(
        args.artifact.resolve(), args.runtime_manifest.resolve(), args.root.resolve()
    )
    print(
        json.dumps(
            {
                "previous": str(previous) if previous else None,
                "version": str(version),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
