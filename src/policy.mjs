const ALLOWED_COMMANDS = new Set([
  "clients",
  "graph",
  "hourly",
  "models",
  "monthly",
  "pricing",
  "report",
  "time-metrics",
]);

const BLOCKED_COMMANDS = new Map([
  ["antigravity", "remote synchronization is outside the local accounting boundary"],
  ["autosubmit", "automatic submission is disabled by policy"],
  ["codex", "credential and subscription account operations are disabled by policy"],
  ["config", "the facade owns backend configuration"],
  ["cursor", "credential and remote synchronization operations are disabled by policy"],
  ["delete-submitted-data", "server-side account operations are disabled by policy"],
  ["headless", "subprocess execution is outside the accounting-only boundary"],
  ["import", "backend-native import is not part of the stable facade contract"],
  ["login", "upstream social login is disabled by policy"],
  ["logout", "upstream social account operations are disabled by policy"],
  ["qr", "credential display is disabled by policy"],
  ["submit", "usage submission to the upstream social platform is disabled by policy"],
  ["trae", "credential and remote synchronization operations are disabled by policy"],
  ["tui", "the upstream TUI can perform background quota requests and retains upstream branding"],
  ["usage", "remote subscription quota requests are disabled by policy"],
  ["warm-tui-cache", "the upstream TUI path is disabled by policy"],
  ["warp", "credential and remote synchronization operations are disabled by policy"],
  ["whoami", "upstream social account operations are disabled by policy"],
  ["wrapped", "upstream-branded generated artifacts are outside the SKCounter facade"],
]);

const SPINNER_COMMANDS = new Set([
  "graph",
  "hourly",
  "models",
  "monthly",
  "pricing",
  "time-metrics",
]);

export class PolicyError extends Error {
  constructor(message) {
    super(message);
    this.name = "PolicyError";
  }
}

export function detectCommand(argv) {
  const known = new Set([...ALLOWED_COMMANDS, ...BLOCKED_COMMANDS.keys()]);
  const first = argv[0]?.toLowerCase();
  return first && known.has(first) ? first : null;
}

export function enforceLocalOnlyPolicy(argv) {
  const args = [...argv];
  const command = detectCommand(args);

  if (command && BLOCKED_COMMANDS.has(command)) {
    throw new PolicyError(`${command}: ${BLOCKED_COMMANDS.get(command)}`);
  }

  if (!command) {
    const first = args[0];
    if (first && !first.startsWith("-")) {
      throw new PolicyError(`unknown command '${first}'; new backend commands fail closed`);
    }

    args.unshift("models");
  }

  const effectiveCommand = command ?? "models";
  if (!ALLOWED_COMMANDS.has(effectiveCommand)) {
    throw new PolicyError(`command '${effectiveCommand}' is not allowed`);
  }

  if (["models", "monthly", "hourly"].includes(effectiveCommand)) {
    if (!args.includes("--light") && !args.includes("--json")) {
      args.push("--light");
    }
  }

  if (effectiveCommand === "report") {
    if (args.includes("--summarizer")) {
      throw new PolicyError("report: external summarizers are disabled by policy");
    }
    if (!args.includes("--no-summarize")) {
      args.push("--no-summarize");
    }
  }

  if (SPINNER_COMMANDS.has(effectiveCommand) && !args.includes("--no-spinner")) {
    args.push("--no-spinner");
  }

  return { args, command: effectiveCommand };
}
