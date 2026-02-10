# Antigravity Provider

Antigravity gives Esprit users **free access to Claude and Gemini models** through Google's Cloud Code API. No API keys needed — just sign in with your Google account.

## How it works

Antigravity authenticates via Google OAuth using the Cloud Code IDE client (the same flow used by Gemini in VS Code). After sign-in, Esprit obtains a Cloud Code project ID and routes all LLM requests through `daily-cloudcode-pa.sandbox.googleapis.com`. Requests are formatted as Google GenAI API calls (not OpenAI format), so a full format-conversion layer handles message translation, tool schema sanitization, SSE response parsing, and thinking-block extraction.

### Architecture

```
User prompt
  ↓
esprit/llm/llm.py  (detects Antigravity model)
  ↓
esprit/providers/antigravity_format.py  (OpenAI → Google GenAI conversion)
  ↓
Cloud Code API  (daily-cloudcode-pa.sandbox.googleapis.com)
  ↓
SSE response stream
  ↓
antigravity_format.parse_sse_chunk()  (Google GenAI → OpenAI conversion)
  ↓
Agent loop continues
```

## Available Models

| Model | Type | Notes |
|-------|------|-------|
| `claude-opus-4-6-thinking` | Claude | Extended thinking enabled |
| `claude-opus-4-5-thinking` | Claude | Extended thinking enabled |
| `claude-sonnet-4-5-thinking` | Claude | Extended thinking enabled |
| `claude-sonnet-4-5` | Claude | Standard inference |
| `gemini-2.5-flash` | Gemini | Fast, cost-effective |
| `gemini-2.5-flash-lite` | Gemini | Lightest Gemini model |
| `gemini-2.5-flash-thinking` | Gemini | Thinking enabled |
| `gemini-2.5-pro` | Gemini | High capability |
| `gemini-3-flash` | Gemini | Latest fast model |
| `gemini-3-pro-high` | Gemini | Latest pro (high quality) |
| `gemini-3-pro-image` | Gemini | Image generation capable |
| `gemini-3-pro-low` | Gemini | Latest pro (faster) |

All models are free to use. Claude models may be rate-limited (429) during peak usage; Esprit's account rotation handles this automatically.

## Setup

### Quick setup

```bash
esprit provider login
# Select "Antigravity (Free Claude/Gemini)"
# Complete Google sign-in in browser
# Done — models are now available
```

### Set default model

```bash
esprit config model
# Select from the Antigravity section
```

Or set directly:

```bash
export ESPRIT_LLM="antigravity/gemini-3-flash"
```

### Multi-account rotation

You can add multiple Google accounts. When one account gets rate-limited (429), Esprit automatically rotates to the next available account with escalating backoff:

```bash
# Add first account
esprit provider login   # → select Antigravity, sign in with account1@gmail.com

# Add another
esprit provider login   # → select Antigravity, sign in with account2@gmail.com

# Check status
esprit provider status
```

Rotation uses a "sticky" strategy by default: the current account is used until rate-limited, then the next available account is selected. Backoff tiers are 1min → 5min → 30min → 2h for consecutive 429s on the same account.

## Technical Details

### OAuth Flow

1. PKCE challenge generated (S256)
2. Browser opens Google consent screen
3. Local callback server on `127.0.0.1:<random-port>` receives the authorization code
4. Code exchanged for access + refresh tokens
5. `loadCodeAssist` called to discover the Cloud Code project ID
6. Credentials stored in `~/.esprit/accounts.json`

### Required Scopes

- `cloud-platform` — Cloud Code API access
- `userinfo.email` / `userinfo.profile` — Account identification
- `cclog` — Cloud Code logging
- `experimentsandconfigs` — Feature flag access

### Request Format

Requests use the Google GenAI streaming endpoint:

```
POST {endpoint}/v1internal:streamGenerateContent?alt=sse
```

The request body wraps the GenAI request in a Cloud Code envelope with `project`, `model`, `requestType: "agent"`, and `userAgent: "antigravity"`.

### Thinking Models

- **Claude thinking**: Uses `include_thoughts` + `thinking_budget` (snake_case). `maxOutputTokens` is auto-bumped to `thinking_budget + 16384` to satisfy Claude's constraint that `maxOutputTokens > thinking_budget`.
- **Gemini thinking**: Uses `includeThoughts` + `thinkingBudget` (camelCase).

### Tool Calling

OpenAI-style tool definitions (`type: "function"`) are converted to Google `functionDeclarations`. JSON Schema keywords unsupported by Google GenAI (like `additionalProperties`, `$ref`, `anyOf`) are stripped or simplified. Claude models use `VALIDATED` function calling mode.

## Credential Storage

Antigravity credentials are stored in `~/.esprit/accounts.json` (not `providers.json`), because Antigravity supports multi-account pools. The file is `chmod 600` on Unix systems.

```json
{
  "version": 1,
  "pools": {
    "antigravity": {
      "accounts": [
        {
          "email": "user@gmail.com",
          "credentials": {
            "type": "oauth",
            "access": "ya29.xxx",
            "refresh": "1//xxx",
            "expires": 1700000000000,
            "extra": {
              "email": "user@gmail.com",
              "project_id": "cloudaicompanion-xxx",
              "managed_project_id": null
            }
          },
          "enabled": true
        }
      ],
      "active_index": 0,
      "strategy": "sticky"
    }
  }
}
```

## Troubleshooting

### 403 PERMISSION_DENIED

The Cloud Code project may not have the required APIs enabled, or the OAuth scopes may be wrong. Try logging out and logging back in:

```bash
esprit provider logout   # select Antigravity
esprit provider login    # re-authenticate
```

### 429 Rate Limited

Normal behavior. Esprit automatically rotates to the next account if you have multiple. Add more accounts to reduce rate limiting:

```bash
esprit provider login   # add another Google account
```

### 400 INVALID_ARGUMENT on Claude thinking models

This happens if `maxOutputTokens` is less than `thinking_budget`. Esprit auto-fixes this, but if you see it with custom configurations, ensure `maxOutputTokens > thinking_budget`.
