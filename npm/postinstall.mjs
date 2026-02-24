#!/usr/bin/env node

import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, "..");
const packageJsonPath = path.join(projectRoot, "package.json");
const installDir = path.join(projectRoot, "npm", ".esprit-bin");
const repo = "improdead/Esprit";
const sandboxImage = process.env.ESPRIT_IMAGE || "improdead/esprit-sandbox:latest";

function getTarget() {
  let platform = os.platform();
  let arch = os.arch();

  if (platform === "darwin") {
    platform = "macos";
  } else if (platform === "linux") {
    platform = "linux";
  } else if (platform === "win32") {
    platform = "windows";
  } else {
    throw new Error(`Unsupported platform: ${platform}`);
  }

  if (arch === "arm64") {
    arch = "arm64";
  } else if (arch === "x64") {
    arch = "x86_64";
  } else {
    throw new Error(`Unsupported architecture: ${arch}`);
  }

  if (platform === "windows" && arch !== "x86_64") {
    throw new Error(`Unsupported platform/arch: ${platform}/${arch}`);
  }

  return { platform, arch, target: `${platform}-${arch}` };
}

function buildAssetName(version, target) {
  const ext = target.startsWith("windows-") ? ".zip" : ".tar.gz";
  return `esprit-${version}-${target}${ext}`;
}

async function downloadFile(url, destPath) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Download failed (${response.status}) ${url}`);
  }
  const arrayBuffer = await response.arrayBuffer();
  await fs.writeFile(destPath, Buffer.from(arrayBuffer));
}

async function ensureDir(dir) {
  await fs.mkdir(dir, { recursive: true });
}

function runCommand(command, args) {
  const result = spawnSync(command, args, { stdio: "inherit" });
  if (result.status !== 0) {
    throw new Error(`Command failed: ${command} ${args.join(" ")}`);
  }
}

function runCommandCapture(command, args) {
  return spawnSync(command, args, { encoding: "utf8" });
}

function extractArchive(archivePath, extractDir) {
  if (archivePath.endsWith(".zip")) {
    if (process.platform === "win32") {
      runCommand("powershell", [
        "-NoProfile",
        "-Command",
        `Expand-Archive -Path "${archivePath}" -DestinationPath "${extractDir}" -Force`,
      ]);
    } else {
      runCommand("unzip", ["-o", archivePath, "-d", extractDir]);
    }
    return;
  }

  runCommand("tar", ["-xzf", archivePath, "-C", extractDir]);
}

function warmSandboxImage() {
  const dockerInfo = runCommandCapture("docker", ["info"]);
  if (dockerInfo.error || dockerInfo.status !== 0) {
    return;
  }

  const inspect = runCommandCapture("docker", ["image", "inspect", sandboxImage]);
  if (inspect.status === 0) {
    process.stdout.write("[esprit] sandbox image already present\n");
    return;
  }

  process.stdout.write(`[esprit] pulling sandbox image ${sandboxImage}\n`);
  let pull = runCommandCapture("docker", ["pull", sandboxImage]);
  if (pull.status === 0) {
    process.stdout.write("[esprit] sandbox image ready\n");
    return;
  }

  const pullText = `${pull.stdout || ""}\n${pull.stderr || ""}`.toLowerCase();
  const missingArmManifest = pullText.includes("no matching manifest") && pullText.includes("arm64");
  if (os.arch() === "arm64" && missingArmManifest) {
    process.stdout.write("[esprit] retrying sandbox pull with linux/amd64 emulation\n");
    pull = runCommandCapture("docker", ["pull", "--platform", "linux/amd64", sandboxImage]);
    if (pull.status === 0) {
      process.stdout.write("[esprit] sandbox image ready (linux/amd64)\n");
      return;
    }
  }

  process.stdout.write("[esprit] sandbox image pull skipped (will retry at first scan)\n");
}

async function installBinary() {
  const pkg = JSON.parse(await fs.readFile(packageJsonPath, "utf8"));
  const version = process.env.ESPRIT_VERSION || pkg.version;
  const { target } = getTarget();
  const assetName = buildAssetName(version, target);
  const downloadUrl = `https://github.com/${repo}/releases/download/v${version}/${assetName}`;

  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), "esprit-npm-"));
  const archivePath = path.join(tempRoot, assetName);
  const extractDir = path.join(tempRoot, "extract");
  await ensureDir(extractDir);

  try {
    process.stdout.write(`[esprit] downloading ${assetName}\n`);
    await downloadFile(downloadUrl, archivePath);
    extractArchive(archivePath, extractDir);

    const binaryName = target.startsWith("windows-") ? "esprit.exe" : "esprit";
    const extractedPath = path.join(extractDir, binaryName);
    await fs.access(extractedPath);

    await ensureDir(installDir);
    const outputPath = path.join(installDir, binaryName);
    await fs.copyFile(extractedPath, outputPath);

    if (!target.startsWith("windows-")) {
      await fs.chmod(outputPath, 0o755);
    }

    await fs.writeFile(path.join(installDir, "VERSION"), `${version}\n`, "utf8");
    process.stdout.write(`[esprit] installed ${binaryName}\n`);
    warmSandboxImage();
  } finally {
    await fs.rm(tempRoot, { recursive: true, force: true });
  }
}

installBinary().catch((error) => {
  process.stderr.write(`[esprit] install failed: ${error.message}\n`);
  process.exit(1);
});
