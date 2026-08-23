#!/usr/bin/env python3
"""Verify one portable CapAuth token without exposing bearer material."""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path


MAX_WIRE_BYTES = 131_072


def _fail(reason: str) -> int:
    print(json.dumps({"ok": False, "reason": reason}, separators=(",", ":")))
    return 1


def _signature_matches_issuer(token: object) -> bool:
    """Pin a valid detached signature to the token's declared issuer."""
    signature = getattr(token, "signature", None)
    payload = getattr(token, "payload", None)
    issuer = str(getattr(payload, "issuer", "") or "").strip().upper()
    if not signature or not issuer or issuer == "UNKNOWN":
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="skcounter-capauth-") as temporary:
            root = Path(temporary)
            data_path = root / "payload.json"
            signature_path = root / "payload.sig"
            data_path.write_text(payload.model_dump_json(), encoding="utf-8")
            signature_path.write_text(signature, encoding="utf-8")
            result = subprocess.run(
                [
                    "gpg",
                    "--batch",
                    "--quiet",
                    "--status-fd",
                    "1",
                    "--verify",
                    str(signature_path),
                    str(data_path),
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        if not line.startswith("[GNUPG:] VALIDSIG "):
            continue
        fingerprints = [part.upper() for part in line.split()[2:] if len(part) == 40]
        if issuer in fingerprints:
            return True
    return False


def main() -> int:
    logging.disable(logging.CRITICAL)
    wire = sys.stdin.buffer.read(MAX_WIRE_BYTES + 1)
    if not wire or len(wire) > MAX_WIRE_BYTES:
        return _fail("token_size")

    try:
        padding = b"=" * (-len(wire.strip()) % 4)
        token_json = base64.urlsafe_b64decode(wire.strip() + padding).decode("utf-8")
        from capauth.tokens import has_scope, import_token, verify_audience_token

        token = import_token(token_json)
    except Exception:
        return _fail("token_format")

    capauth_home = Path(os.environ["SKCOUNTER_CAPAUTH_HOME"])
    try:
        if not verify_audience_token(token, "skcounter", home=capauth_home):
            return _fail("token_verification")
        if not _signature_matches_issuer(token):
            return _fail("token_verification")
        allowed_scopes = ("skcounter.report.submit", "skcounter.gateway.submit")
        if not any(has_scope(token, scope) for scope in allowed_scopes):
            return _fail("token_scope")
    except Exception:
        return _fail("token_verification")

    payload = token.payload
    result = {
        "ok": True,
        "issuer": payload.issuer,
        "subject": payload.subject,
        "audience": payload.audience,
        "capabilities": list(payload.capabilities),
        "issued_at": payload.issued_at.isoformat(),
        "expires_at": payload.expires_at.isoformat() if payload.expires_at else None,
        "token_id": payload.token_id,
        "metadata": dict(payload.metadata or {}),
    }
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
