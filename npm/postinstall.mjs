#!/usr/bin/env node

import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const installerPath = path.resolve(__dirname, "..", "scripts", "install.sh");

if (process.platform === "win32") {
  process.stdout.write("[esprit] npm install currently supports macOS/Linux. Use WSL on Windows.\n");
  process.exit(0);
}

const result = spawnSync("bash", [installerPath], {
  stdio: "inherit",
  env: {
    ...process.env,
    ESPRIT_SKIP_DOCKER_WARM: process.env.ESPRIT_SKIP_DOCKER_WARM || "1",
  },
});

if (result.status !== 0) {
  process.exit(result.status || 1);
}
