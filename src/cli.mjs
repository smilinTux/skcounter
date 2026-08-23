import { resolveBackend } from "./backends.mjs";
import {
  DEFAULT_BACKEND,
  SKCOUNTER_VERSION,
} from "./constants.mjs";
import { buildDoctorReport } from "./doctor.mjs";
import { HELP_TEXT } from "./help.mjs";
import { enforceLocalOnlyPolicy, PolicyError } from "./policy.mjs";
import { collectSnapshot, parseCollectionOptions } from "./snapshot.mjs";
import { writeObservation } from "./storage.mjs";

function hasJsonFlag(argv) {
  return argv.includes("--json");
}

function writeObject(value, output) {
  output(`${JSON.stringify(value, null, 2)}\n`);
}

export async function run(
  argv,
  {
    backendName = process.env.SKCOUNTER_BACKEND ?? DEFAULT_BACKEND,
    backend,
    output = (value) => process.stdout.write(value),
    errorOutput = (value) => process.stderr.write(value),
    home = process.env.HOME,
    now = new Date(),
    observationWriter = writeObservation,
  } = {},
) {
  let selectedBackend;
  try {
    selectedBackend = backend ?? resolveBackend(backendName);
  } catch (error) {
    errorOutput(`SKCounter backend error: ${error.message}\n`);
    return 1;
  }

  if (argv.length === 1 && ["--version", "-V"].includes(argv[0])) {
    output(
      `skcounter ${SKCOUNTER_VERSION} (backend ${selectedBackend.id} ${selectedBackend.version})\n`,
    );
    return 0;
  }

  if (argv.length === 0 || argv.includes("--help") || argv.includes("-h")) {
    if (argv.length > 0) {
      output(HELP_TEXT);
      return 0;
    }
  }

  const facadeCommand = argv[0]?.toLowerCase();
  if (["collect", "snapshot"].includes(facadeCommand)) {
    try {
      const options = parseCollectionOptions(argv.slice(1), now);
      const snapshot = collectSnapshot({ backend: selectedBackend, options, now });
      if (facadeCommand === "snapshot") {
        output(`${JSON.stringify(snapshot, null, 2)}\n`);
      } else {
        const path = observationWriter(snapshot, { outputDir: options.outputDir || undefined });
        output(`SKCounter observation written: ${path}\n`);
      }
      return 0;
    } catch (error) {
      errorOutput(`SKCounter collection error: ${error.message}\n`);
      return 1;
    }
  }

  if (["backend", "doctor"].includes(facadeCommand)) {
    const asJson = hasJsonFlag(argv);
    const report =
      facadeCommand === "backend"
        ? {
            product: "skcounter",
            facade_version: SKCOUNTER_VERSION,
            backend: {
              id: selectedBackend.id,
              version: selectedBackend.version,
              source: selectedBackend.source,
            },
            policy: "local-only",
          }
        : buildDoctorReport({ home, backend: selectedBackend });

    if (asJson) {
      writeObject(report, output);
    } else if (facadeCommand === "backend") {
      output(
        `SKCounter ${SKCOUNTER_VERSION}\nBackend: ${selectedBackend.id} ${selectedBackend.version}\nPolicy: local-only\n`,
      );
    } else {
      const detected = report.sources
        .filter((source) => source.detected)
        .map((source) => source.client)
        .join(", ");
      output(
        `SKCounter doctor\nHost: ${report.host}\nBackend: ${report.backend.id} ${report.backend.version}\nPolicy: ${report.mode}\nDetected: ${detected || "none"}\n`,
      );
    }
    return 0;
  }

  let governed;
  try {
    governed = enforceLocalOnlyPolicy(argv);
  } catch (error) {
    const message = error instanceof PolicyError ? error.message : String(error);
    errorOutput(`SKCounter policy denied ${message}\n`);
    return 2;
  }

  if (!hasJsonFlag(governed.args)) {
    errorOutput(
      `SKCounter using ${selectedBackend.id} ${selectedBackend.version} in local-only mode\n`,
    );
  }

  const result = selectedBackend.execute(governed.args);
  if (result.error) {
    errorOutput(`SKCounter backend error: ${result.error}\n`);
  }
  return result.code;
}
