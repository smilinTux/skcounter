# SKCounter agent instructions

## Scope

SKCounter is the governed, provider-neutral token accounting facade for the SK fleet. The facade owns policy, branding, the normalized snapshot contract, installation, and fleet integration. Tokscale is the initial replaceable backend.

## Coordination

Claim an eligible SKCapstone card before modifying the repository. Keep central collector work, fleet rollout, and provider replacement in separately scoped cards when their operational gates differ.

## Security boundaries

- Local harness session data stays on the originating host and user account.
- Never copy raw transcripts, prompts, responses, workspace paths, session identifiers, credentials, or capability tokens to the central collector.
- Upstream social submission, autosubmit, login, account, credential, remote synchronization, subprocess capture, and TUI paths remain blocked unless an explicit reviewed task changes the policy.
- Run collectors as the harness user. Never use a root process to scan every home directory.
- Central reports use CapAuth, least-privilege report capabilities, idempotency keys, append-only observations, and an outbox.
- Keep harness-reported and gateway-observed measurement lanes separate.
- Fail closed for unknown backend commands and unknown snapshot schema versions.

## Engineering rules

- Pin provider dependencies exactly and commit the lockfile.
- Keep provider-specific behavior behind `src/providers/`.
- Add tests for policy, adapter, schema, and failure behavior with each change.
- Use `rg` for repository search and `apply_patch` for edits.
- Never add secrets to source, logs, fixtures, documentation, command arguments, or workflow output.
- Never use an em dash or en dash in chat, code comments, documentation, or generated artifacts.
- Do not deploy to additional nodes, enable timers, create credentials, or activate the central collector unless the assigned card authorizes it.
