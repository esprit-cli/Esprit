#!/usr/bin/env node

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const isWindows = process.platform === "win32";
const binaryName = isWindows ? "esprit.exe" : "esprit";
const installDir = path.join(os.homedir(), ".esprit", "bin");
const binaryPath = path.join(installDir, binaryName);
const installerPath = path.resolve(
  __dirname,
  "..",
  "scripts",
  isWindows ? "install.ps1" : "install.sh"
);

function ensureInstalled() {
  if (fs.existsSync(binaryPath)) {
    return;
  }

  const bootstrapEnv = {
    ...process.env,
    ESPRIT_SKIP_DOCKER_WARM: process.env.ESPRIT_SKIP_DOCKER_WARM || "1",
  };

  const bootstrap = isWindows
    ? spawnSync(
        "powershell",
        ["-ExecutionPolicy", "Bypass", "-File", installerPath],
        { stdio: "inherit", env: bootstrapEnv }
      )
    : spawnSync("bash", [installerPath], {
        stdio: "inherit",
        env: bootstrapEnv,
      });

  if (bootstrap.status !== 0) {
    process.exit(bootstrap.status || 1);
  }
}

ensureInstalled();

const result = spawnSync(binaryPath, process.argv.slice(2), { stdio: "inherit" });
if (result.error) {
  console.error(`[esprit] failed to execute binary: ${result.error.message}`);
  process.exit(1);
}
process.exit(result.status || 0);
