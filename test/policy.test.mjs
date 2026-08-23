import assert from "node:assert/strict";
import test from "node:test";

import {
  enforceLocalOnlyPolicy,
  PolicyError,
} from "../src/policy.mjs";

test("implicit report is a static model report", () => {
  assert.deepEqual(enforceLocalOnlyPolicy([]), {
    args: ["models", "--light", "--no-spinner"],
    command: "models",
  });
});

test("allowed arguments are forwarded with deterministic display flags", () => {
  assert.deepEqual(enforceLocalOnlyPolicy(["monthly", "--json"]), {
    args: ["monthly", "--json", "--no-spinner"],
    command: "monthly",
  });
});

test("report always disables LLM summarization", () => {
  assert.deepEqual(enforceLocalOnlyPolicy(["report", "--full"]), {
    args: ["report", "--full", "--no-summarize"],
    command: "report",
  });
});

for (const command of ["submit", "autosubmit", "login", "usage", "tui", "codex"]) {
  test(`blocked command fails closed: ${command}`, () => {
    assert.throws(
      () => enforceLocalOnlyPolicy([command]),
      (error) => error instanceof PolicyError && error.message.startsWith(`${command}:`),
    );
  });
}

test("unknown future command fails closed", () => {
  assert.throws(
    () => enforceLocalOnlyPolicy(["future-command"]),
    /unknown command 'future-command'/,
  );
});
