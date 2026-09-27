#!/usr/bin/env python3
"""Enroll every SKWorld fleet node into SKCounter from the collector node.

Run on the node that hosts (or will host) the collector:

    scripts/fleet-enroll.py                 # collector + every fleet node
    scripts/fleet-enroll.py --dry-run       # show the plan only
    scripts/fleet-enroll.py --node ollama   # just one node

Nodes come from this estate's fleet store
(``~/.skcapstone/fleet/objects/node/node-<id>.json``), so a node admitted with
``skcapstone fleet admit`` is picked up on the next run. Safe to re-run: every
step is idempotent, and an existing edge identity is kept.

Per node (over SSH, or locally for this host) it syncs this checkout, ensures
Node.js >= 20, runs ``install-user.sh`` and ``install-runtime.sh edge``,
provisions the edge signing identity, writes ``edge.json`` pointing at the
collector, and enables the timer on harness nodes (label ``pi-harness=true`` or
``--passive`` opts a node out: by default every fleet node reports, and a node
with no harness truthfully reports zero usage).
On the collector it installs the runtime and TLS, imports every edge public
key into the verifier keyring, merges ``trusted_issuers`` and restarts.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import socket
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOME = Path.home()
CONFIG_DIR = HOME / ".config" / "skcounter"
COLLECTOR_CONFIG = CONFIG_DIR / "collector.json"
COLLECTOR_STATE = HOME / ".local" / "state" / "skcounter-collector"
TLS_DIR = CONFIG_DIR / "tls"
PORT = 9398
REMOTE_SRC = "$HOME/.local/src/skcounter"
HARNESS_LABELS = ("pi-harness", "skcode-harness")

ORIGIN = "https://github.com/smilinTux/skcounter.git"

# A real git checkout pinned to this exact commit: the test suite (bundle build)
# needs git metadata and a clean tree, and every node runs identical code.
SYNC_SCRIPT = r"""
set -euo pipefail
origin=$1; revision=$2; src="$HOME/.local/src/skcounter"
if [ ! -d "$src/.git" ]; then rm -rf "$src"; git clone -q "$origin" "$src"; fi
git -C "$src" fetch -q origin
git -C "$src" checkout -q --detach "$revision"
git -C "$src" reset -q --hard "$revision"
git -C "$src" clean -qfdx -e node_modules
"""


def pinned_revision() -> str:
    """This checkout's HEAD; it must be clean and pushed so nodes can fetch it."""
    if run(["git", "-C", str(REPO), "status", "--porcelain"]).stdout.strip():
        sys.exit("commit your changes first: nodes install the pinned commit, not a dirty tree")
    revision = run(["git", "-C", str(REPO), "rev-parse", "HEAD"]).stdout.strip()
    contains = run(["git", "-C", str(REPO), "branch", "-r", "--contains", revision], check=False).stdout
    if not contains.strip():
        sys.exit(f"push {revision[:10]} first: nodes fetch it from {ORIGIN}")
    return revision


# Runs on each edge node as its harness user. Arguments: collector_url node_id harness.
EDGE_SCRIPT = r"""
set -euo pipefail
collector_url=$1; node_id=$2; harness=$3
src="$HOME/.local/src/skcounter"
export PATH="$HOME/.local/bin:$HOME/.skenv/bin:$PATH"  # skenv python has capauth (edge + tests need it)
cd "$src"
if ! command -v node >/dev/null || ! node -e 'process.exit(Number(process.versions.node.split(".")[0])<20?1:0)'; then
  ./scripts/install-node-user.sh >/dev/null
fi
state="$HOME/.local/state/skcounter"
install -d -m 0700 "$state" "$HOME/.config/skcounter"
revision=$(git rev-parse HEAD)
# install-user.sh runs the full test suite; skip it when this exact revision
# already installed and passed on this node.
if [ "$(cat "$state/installed-revision" 2>/dev/null)" != "$revision" ] || [ ! -x "$HOME/.local/bin/skcounter" ]; then
  ./scripts/install-user.sh >/tmp/skcounter-install.$$.log 2>&1 || { tail -20 /tmp/skcounter-install.$$.log; exit 1; }
  rm -f /tmp/skcounter-install.$$.log
  printf '%s\n' "$revision" > "$state/installed-revision"
fi
./scripts/install-runtime.sh edge >/dev/null
./scripts/provision-edge-identity.sh "$state/capauth" "$state/capauth-gnupg" "$node_id" "$USER" "$state/public.asc" >/dev/null
install -m 0600 /dev/stdin "$HOME/.config/skcounter/collector-ca.crt"
cat > "$HOME/.config/skcounter/edge.json.tmp" <<JSON
{
  "schema_version": "skcounter.edge.config.v1",
  "collector_url": "$collector_url/v1/observations",
  "ca_file": "$HOME/.config/skcounter/collector-ca.crt",
  "state_dir": "$state",
  "skcounter_bin": "$HOME/.local/bin/skcounter",
  "capauth_home": "$state/capauth",
  "gnupg_home": "$state/capauth-gnupg",
  "node_id": "$node_id",
  "principal_id": "$USER",
  "subject": "skcounter:$node_id:$USER"
}
JSON
chmod 600 "$HOME/.config/skcounter/edge.json.tmp"
mv -f "$HOME/.config/skcounter/edge.json.tmp" "$HOME/.config/skcounter/edge.json"
dropin="$HOME/.config/systemd/user/skcounter-edge.service.d"
install -d -m 0700 "$dropin"
printf '[Service]\nEnvironment=PATH=%s/.local/bin:/usr/local/bin:/usr/bin:/bin\n' "$HOME" > "$dropin/path.conf"
systemctl --user daemon-reload
if [ "$harness" = "1" ]; then
  systemctl --user enable --now skcounter-edge.timer >/dev/null 2>&1
else
  systemctl --user disable --now skcounter-edge.timer >/dev/null 2>&1 || true
fi
printf 'SKCOUNTER_PUBLIC_KEY_BEGIN\n'
cat "$state/public.asc"
printf 'SKCOUNTER_PUBLIC_KEY_END\n'
"""


