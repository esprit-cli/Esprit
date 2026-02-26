#Requires -Version 5.1
<#
.SYNOPSIS
    Installs the Esprit runtime on Windows.
.DESCRIPTION
    Mirrors the functionality of install.sh for Windows/PowerShell:
      1. Clones Esprit repo to ~/.esprit/runtime
      2. Creates a Python venv at ~/.esprit/venv
      3. Installs dependencies via pip
      4. Creates an esprit.cmd launcher
      5. Adds the bin dir to user PATH
      6. Optionally warms the Docker image
#>

param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$APP          = 'esprit'
$REPO_URL     = if ($env:ESPRIT_REPO_URL)  { $env:ESPRIT_REPO_URL }  else { 'https://github.com/esprit-cli/Esprit.git' }
$REPO_REF     = if ($env:ESPRIT_REPO_REF)  { $env:ESPRIT_REPO_REF }  else { 'main' }
$INSTALL_ROOT = if ($env:ESPRIT_HOME)      { $env:ESPRIT_HOME }      else { Join-Path $env:USERPROFILE '.esprit' }
$BIN_DIR      = Join-Path $INSTALL_ROOT 'bin'
$RUNTIME_DIR  = Join-Path $INSTALL_ROOT 'runtime'
$VENV_DIR     = Join-Path $INSTALL_ROOT 'venv'
$LAUNCHER_CMD = Join-Path $BIN_DIR 'esprit.cmd'
$ESPRIT_IMAGE = if ($env:ESPRIT_IMAGE)     { $env:ESPRIT_IMAGE }     else { 'improdead/esprit-sandbox:latest' }

function Print-Message {
    param([string]$Level, [string]$Message)
    switch ($Level) {
        'success' { Write-Host $Message -ForegroundColor Green }
        'warning' { Write-Host $Message -ForegroundColor Yellow }
        'error'   { Write-Host $Message -ForegroundColor Red }
        'info'    { Write-Host $Message -ForegroundColor DarkGray }
        default   { Write-Host $Message }
    }
}

function Require-Command {
    param([string]$Cmd, [string]$Hint)
    if (-not (Get-Command $Cmd -ErrorAction SilentlyContinue)) {
        Print-Message 'error' "Missing required command: $Cmd"
        Print-Message 'info'  $Hint
        exit 1
    }
}

function Choose-Python {
    # Try py launcher first (standard on Windows), then bare python
    foreach ($candidate in @('py -3.13', 'py -3.12', 'python')) {
        try {
            $parts = $candidate -split ' ', 2
            $exe  = $parts[0]
            $args = if ($parts.Length -gt 1) { $parts[1] } else { $null }

            if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }

            $checkArgs = @()
            if ($args) { $checkArgs += $args }
            $checkArgs += '-c'
            $checkArgs += 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'

            $proc = & $exe @checkArgs 2>$null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch {
            continue
        }
    }
    return $null
}

function Invoke-Python {
    param([string]$Candidate, [string[]]$Arguments)
    $parts = $Candidate -split ' ', 2
    $exe   = $parts[0]
    $allArgs = @()
    if ($parts.Length -gt 1) { $allArgs += $parts[1] }
    $allArgs += $Arguments
    & $exe @allArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Command '$Candidate $($Arguments -join ' ')' failed with exit code $LASTEXITCODE"
    }
}

function Sync-RuntimeRepo {
    Print-Message 'info' 'Syncing Esprit runtime source...'

    if (Test-Path (Join-Path $RUNTIME_DIR '.git')) {
        & git -C $RUNTIME_DIR remote set-url origin $REPO_URL
        & git -C $RUNTIME_DIR fetch --depth 1 origin $REPO_REF
        if ($LASTEXITCODE -ne 0) { throw 'git fetch failed' }
        & git -C $RUNTIME_DIR checkout -q FETCH_HEAD
        if ($LASTEXITCODE -ne 0) { throw 'git checkout failed' }
    } else {
        if (Test-Path $RUNTIME_DIR) { Remove-Item -Recurse -Force $RUNTIME_DIR }
        & git clone --depth 1 --branch $REPO_REF $REPO_URL $RUNTIME_DIR
        if ($LASTEXITCODE -ne 0) { throw 'git clone failed' }
    }

    $commit = & git -C $RUNTIME_DIR rev-parse --short HEAD 2>$null
    if (-not $commit) { $commit = 'unknown' }
    Print-Message 'success' "✓ Runtime ready ($commit)"
}

