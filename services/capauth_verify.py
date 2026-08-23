#!/usr/bin/env python3
"""Verify one portable CapAuth token without exposing bearer material."""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from pathlib import Path


MAX_WIRE_BYTES = 131_072


def _fail(reason: str) -> int:
    print(json.dumps({"ok": False, "reason": reason}, separators=(",", ":")))
    return 1


def main() -> int:
    logging.disable(logging.CRITICAL)
    wire = sys.stdin.buffer.read(MAX_WIRE_BYTES + 1)
    if not wire or len(wire) > MAX_WIRE_BYTES:
        return _fail("token_size")

    try:
        padding = b"=" * (-len(wire.strip()) % 4)
        token_json = base64.urlsafe_b64decode(wire.strip() + padding).decode("utf-8")
        from capauth.tokens import has_scope, import_token, signature_verifies, verify_audience_token

        token = import_token(token_json)
    except Exception:
        return _fail("token_format")

    capauth_home = Path(os.environ["SKCOUNTER_CAPAUTH_HOME"])
    try:
        if not verify_audience_token(token, "skcounter", home=capauth_home):
            return _fail("token_verification")
        if not signature_verifies(token):
            return _fail("token_verification")
        if not has_scope(token, "skcounter.report.submit"):
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
