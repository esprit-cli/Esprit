# Esprit

**AI-Powered Penetration Testing Agent**

Esprit is an autonomous security assessment tool that uses AI agents to perform comprehensive penetration tests. It can analyze web applications, APIs, code repositories, and network targets with minimal human intervention.

---

## Quick Start

### Option 1: Install with curl

```bash
curl -fsSL https://esprit.dev/install.sh | bash
```

### Option 2: Homebrew

```bash
brew tap improdead/esprit
brew install esprit
```

### Option 3: From Source

```bash
git clone https://github.com/improdead/Esprit.git
cd Esprit
pip install poetry
poetry install
```

---

## Choose Your Setup

Esprit supports two runtime modes depending on how you want to run scans.

### Esprit Cloud (No Docker Required)

Use your Esprit subscription to run scans entirely in the cloud. No Docker, no local setup.

```bash
# 1. Login with your Esprit account (opens browser)
esprit provider login esprit

# 2. Run a scan — that's it
esprit scan https://example.com
```

When authenticated with a paid plan (Pro, Team, or Enterprise), Esprit automatically routes scans to cloud sandboxes. You'll see:

```
✓ Using Esprit Cloud (no Docker required)
Plan: PRO
Quota: scans 100  |  tokens 500,000
```

**Available models via Esprit Cloud:**

| Name | Alias | Description |
|------|-------|-------------|
| Esprit Default | `esprit/default` | Default model (Haiku 4.5) |
| Esprit Pro | `esprit/kimi-k2.5` | Advanced model (Kimi K2.5) |
| Esprit Fast | `esprit/haiku` | Fast, lightweight scans |

### Local Mode (Docker)

Use any LLM provider with your own API keys. Requires Docker for the pentest sandbox.

```bash
# 1. Install Docker: https://docs.docker.com/get-docker/

# 2. Connect a provider (pick one)
esprit provider login anthropic       # Claude (OAuth)
esprit provider login openai          # GPT / Codex (OAuth)
esprit provider login google          # Gemini (OAuth)
esprit provider login github-copilot  # Copilot (OAuth)

# Or set an API key directly
export ESPRIT_LLM="anthropic/claude-sonnet-4-5-20250514"
export LLM_API_KEY="sk-ant-..."

# 3. Run a scan
esprit scan https://example.com
```

> **Free tier:** Use `esprit provider login antigravity` for free access to Claude and Gemini models (no API key needed). Docker is still required.

---

## Usage

### Scan Targets

```bash
# Web application
esprit scan https://api.example.com

# GitHub repository (white-box)
esprit scan https://github.com/user/repo

# Local codebase
esprit scan ./my-project

# Multiple targets
esprit scan https://api.example.com https://github.com/user/repo
```

### Scan Modes

```bash
esprit scan https://example.com -m quick      # Fast surface-level scan
esprit scan https://example.com -m standard   # Balanced scan
esprit scan https://example.com -m deep       # Comprehensive (default)
```

### Custom Instructions

```bash
esprit scan https://example.com --instruction "Focus on authentication and JWT vulnerabilities"
esprit scan https://example.com --instruction-file ./instructions.txt
```

### Non-Interactive Mode (CI/CD)

```bash
esprit scan https://example.com --non-interactive

# Exit codes:
# 0 = No vulnerabilities found
# 2 = Vulnerabilities found
```

### Provider Management

```bash
esprit provider login              # Interactive provider selection
esprit provider login esprit       # Esprit Cloud subscription
esprit provider login openai       # OpenAI Codex (OAuth)
esprit provider login anthropic    # Anthropic Claude (OAuth)
esprit provider login google       # Google Gemini (OAuth)
esprit provider login github-copilot

esprit provider status             # Check all connected providers
esprit provider logout <provider>  # Disconnect a provider
```

---

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ESPRIT_LLM` | No | LLM model (auto-detected from provider) |
| `LLM_API_KEY` | No* | API key for direct LLM access |
| `LLM_API_BASE` | No | Custom API endpoint (Ollama, etc.) |
| `ESPRIT_IMAGE` | No | Custom Docker sandbox image |
| `PERPLEXITY_API_KEY` | No | Enables web search during scans |

*Not required when using OAuth providers or Esprit Cloud.

### Supported Providers

| Provider | Auth | Docker Required | Models |
|----------|------|----------------|--------|
| **Esprit** (Cloud) | OAuth | No | Esprit Default, Esprit Pro, Esprit Fast |
| **Anthropic** | OAuth / API key | Yes | Claude Sonnet 4.5, Opus 4.5, Haiku 4.5 |
| **OpenAI** | OAuth / API key | Yes | GPT-5.3 Codex, GPT-5.2, GPT-5.1 |
| **Google** | OAuth / API key | Yes | Gemini 3 Pro, Gemini 3 Flash |
| **GitHub Copilot** | OAuth | Yes | GPT-5, Claude Sonnet 4.5 |
| **Antigravity** | OAuth (free) | Yes | Claude Opus 4.6, Gemini 3 Pro |
| **Ollama** | Local | Yes | Any local model |

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                      ESPRIT CLI                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │  EspritAgent (AI Core)                             │  │
│  │  - Multi-turn LLM conversation                     │  │
│  │  - Native tool calling (JSON schemas)              │  │
│  │  - Multi-agent orchestration                       │  │
│  └──────────────────────┬─────────────────────────────┘  │
│                         │                                 │
│  ┌──────────────────────┴─────────────────────────────┐  │
│  │  Tools: Browser, Terminal, Proxy, Python,          │  │
│  │         File Editor, Reporting, Web Search         │  │
│  └────────────────────────────────────────────────────┘  │
└───────────────────┬──────────────────┬───────────────────┘
                    │                  │
          ┌─────────┘                  └─────────┐
          ▼                                      ▼
┌──────────────────────┐          ┌──────────────────────┐
│  Docker Sandbox      │          │  Esprit Cloud        │
│  (Local)             │          │  (Remote)            │
│                      │          │                      │
│  - Caido proxy       │          │  - No Docker needed  │
│  - Playwright        │          │  - Managed sandbox   │
│  - nmap, sqlmap,     │          │  - Auto-cleanup      │
│    nuclei, ffuf...   │          │  - Paid plans only   │
└──────────────────────┘          └──────────────────────┘
```

---

## Vulnerability Detection

- SQL Injection
- Cross-Site Scripting (XSS)
- Authentication & JWT Flaws
- IDOR & Broken Access Control
- SSRF & Path Traversal
- Race Conditions
- Business Logic Vulnerabilities
- Mass Assignment
- CSRF
- Open Redirects
- Information Disclosure
- And more...

---

## Development

```bash
git clone https://github.com/improdead/Esprit.git
cd Esprit
poetry install

# Run tests
poetry run pytest

# Run linting
poetry run ruff check .

# Run a scan in dev
poetry run esprit scan https://example.com
```

---

## Security

Esprit is designed for **authorized security testing only**.

- Only test systems you own or have explicit written permission to test
- Sandboxed execution prevents damage to your local system
- All scan results are stored locally
- No data is shared with third parties

---

## License

MIT License - see [LICENSE](LICENSE) for details.

---

## Support

- **Issues**: https://github.com/improdead/Esprit/issues
- **Website**: https://esprit.dev