function Install-PythonRuntime {
    param([string]$PyBin)

    if (-not (Test-Path $INSTALL_ROOT)) {
        New-Item -ItemType Directory -Path $INSTALL_ROOT -Force | Out-Null
    }

    $venvPython = Join-Path $VENV_DIR 'Scripts\python.exe'
    if ($Force -or -not (Test-Path $venvPython)) {
        Print-Message 'info' 'Creating virtual environment...'
        if (Test-Path $VENV_DIR) { Remove-Item -Recurse -Force $VENV_DIR }
        Invoke-Python $PyBin @('-m', 'venv', $VENV_DIR)
    }

    Print-Message 'info' 'Installing Esprit dependencies (this can take a few minutes)...'
    $venvPip = Join-Path $VENV_DIR 'Scripts\pip.exe'
    & $venvPython -m pip install --upgrade pip setuptools wheel --quiet
    if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed' }
    & $venvPip install --upgrade $RUNTIME_DIR --quiet
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
    Print-Message 'success' '✓ Python runtime installed'
}

function Write-Launcher {
    if (-not (Test-Path $BIN_DIR)) {
        New-Item -ItemType Directory -Path $BIN_DIR -Force | Out-Null
    }

    $launcherContent = @"
@echo off
setlocal
set "ROOT=%ESPRIT_HOME%"
if "%ROOT%"=="" set "ROOT=%USERPROFILE%\.esprit"
set "BIN=%ROOT%\venv\Scripts\esprit.exe"
if not exist "%BIN%" (
    echo Esprit runtime not found. Re-run the installer.
    exit /b 1
)
"%BIN%" %*
"@

    Set-Content -Path $LAUNCHER_CMD -Value $launcherContent -Encoding ASCII
    Print-Message 'success' "✓ Installed launcher at $LAUNCHER_CMD"
}

function Setup-Path {
    $currentPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($currentPath -and ($currentPath -split ';' | ForEach-Object { $_.TrimEnd('\') }) -contains $BIN_DIR.TrimEnd('\')) {
        return
    }

    $newPath = "$BIN_DIR;$currentPath"
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    # Also update the current session
    $env:Path = "$BIN_DIR;$env:Path"
    Print-Message 'info' "Added $BIN_DIR to user PATH"
}

function Warm-DockerImage {
    if ($env:ESPRIT_SKIP_DOCKER_WARM -eq '1') { return }

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Print-Message 'warning' 'Docker not found (required for local/provider scans).'
        Print-Message 'info'    'Esprit Cloud scans still work without Docker.'
        return
    }

    $null = & docker info 2>&1
    if ($LASTEXITCODE -ne 0) {
        Print-Message 'warning' 'Docker daemon is not running.'
        Print-Message 'info'    'Start Docker for local/provider scans.'
        return
    }

    Print-Message 'info' 'Pulling sandbox image (optional warm-up)...'
    $pullOutput = & docker pull $ESPRIT_IMAGE 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        Print-Message 'success' '✓ Sandbox image ready'
        return
    }

    Write-Host $pullOutput
    Print-Message 'warning' 'Sandbox pull skipped (will retry at first local scan).'
}

# ── Main ──────────────────────────────────────────────────────────────────────

try {
    Require-Command 'git' 'Install git and re-run the installer.'

    $pyBin = Choose-Python
    if (-not $pyBin) {
        Print-Message 'error' 'Python 3.12+ is required.'
        Print-Message 'info'  'Install Python 3.12 and re-run this installer.'
        exit 1
    }

    Print-Message 'info' "Installing Esprit (source mode)"
    Print-Message 'info' "Runtime source: $REPO_URL@$REPO_REF"
    Print-Message 'info' "Install root:   $INSTALL_ROOT"

    Sync-RuntimeRepo
    Install-PythonRuntime -PyBin $pyBin
    Write-Launcher
    Setup-Path
    Warm-DockerImage

    $version = 'unknown'
    try {
        $version = & $LAUNCHER_CMD --version 2>$null
        if (-not $version) { $version = 'unknown' }
    } catch { }
    Print-Message 'success' "✓ $version ready"

    Write-Host ''
    Print-Message 'info' 'You may need to restart your terminal for PATH changes to take effect.'
    Print-Message 'info' "  $APP --help"
} catch {
    Print-Message 'error' "Installation failed: $_"
    exit 1
}
