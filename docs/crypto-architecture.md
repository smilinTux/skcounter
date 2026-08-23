# SKCounter cryptographic surfaces

Status: deployed design for 0.2.0.

Maturity-tier: T0 - Classical.

SKCounter does not implement cryptographic primitives. It delegates report signing and verification to CapAuth and HTTPS to the Node.js and OpenSSL runtime. It does create SHA-256 digests for canonical payload integrity and idempotency.

## Surface inventory

| Surface | Container or protocol tag | Primitive | Owner | Runtime evidence |
| --- | --- | --- | --- | --- |
| Report capability | `skcapstone_token=1.0`, audience `skcounter` | Classical OpenPGP Ed25519 service signature | CapAuth | `capauth_verify.py` requires both audience verification and signer fingerprint verification |
| Collector transport | TLS 1.2 or newer | Runtime negotiated classical TLS suite | Node.js/OpenSSL over Tailscale | `openssl s_client` and collector certificate inspection |
| Payload integrity | `skcounter.snapshot.v1` | SHA-256 over canonical JSON | SKCounter | Collector recomputes `payload_hash` and `idempotency_key` |
| At-rest service key | GnuPG keyring, no SKCounter wire container | Classical Ed25519 service key | GnuPG and operating-system user controls | `gpg --with-colons --list-secret-keys` under the isolated service keyring |

The service identity is one-year, signing-only, passphrase-less key material because the unattended one-shot timer must mint short-lived tokens without interactive unlock. Its risk is constrained by a mode `0700` isolated GnuPG home, the harness user boundary, a one-hour token lifetime, one exact scope, and a central allowlist binding the issuer to one subject, node, and principal. Removing or disabling the allowlist entry revokes that service identity immediately at the collector.

## Agility and current limitation

SKCounter consumes the CapAuth portable token envelope and does not select or negotiate its signature suite. The collector accepts only the reviewed CapAuth classical signature path in 0.2.0. This is an honest T0 posture, not T1: SKCounter has no local suite registry or hybrid negotiation surface. Future CapAuth envelope suites can be added behind the verification adapter without changing the snapshot schema or edge outbox.

The transport is also classical. Tailscale encryption plus certificate-verified HTTPS protects the live cluster path, but SKCounter makes no post-quantum or hybrid transport claim.

## Rotation and revocation

1. Disable the issuer in `collector.json`.
2. Provision a replacement edge identity in a new isolated GnuPG home.
3. Import only its public certificate into the collector verifier keyring.
4. Add the new fingerprint binding and restart the collector.
5. Run a signed canary and confirm one acknowledgement.
6. Delete the old allowlist entry, revoke the old key, and preserve the rotation evidence.

Token-level revocation uses the CapAuth verifier home's `security/revoked-tokens.json`. Issuer-level emergency revocation uses the collector allowlist and does not depend on an edge being online.

## Standards statement

SKCounter follows the SK Cryptography Standard's honest-claim and self-report requirements for these delegated surfaces. Version 0.2.0 remains T0 Classical and does not claim the T1 crypto-agility, T2 hybrid KEM, T3 hybrid signature, or T4 transport goals.