def fleet_nodes(fleet_home: Path) -> list[dict]:
    nodes = []
    for path in sorted((fleet_home / "fleet" / "objects" / "node").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("kind", "Node") != "Node":
            continue
        name = str(data.get("name") or path.stem)
        labels = data.get("labels") or {}
        address = ((data.get("spec") or {}).get("address") or {}).get("hostname") or ""
        nodes.append(
            {
                "node_id": name.removeprefix("node-"),
                "address": address,
                "harness": any(str(labels.get(key, "")).strip() for key in HARNESS_LABELS),
            }
        )
    return nodes


def is_local(node: dict) -> bool:
    names = {socket.gethostname(), socket.getfqdn(), "localhost", "127.0.0.1"}
    return node["address"] in names or node["node_id"] in names


def run(cmd, *, input_text=None, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=input_text, text=True, capture_output=True, check=check)


def tailscale_ipv4() -> str:
    out = run(["tailscale", "ip", "-4"], check=False).stdout.split()
    return out[0] if out else ""


def _tool_env() -> dict:
    """PATH with ~/.local/bin and ~/.skenv/bin first: the edge and the test suite
    need a python3 that can import capauth, which on SK nodes lives in ~/.skenv."""
    env = dict(os.environ)
    env["PATH"] = f"{HOME}/.local/bin:{HOME}/.skenv/bin:" + env.get("PATH", "")
    return env


def ensure_collector(bind_ip: str) -> Path:
    marker = COLLECTOR_STATE / "installed-revision"
    revision = pinned_revision()
    if not marker.exists() or marker.read_text().strip() != revision:
        install = subprocess.run([str(REPO / "scripts" / "install-user.sh")], env=_tool_env(),
                                 text=True, capture_output=True)
        if install.returncode != 0:
            raise SystemExit("collector install-user.sh failed:\n" + (install.stdout + install.stderr)[-1500:])
        COLLECTOR_STATE.mkdir(parents=True, exist_ok=True)
        marker.write_text(revision + "\n")
    run([str(REPO / "scripts" / "install-runtime.sh"), "collector"])
    run([str(REPO / "scripts" / "provision-collector-tls.sh"), bind_ip, str(TLS_DIR)])
    for sub in ("capauth", "capauth-gnupg"):
        (COLLECTOR_STATE / sub).mkdir(parents=True, exist_ok=True)
        os.chmod(COLLECTOR_STATE / sub, 0o700)
    os.chmod(COLLECTOR_STATE, 0o700)
    return TLS_DIR / "collector.crt"


def enroll_edge(node: dict, collector_url: str, ca_pem: str) -> str:
    """Install/refresh the edge on one node and return its armored public key."""
    args = [collector_url, node["node_id"], "1" if node["harness"] else "0"]
    local = is_local(node)
    if local:
        run(["bash", "-c", SYNC_SCRIPT, "sync", ORIGIN, pinned_revision()])
        # The CA certificate travels on stdin, the script on the command line.
        proc = run(["bash", "-c", EDGE_SCRIPT, "edge", *args], input_text=ca_pem, check=False)
    else:
        host = node["address"]
        sync = "bash -c " + shlex.quote(SYNC_SCRIPT) + " sync " + shlex.quote(ORIGIN) + " " + pinned_revision()
        run(["ssh", "-o", "BatchMode=yes", host, sync])
        remote = "bash -c " + shlex.quote(EDGE_SCRIPT) + " edge " + " ".join(map(shlex.quote, args))
        proc = run(["ssh", "-o", "BatchMode=yes", host, remote], input_text=ca_pem, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"edge install failed on {node['node_id']}: {(proc.stdout + proc.stderr)[-800:]}")
    text = proc.stdout
    start = text.index("SKCOUNTER_PUBLIC_KEY_BEGIN\n") + len("SKCOUNTER_PUBLIC_KEY_BEGIN\n")
    return text[start : text.index("SKCOUNTER_PUBLIC_KEY_END")]


def import_key(armor: str, gnupg: Path) -> str:
    """Import an edge public key into the verifier keyring; return its fingerprint."""
    run(["gpg", "--homedir", str(gnupg), "--batch", "--import"], input_text=armor)
    listing = run(["gpg", "--homedir", str(gnupg), "--batch", "--with-colons", "--show-keys"],
                  input_text=armor).stdout
    return next(line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fleet-home", type=Path, default=HOME / ".skcapstone")
    parser.add_argument("--bind-ip", default="", help="collector address (default: tailscale IPv4)")
    parser.add_argument("--node", action="append", help="limit to these node ids")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--passive", action="append", default=[],
                        help="node ids to install without a timer (default: every node reports)")
    args = parser.parse_args()

    nodes = fleet_nodes(args.fleet_home)
    if args.node:
        nodes = [node for node in nodes if node["node_id"] in set(args.node)]
    for node in nodes:
        # Every fleet node reports (a node with no harness truthfully reports zero),
        # so dashboard coverage over all fleet nodes is meaningful.
        node["harness"] = node["node_id"] not in set(args.passive)
    if not nodes:
        sys.exit("no fleet nodes found; admit nodes with `skcapstone fleet admit` first")
    bind_ip = args.bind_ip or tailscale_ipv4()
    if not bind_ip:
        sys.exit("could not determine the collector IP; pass --bind-ip")
    collector_url = f"https://{bind_ip}:{PORT}"
    print(f"collector {collector_url}  skcounter {pinned_revision()[:10]}")
    for node in nodes:
        where = "local" if is_local(node) else node["address"]
        print(f"  {node['node_id']:12s} {where:16s} {'timer' if node['harness'] else 'passive'}")
    if args.dry_run:
        return

    ca_pem = ensure_collector(bind_ip).read_text(encoding="utf-8")
    config = json.loads(COLLECTOR_CONFIG.read_text()) if COLLECTOR_CONFIG.exists() else {}
    issuers = dict(config.get("trusted_issuers") or {})
    gnupg = COLLECTOR_STATE / "capauth-gnupg"
    failures = []
    for node in nodes:
        try:
            armor = enroll_edge(node, collector_url, ca_pem)
            fingerprint = import_key(armor, gnupg)
            principal = run(["ssh", "-o", "BatchMode=yes", node["address"], "id -un"]).stdout.strip() \
                if not is_local(node) else os.environ.get("USER", "")
            issuers[fingerprint] = {
                "enabled": True,
                "node_id": node["node_id"],
                "principal_id": principal,
                "subject": f"skcounter:{node['node_id']}:{principal}",
            }
            print(f"  enrolled {node['node_id']} ({fingerprint[-16:]})")
        except Exception as exc:  # keep going; report at the end
            failures.append(f"{node['node_id']}: {exc}")
            print(f"  FAILED {node['node_id']}: {exc}", file=sys.stderr)

    config.update(
        {
            "schema_version": "skcounter.collector.config.v1",
            "bind_host": bind_ip,
            "port": PORT,
            "state_dir": str(COLLECTOR_STATE),
            "tls": {"cert_file": str(TLS_DIR / "collector.crt"), "key_file": str(TLS_DIR / "collector.key")},
            "capauth": {
                "python": config.get("capauth", {}).get("python") or str(HOME / ".skenv" / "bin" / "python3"),
                "verifier": str(HOME / ".local" / "lib" / "skcounter" / "services" / "capauth_verify.py"),
                "home": str(COLLECTOR_STATE / "capauth"),
                "gnupg_home": str(gnupg),
            },
            "trusted_issuers": issuers,
        }
    )
    if issuers:
        tmp = COLLECTOR_CONFIG.with_suffix(".json.tmp")
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(config, handle, indent=2)
        os.replace(tmp, COLLECTOR_CONFIG)
        run(["systemctl", "--user", "enable", "skcounter-collector.service"])
        run(["systemctl", "--user", "restart", "skcounter-collector.service"])
        print(f"collector trusts {len(issuers)} edge identities; restarted")
    print(f"dashboard: SKCOUNTER_DATA_DIR={COLLECTOR_STATE}")
    if failures:
        sys.exit("some nodes failed:\n  " + "\n  ".join(failures))


if __name__ == "__main__":
    main()
