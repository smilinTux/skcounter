import { DEFAULT_BACKEND } from "./constants.mjs";
import { createTokscaleBackend } from "./providers/tokscale.mjs";

export function resolveBackend(name = DEFAULT_BACKEND, dependencies = {}) {
  if (name === "tokscale") {
    return createTokscaleBackend(dependencies.tokscale);
  }
  throw new Error(`unsupported backend '${name}'`);
}
