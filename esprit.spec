# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Esprit CLI."""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Project root
ROOT = os.path.abspath(os.path.dirname(SPEC))
ESPRIT_PKG = os.path.join(ROOT, "esprit")

# Collect non-Python data files that must be bundled
datas = []

# Agent prompt templates (.jinja)
for jinja in Path(ESPRIT_PKG).rglob("*.jinja"):
    datas.append((str(jinja), str(jinja.parent.relative_to(ROOT))))

# Tool XML schemas
for xml in Path(ESPRIT_PKG).rglob("*_schema.xml"):
    datas.append((str(xml), str(xml.parent.relative_to(ROOT))))

# TUI stylesheets (.tcss)
for tcss in Path(ESPRIT_PKG).rglob("*.tcss"):
    datas.append((str(tcss), str(tcss.parent.relative_to(ROOT))))

# Skills (.md files)
skills_dir = os.path.join(ESPRIT_PKG, "skills")
if os.path.isdir(skills_dir):
    for md in Path(skills_dir).rglob("*.md"):
        datas.append((str(md), str(md.parent.relative_to(ROOT))))

# LiteLLM data files (model cost maps, etc.)
datas += collect_data_files("litellm")

# Hidden imports that PyInstaller can't detect automatically
hiddenimports = [
    # Core
    "esprit.interface.main",
    "esprit.interface.cli",
    "esprit.interface.tui",
    "esprit.interface.launchpad",
    # Agents
    "esprit.agents.base_agent",
    "esprit.agents.EspritAgent",
    "esprit.agents.EspritAgent.esprit_agent",
    # Auth
    "esprit.auth",
    "esprit.auth.credentials",
    "esprit.auth.client",
    # Providers
    "esprit.providers.esprit_subs",
    "esprit.providers.antigravity",
    "esprit.providers.litellm_integration",
    # Runtime
    "esprit.runtime.docker_runtime",
    "esprit.runtime.cloud_runtime",
    # Tools
    "esprit.tools.registry",
    "esprit.tools.executor",
    # LiteLLM internals
    "litellm",
    "litellm.llms",
    "litellm.llms.anthropic",
    "litellm.llms.openai",
    # Networking
    "httpx",
    "httpx._transports",
    "httpx._transports.default",
    "httpcore",
    "h11",
    "anyio",
    "anyio._backends",
    "anyio._backends._asyncio",
    "sniffio",
    # Rich / Textual (TUI)
    "rich",
    "textual",
    "textual.app",
    # Docker
    "docker",
    "docker.api",
    "docker.models",
    "docker.transport",
    # Other
    "requests",
    "pydantic",
    "xmltodict",
    "jinja2",
    "defusedxml",
    "cvss",
]

# Collect all litellm submodules (it has many dynamic imports)
hiddenimports += collect_submodules("litellm")

a = Analysis(
    [os.path.join(ESPRIT_PKG, "interface", "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude sandbox-only deps (not needed in CLI binary)
        "fastapi",
        "uvicorn",
        "playwright",
        "gql",
        "pyte",
        "libtmux",
        "openhands_aci",
        "numpydoc",
        "ipython",
        # Exclude dev tools
        "pytest",
        "mypy",
        "ruff",
        "pylint",
        "pyright",
        "bandit",
        "black",
        "isort",
        # Exclude heavy optional deps
        "google.cloud",
        "torch",
        "numpy",
        "pandas",
        "matplotlib",
        "scipy",
        "sklearn",
        "tensorflow",
    ],
    noarchive=False,
    optimize=0,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="esprit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
