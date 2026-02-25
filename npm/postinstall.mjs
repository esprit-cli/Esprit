#!/usr/bin/env node

import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const isWindows = process.platform === "win32";
const installerPath = path.resolve(
  __dirname,
  "..",
  "scripts",
  isWindows ? "install.ps1" : "install.sh"
);

const installEnv = {
  ...process.env,
  // npm installs should be fast/predictable; sandbox image is pulled at first scan.
  ESPRIT_SKIP_DOCKER_WARM: "1",
};

const result = isWindows
  ? spawnSync(
      "powershell",
      ["-ExecutionPolicy", "Bypass", "-File", installerPath],
      { stdio: "inherit", env: installEnv }
    )
  : spawnSync("bash", [installerPath], {
      stdio: "inherit",
      env: installEnv,
    });

if (result.status !== 0) {
  process.exit(result.status || 1);
}
