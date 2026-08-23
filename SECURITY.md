# Security Policy

SKCounter reads sensitive local AI harness stores to produce aggregate usage observations. Treat every source store as sensitive even when the requested result is only a token count.

## Current posture

Version 0.2.0 is pre-1.0 and has not received an independent third-party security audit. Tests cover policy decisions, adapter behavior, normalization, private storage, signed delivery, schema denial, identity binding, replay, duplicate handling, and failure paths. Those tests do not prove the absence of implementation or dependency vulnerabilities.

SKCounter is a T0 Classical cryptographic component on its reporting surfaces. It provisions isolated Ed25519 service identities, delegates short-lived capability signing and verification to CapAuth, requires TLS 1.2 or newer, and creates SHA-256 content digests. The exact surface inventory and current limitations are in [docs/crypto-architecture.md](./docs/crypto-architecture.md).

## Threat model summary

### Protected material

Harness stores may contain:

- Prompts and responses.
- Source code and workspace paths.
- Tool calls and tool arguments.
- Raw session identifiers.
- Credentials accidentally included in conversations.
- Protected client or Matter content.

### In scope

- A facade command that reaches a blocked Tokscale submission, account, credential, synchronization, quota, subprocess, summarization, or TUI path.
- An unknown backend command that does not fail closed.
- A normalized snapshot containing raw prompt or response content, source paths, workspace paths, raw session identifiers, tool arguments, or credentials.
- A cross-user or root scanner that traverses another principal's harness stores.
- An observation written with permissions broader than user-only.
- An adapter replacement that bypasses the facade policy or changes the stable snapshot contract silently.
- A dependency or packaging change that removes Tokscale attribution or weakens the exact version pin.
- Acceptance of an unsigned, expired, revoked, wrong-audience, over-scoped, untrusted, replayed, identity-mismatched, malformed, oversized, or clock-skewed central report.
- Capability-token or private-key disclosure through process arguments, logs, acknowledgements, fixtures, or central observations.

### Out of scope

- Vulnerabilities in Tokscale itself. Report upstream and notify SKCounter maintainers so the dependency can be assessed and pinned forward.
- Provider billing accuracy. Costs are estimates tied to the reported pricing revision.
- Source harness retention and access controls. The source harness owns its files.
- The planned SKGateway adapter until it is implemented in its separately reviewed task.
- A local attacker who already controls the same operating-system user and can directly read that user's harness stores.

## Enforced controls

- Collectors run as the harness-owning user, never root.
- Provider configuration is isolated under `~/.config/skcounter/providers/` by default.
- The upstream social API base is redirected to an unused loopback endpoint.
- Policy uses an allowlist and rejects unknown top-level commands.
- `report` forces `--no-summarize` and rejects external summarizer selection.
- Snapshot normalization emits only the fields allowed by `skcounter.snapshot.v1`.
- Local observations are appended exclusively beneath mode `0700` directories with mode `0600` files.
- Harness and gateway measurement lanes remain distinct.
- The backend package and lockfile integrity are committed and checked in CI.
- The collector binds each trusted CapAuth issuer to one exact subject, node, and principal.
- The collector accepts only HTTPS, one report scope, bounded clock skew, privacy-safe views, and a one MiB body.
- Edge outboxes, service keyrings, central observations, acknowledgements, replay records, and projection outboxes use user-only permissions.
- Accepted observations are immutable, acknowledgements are idempotent, and duplicate requests do not inflate totals.

Normal allowed reports may fetch public pricing metadata. Production egress policy should constrain that destination at the host network boundary. SKCounter does not claim that a local report is fully offline.

The detailed implementation boundary is documented in [docs/SECURITY.md](./docs/SECURITY.md).

## Secret handling

Never place credentials, capability tokens, cookies, API keys, harness content, or raw observations in:

- Git commits or pull requests.
- Issue or coordination card descriptions.
- CI variables that print to logs.
- Command arguments visible through process listings.
- Snapshot fixtures or documentation examples.

Network reporting sources its CapAuth identity from isolated operating-system user custody, sends only normalized snapshots, and fails closed when policy verification is unavailable. Never paste a portable token or service private key into a shell command.

## Dependency and release posture

| Surface | Control |
| --- | --- |
| Tokscale | Exact `@tokscale/cli` 4.13.0 pin plus npm lock integrity |
| npm install | `npm ci --ignore-scripts` |
| Dependency findings | `npm audit --omit=dev` release gate |
| Packaging | `npm pack --dry-run` inspection |
| Repository history | Pinned gitleaks workflow with redaction |
| Documentation | Reusable sk-standards docs-check and negative control |
| Workflow integrity | Reusable sk-standards CI gate audit and negative control |

## Supported versions

| Version | Supported |
| --- | --- |
| Latest tagged `0.2.x` | Yes |
| Untagged development commits | Best effort |
| Older lines | No |

Until 1.0, security fixes target the latest tagged 0.2.x release. Upgrade to the latest tag before reporting a version-specific issue.

## Reporting a vulnerability

Do not open a public issue for a vulnerability.

- Primary: use GitHub private vulnerability reporting on the [`smilinTux/skcounter`](https://github.com/smilinTux/skcounter/security) Security tab.
- Secondary: contact the smilinTux or SKWorld maintainers through the address and encryption identity published on the GitHub organization profile. The repository does not duplicate a fingerprint that could become stale.

Include the SKCounter version, Node.js version, operating system, backend report from `skcounter backend --json`, affected command, exit status, and a minimal reproduction with all protected content removed.

We acknowledge reports within 72 hours and target a fix or documented mitigation within 90 days while coordinating disclosure timing. Active exploitation may require earlier protective disclosure.

Good-faith security research performed under coordinated disclosure will not be pursued. Do not access data that is not yours, degrade service, or publish protected material. Reporter credit is given unless declined.

## Standards

This policy follows the [SK Security Disclosure Standard](https://github.com/smilinTux/sk-standards/blob/main/standards/SECURITY_DISCLOSURE_STANDARD.md), ISO/IEC 29147 and 30111 disclosure practices, and CVSS v4.0 for severity assessment.

For cryptographic surfaces, SKCounter follows the [SK Cryptography Standard](https://github.com/smilinTux/sk-standards/blob/main/standards/CRYPTOGRAPHY_STANDARD.md) honest-claim rules. Version 0.2.0 is T0 Classical and does not claim hybrid or post-quantum protection.
