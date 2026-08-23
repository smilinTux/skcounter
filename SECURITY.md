# Security Policy

SKCounter reads sensitive local AI harness stores to produce aggregate usage observations. Treat every source store as sensitive even when the requested result is only a token count.

## Current posture

Version 0.1.0 is pre-1.0 and has not received an independent third-party security audit. Tests cover policy decisions, adapter behavior, normalization, private storage, and failure paths. Those tests do not prove the absence of implementation or dependency vulnerabilities.

SKCounter is not a cryptographic component. It creates SHA-256 content digests for idempotency and integrity indexing but owns no signing keys, encryption keys, capability tokens, or transport protocol.

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

### Out of scope

- Vulnerabilities in Tokscale itself. Report upstream and notify SKCounter maintainers so the dependency can be assessed and pinned forward.
- Provider billing accuracy. Version 0.1.0 costs are estimates tied to the reported pricing revision.
- Source harness retention and access controls. The source harness owns its files.
- The planned central collector, CapAuth report capability, fleet timers, and SKGateway adapter until those components are implemented in separately reviewed tasks.
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

Normal allowed reports may fetch public pricing metadata. Production egress policy should constrain that destination at the host network boundary. SKCounter does not claim that a local report is fully offline.

The detailed implementation boundary is documented in [docs/SECURITY.md](./docs/SECURITY.md).

## Secret handling

Never place credentials, capability tokens, cookies, API keys, harness content, or raw observations in:

- Git commits or pull requests.
- Issue or coordination card descriptions.
- CI variables that print to logs.
- Command arguments visible through process listings.
- Snapshot fixtures or documentation examples.

Future network reporting must source its CapAuth identity from approved custody, send only normalized snapshots, and fail closed when policy verification is unavailable.

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
| Latest tagged `0.1.x` | Yes |
| Untagged development commits | Best effort |
| Older lines | No |

Until 1.0, security fixes target the latest tagged 0.1.x release. Upgrade to the latest tag before reporting a version-specific issue.

## Reporting a vulnerability

Do not open a public issue for a vulnerability.

- Primary: use GitHub private vulnerability reporting on the [`smilinTux/skcounter`](https://github.com/smilinTux/skcounter/security) Security tab.
- Secondary: contact the smilinTux or SKWorld maintainers through the address and encryption identity published on the GitHub organization profile. The repository does not duplicate a fingerprint that could become stale.

Include the SKCounter version, Node.js version, operating system, backend report from `skcounter backend --json`, affected command, exit status, and a minimal reproduction with all protected content removed.

We acknowledge reports within 72 hours and target a fix or documented mitigation within 90 days while coordinating disclosure timing. Active exploitation may require earlier protective disclosure.

Good-faith security research performed under coordinated disclosure will not be pursued. Do not access data that is not yours, degrade service, or publish protected material. Reporter credit is given unless declined.

## Standards

This policy follows the [SK Security Disclosure Standard](https://github.com/smilinTux/sk-standards/blob/main/standards/SECURITY_DISCLOSURE_STANDARD.md), ISO/IEC 29147 and 30111 disclosure practices, and CVSS v4.0 for severity assessment.
