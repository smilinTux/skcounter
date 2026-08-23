# Contributing to SKCounter

SKCounter is a privacy-sensitive facade over local AI harness stores. Read [SOP.md](./SOP.md), [SECURITY.md](./SECURITY.md), and [AGENTS.md](./AGENTS.md) before changing code.

## Coordination

Claim an eligible SKCapstone card before modifying the repository. Central collector work, fleet rollout, provider replacement, and policy expansion require separate cards because their operational gates differ.

## Setup

```bash
git clone https://github.com/smilinTux/skcounter.git
cd skcounter
npm ci --ignore-scripts
npm test
```

Use Node.js 20 or newer. Do not run the test suite as root.

## Branch model

- `main` must remain releasable.
- Create one focused branch per change using `feat/`, `fix/`, `docs/`, `refactor/`, `chore/`, or `ci/`.
- Open a pull request against `main`.
- Do not mix provider changes, central collector changes, or fleet activation into an unrelated facade change.
- Delete merged branches and dedicated worktrees after main and CI are verified.

The initial v0.1.0 repository bootstrap may land directly on an empty remote. All later code changes use pull requests.

## Commit convention

Use Conventional Commits with imperative subjects, for example:

```text
feat(snapshot): add a provider-neutral aggregate view
fix(policy): reject a newly introduced upstream command
docs: verify the deployment rollback procedure
```

Reference the SKCapstone card in the commit body when applicable. An AI-assisted commit includes an accurate trailer, for example:

```text
Co-Authored-By: OpenAI Codex <noreply@openai.com>
```

Do not use em dashes or en dashes in chat, code, comments, commits, or documentation.

## Test gate

Run the complete local gate:

```bash
npm ci --ignore-scripts
npm test
npm audit --omit=dev
npm run test:package
node --check bin/skcounter.mjs
node --check src/cli.mjs
node --check src/snapshot.mjs
```

Run the sk-standards gates from a local `sk-standards` checkout:

```bash
python3 ../sk-standards/scripts/docs_check.py --repo . --tier 1 --tier 2 --tier 3
python3 ../sk-standards/scripts/docs_check.py --self-test
python3 ../sk-standards/scripts/ci_gate_check.py --self-test
python3 ../sk-standards/scripts/ci_gate_check.py audit --repo .
```

New logic requires tests. A bug fix requires a regression test that fails for the original defect and passes after the repair. A provider adapter change must cover argument forwarding, environment isolation, exit status, malformed output, and policy interaction.

## Documentation gate

- Code changes update `CHANGELOG.md` under `[Unreleased]`.
- Any command, path, version, schema, or deployment change updates `SOP.md` in the same pull request.
- Keep the `SOP.md` `docs-evidence` checks hermetic and cheap.
- When changing an evidence check, prove the gate can fail, restore the fact, and record both results in the pull request.
- Keep README as the hub. Link canonical facts instead of copying them.

## Security boundaries

- Never commit live harness content, observations, source paths, workspace paths, raw session identifiers, credentials, or capability tokens.
- Never add a root scanner or traverse every home directory.
- Do not enable upstream social, account, credential, quota, synchronization, subprocess, summarization, or TUI commands without a dedicated security-reviewed task.
- Unknown backend commands remain fail closed.
- Keep `harness_reported` and `gateway_observed` separate.
- Preserve upstream Tokscale license attribution and the exact dependency pin.

## Pull request checklist

- The assigned coordination card is linked.
- Tests, package checks, audits, docs checks, negative controls, and secret scan pass.
- `CHANGELOG.md` describes the user-visible effect.
- `SOP.md` remains accurate.
- No protected data or secrets are present.
- Rollback is documented for any installation, scheduling, or data change.
- Provider-specific behavior stays behind `src/providers/`.
- Reviewers can map every release claim to a named test or command.

## Security issues

Do not open a public issue for a vulnerability. Follow [SECURITY.md](./SECURITY.md).

## Code of conduct

Participation is governed by [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md).
