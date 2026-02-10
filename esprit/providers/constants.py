"""Shared constants for provider modules."""

# Providers that support the multi-account credential pool
MULTI_ACCOUNT_PROVIDERS: frozenset[str] = frozenset({"openai", "antigravity"})
