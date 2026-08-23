import { existsSync } from "node:fs";
import { join } from "node:path";
import { hostname } from "node:os";

const SESSION_ROOTS = [
  ["codex", [".codex", "sessions"]],
  ["claude", [".claude", "projects"]],
  ["opencode", [".local", "share", "opencode"]],
  ["openclaw", [".openclaw", "agents"]],
  ["kimi", [".kimi", "sessions"]],
  ["qwen", [".qwen", "projects"]],
  ["zcode", [".zcode", "projects"]],
];

export function buildDoctorReport({ home = process.env.HOME, backend }) {
  if (!home) {
    throw new Error("HOME is required for the local collector");
  }

  const sources = SESSION_ROOTS.map(([client, segments]) => ({
    client,
    detected: existsSync(join(home, ...segments)),
  }));

  return {
    product: "skcounter",
    host: hostname(),
    mode: "local-only",
    backend: {
      id: backend.id,
      version: backend.version,
    },
    sources,
  };
}
