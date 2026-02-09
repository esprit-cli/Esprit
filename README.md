<div align="center">

# Esprit

### Open-source AI hackers for your apps.

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

</div>

---

## Install

```bash
curl -fsSL https://esprit.dev/install.sh | bash
```

## Quick Start (From Source)

**Prerequisites:**
- Python 3.12+
- [Poetry](https://python-poetry.org/docs/#installation)
- Docker (running, required for scans)

### Install & Run

```bash
poetry install

# Launch the setup launchpad
poetry run esprit
```

This opens the interactive launchpad where you can:

- **Model Config** — pick your default LLM (GPT-5, Claude Sonnet, Gemini, etc.)
- **Provider Config** — connect to OpenAI, Anthropic, Google, or GitHub Copilot via OAuth or your own API key
- **Scan Mode** — choose quick, standard, or deep
- **Scan** — enter a target and start a penetration test

### Direct Commands

```bash
# Skip the launchpad and scan directly
poetry run esprit scan https://your-app.com
poetry run esprit scan ./local-codebase
poetry run esprit scan https://github.com/org/repo

# Provider management
poetry run esprit provider login       # OAuth login
poetry run esprit provider api-key     # Set your own API key
poetry run esprit provider status      # See connected providers
poetry run esprit provider logout      # Remove credentials

# Account
poetry run esprit login                # Log in to Esprit platform
poetry run esprit whoami               # Show current user
poetry run esprit status               # Account status

# Help
poetry run esprit --help
```

### Configuration

Configuration is stored in `~/.esprit/`:

| File | Purpose |
|------|---------|
| `providers.json` | OAuth tokens and API keys per provider |
| `config.json` | Default model selection |
| `cli-config.json` | Saved environment variables |
| `credentials.json` | Esprit platform account |

You can also configure via environment variables:

```bash
export ESPRIT_LLM="openai/gpt-5"          # default model
export LLM_API_KEY="sk-..."               # API key (alternative to provider config)
export LLM_API_BASE="http://localhost:11434" # for local models (Ollama, LMStudio)
export PERPLEXITY_API_KEY="pplx-..."       # optional, for search capabilities
```

### Supported Models

- **OpenAI** — `openai/gpt-5`, `openai/o3`, `openai/gpt-4.1`
- **Anthropic** — `anthropic/claude-sonnet-4-5`, `anthropic/claude-sonnet-4-20250514`
- **Google** — `vertex_ai/gemini-3-pro-preview`
- **GitHub Copilot** — `github-copilot/gpt-5`
- **Local** — Any model via Ollama/LMStudio using `LLM_API_BASE`

### Advanced Usage

```bash
# Authenticated testing
poetry run esprit scan https://app.com --instruction "Use credentials admin:pass123"

# Multi-target
poetry run esprit scan -t https://github.com/org/app -t https://app.com

# Focused testing
poetry run esprit scan https://api.app.com --instruction "Focus on IDOR and auth bypass"

# Non-interactive / headless (for CI)
poetry run esprit scan -n --target https://app.com --scan-mode quick
```

---

## Project Structure

```
esprit/
├── interface/       # CLI entry point, TUI, launchpad
├── providers/       # OAuth + API key auth for LLM providers
├── config/          # Environment + config file management
├── llm/             # LiteLLM integration, model config
├── auth/            # Esprit platform authentication
├── agents/          # AI agent orchestration
├── tools/           # Browser, terminal, proxy, code analysis
├── runtime/         # Docker sandbox management
├── skills/          # Scan strategies and vuln knowledge
└── telemetry/       # Usage tracking
```

---

> **Warning:** Only test applications you own or have explicit permission to test.
