#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const isWindows = process.platform === "win32";
const binaryName = isWindows ? "esprit.exe" : "esprit";
const installDir = path.resolve(__dirname, "..", "npm", ".esprit-bin");
const binaryPath = path.join(installDir, binaryName);
const installerPath = path.resolve(__dirname, "..", "npm", "postinstall.mjs");

function ensureInstalled() {
  if (fs.existsSync(binaryPath)) {
    return;
  }

  const bootstrap = spawnSync(process.execPath, [installerPath], {
    stdio: "inherit",
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
