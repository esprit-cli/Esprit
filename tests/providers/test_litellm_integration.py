"""Tests for the LiteLLM integration provider auth client."""

from unittest.mock import MagicMock, patch

import pytest

from esprit.providers.base import OAuthCredentials
from esprit.providers.litellm_integration import (
    ProviderAuthClient,
    get_provider_api_base,
    get_provider_api_key,
    get_provider_headers,
)


@pytest.fixture
def _no_pool():
    """Ensure the account pool reports no accounts so heuristics aren't bypassed."""
    mock_pool = MagicMock()
    mock_pool.has_accounts.return_value = False
    with patch("esprit.providers.litellm_integration.get_account_pool", return_value=mock_pool):
        yield mock_pool


@pytest.fixture
def client(_no_pool) -> ProviderAuthClient:
    return ProviderAuthClient()


class TestDetectProvider:
    """Tests for provider detection from model names."""

    def test_explicit_anthropic_prefix(self, client: ProviderAuthClient) -> None:
        assert client.detect_provider("anthropic/claude-sonnet-4") == "anthropic"

    def test_explicit_openai_prefix(self, client: ProviderAuthClient) -> None:
        assert client.detect_provider("openai/gpt-5") == "openai"

    def test_explicit_google_prefix(self, client: ProviderAuthClient) -> None:
        assert client.detect_provider("google/gemini-2.5-pro") == "google"

    def test_explicit_antigravity_prefix(self, client: ProviderAuthClient) -> None:
        assert client.detect_provider("antigravity/claude-opus-4-6-thinking") == "antigravity"

    def test_explicit_opencode_prefix(self, client: ProviderAuthClient) -> None:
        assert client.detect_provider("opencode/gpt-5.1-codex") == "opencode"

    def test_zen_alias_prefix_maps_to_opencode(self, client: ProviderAuthClient) -> None:
        assert client.detect_provider("zen/gpt-5.1-codex") == "opencode"

    def test_bedrock_returns_none(self, client: ProviderAuthClient) -> None:
        """Bedrock uses AWS credentials, not OAuth — should be skipped."""
        assert client.detect_provider("bedrock/claude-sonnet-4") is None

    def test_claude_heuristic(self, client: ProviderAuthClient) -> None:
        assert client.detect_provider("claude-sonnet-4") == "anthropic"

    def test_gemini_heuristic(self, client: ProviderAuthClient) -> None:
        assert client.detect_provider("gemini-2.5-flash") == "google"

    def test_gpt_with_copilot_credentials(self, client: ProviderAuthClient) -> None:
        """If copilot credentials exist, GPT models should route to copilot."""
        client.token_store = MagicMock()
        client.token_store.has_credentials.return_value = True
        assert client.detect_provider("gpt-5") == "github-copilot"

    def test_codex_alias_stays_openai_with_copilot_credentials(self, client: ProviderAuthClient) -> None:
        """Codex models are OpenAI-specific and should not auto-route to Copilot."""
        client.token_store = MagicMock()
        client.token_store.has_credentials.return_value = True
        assert client.detect_provider("codex-5.3") == "openai"

    def test_gpt_without_copilot_falls_to_openai(self, client: ProviderAuthClient) -> None:
        client.token_store = MagicMock()
        client.token_store.has_credentials.return_value = False
        assert client.detect_provider("gpt-5") == "openai"

    def test_unknown_model(self, client: ProviderAuthClient) -> None:
        assert client.detect_provider("llama-3.1-70b") is None

    def test_case_insensitive(self, client: ProviderAuthClient) -> None:
        assert client.detect_provider("Anthropic/Claude-Sonnet-4") == "anthropic"
        assert client.detect_provider("GOOGLE/gemini-2.5-pro") == "google"


class TestGetCredentials:
    """Tests for credential retrieval."""

    def test_single_account_provider_uses_token_store(self, client: ProviderAuthClient) -> None:
        creds = OAuthCredentials(type="api", access_token="sk-test")
        client.token_store = MagicMock()
        client.token_store.get.return_value = creds
        result = client.get_credentials("anthropic")
        assert result is creds
        client.token_store.get.assert_called_with("anthropic")

    def test_multi_account_provider_uses_pool(self, client: ProviderAuthClient) -> None:
        """For multi-account providers (openai, antigravity), pool is checked first."""
        creds = OAuthCredentials(type="oauth", access_token="tok_pool")
        mock_pool = MagicMock()
        mock_acct = MagicMock()
        mock_acct.credentials = creds
        mock_pool.get_best_account.return_value = mock_acct

        with patch("esprit.providers.litellm_integration.get_account_pool", return_value=mock_pool):
            result = client.get_credentials("openai")
        assert result is creds

    def test_multi_account_falls_back_to_token_store(
        self,
        client: ProviderAuthClient,
    ) -> None:
        mock_pool = MagicMock()
        mock_pool.get_best_account.return_value = None

        creds = OAuthCredentials(type="api", access_token="sk-fallback")
        client.token_store = MagicMock()
        client.token_store.get.return_value = creds

        with patch("esprit.providers.litellm_integration.get_account_pool", return_value=mock_pool):
            result = client.get_credentials("openai")
        assert result is creds


class TestHasOAuthCredentials:
    def test_multi_account_with_pool(self, client: ProviderAuthClient) -> None:
        mock_pool = MagicMock()
        mock_pool.has_accounts.return_value = True
        with patch("esprit.providers.litellm_integration.get_account_pool", return_value=mock_pool):
            assert client.has_oauth_credentials("openai") is True

    def test_single_account_oauth(self, client: ProviderAuthClient) -> None:
        creds = OAuthCredentials(type="oauth", access_token="tok")
        client.token_store = MagicMock()
        client.token_store.get.return_value = creds
        assert client.has_oauth_credentials("anthropic") is True

    def test_single_account_api_key(self, client: ProviderAuthClient) -> None:
        creds = OAuthCredentials(type="api", access_token="sk-xxx")
        client.token_store = MagicMock()
        client.token_store.get.return_value = creds
        assert client.has_oauth_credentials("anthropic") is False

    def test_no_credentials(self, client: ProviderAuthClient) -> None:
        client.token_store = MagicMock()
        client.token_store.get.return_value = None
        assert client.has_oauth_credentials("anthropic") is False


class TestGetProviderApiKey:
    def test_public_opencode_without_credentials_returns_placeholder(self) -> None:
        mock_client = MagicMock()
        mock_client.detect_provider.return_value = "opencode"
        mock_client.get_credentials.return_value = None

        with (
            patch("esprit.providers.litellm_integration.get_auth_client", return_value=mock_client),
            patch("esprit.providers.litellm_integration.is_public_opencode_model", return_value=True),
        ):
            assert get_provider_api_key("opencode/minimax-m2.5-free") == "sk-opencode-public-noauth"

    def test_non_public_opencode_without_credentials_returns_none(self) -> None:
        mock_client = MagicMock()
        mock_client.detect_provider.return_value = "opencode"
        mock_client.get_credentials.return_value = None

        with (
            patch("esprit.providers.litellm_integration.get_auth_client", return_value=mock_client),
            patch("esprit.providers.litellm_integration.is_public_opencode_model", return_value=False),
        ):
            assert get_provider_api_key("opencode/gpt-5.2-codex") is None


class TestGetProviderHeaders:
    def test_public_opencode_without_credentials_clears_authorization(self) -> None:
        mock_client = MagicMock()
        mock_client.detect_provider.return_value = "opencode"
        mock_client.get_credentials.return_value = None

        with (
            patch("esprit.providers.litellm_integration.get_auth_client", return_value=mock_client),
            patch("esprit.providers.litellm_integration.is_public_opencode_model", return_value=True),
        ):
            assert get_provider_headers("opencode/minimax-m2.5-free") == {"Authorization": ""}

    def test_non_public_opencode_without_credentials_no_headers(self) -> None:
        mock_client = MagicMock()
        mock_client.detect_provider.return_value = "opencode"
        mock_client.get_credentials.return_value = None

        with (
            patch("esprit.providers.litellm_integration.get_auth_client", return_value=mock_client),
            patch("esprit.providers.litellm_integration.is_public_opencode_model", return_value=False),
        ):
            assert get_provider_headers("opencode/gpt-5.2-codex") == {}


class TestGetProviderApiBase:
    def test_openai_oauth_routes_to_codex_backend(self) -> None:
        mock_client = MagicMock()
        mock_client.detect_provider.return_value = "openai"
        mock_client.get_credentials.return_value = OAuthCredentials(type="oauth", access_token="tok")

        with patch("esprit.providers.litellm_integration.get_auth_client", return_value=mock_client):
            assert get_provider_api_base("openai/gpt-5.3-codex") == "https://chatgpt.com/backend-api/codex"

    def test_openai_api_key_does_not_override_base(self) -> None:
        mock_client = MagicMock()
        mock_client.detect_provider.return_value = "openai"
        mock_client.get_credentials.return_value = OAuthCredentials(type="api", access_token="sk-123")

        with patch("esprit.providers.litellm_integration.get_auth_client", return_value=mock_client):
            assert get_provider_api_base("openai/gpt-5.3-codex") is None

    def test_non_openai_provider_returns_none(self) -> None:
        mock_client = MagicMock()
        mock_client.detect_provider.return_value = "google"
        mock_client.get_credentials.return_value = OAuthCredentials(type="oauth", access_token="tok")

        with patch("esprit.providers.litellm_integration.get_auth_client", return_value=mock_client):
            assert get_provider_api_base("google/gemini-3-pro") is None

    def test_copilot_oauth_uses_default_copilot_base(self) -> None:
        mock_client = MagicMock()
        mock_client.detect_provider.return_value = "github-copilot"
        mock_client.get_credentials.return_value = OAuthCredentials(
            type="oauth",
            access_token="tok",
            refresh_token="tok",
        )

        with patch("esprit.providers.litellm_integration.get_auth_client", return_value=mock_client):
            assert get_provider_api_base("github-copilot/gpt-5") == "https://api.githubcopilot.com"

    def test_copilot_enterprise_oauth_uses_enterprise_base(self) -> None:
        mock_client = MagicMock()
        mock_client.detect_provider.return_value = "github-copilot"
        mock_client.get_credentials.return_value = OAuthCredentials(
            type="oauth",
            access_token="tok",
            refresh_token="tok",
            enterprise_url="https://github.example.com",
        )

        with patch("esprit.providers.litellm_integration.get_auth_client", return_value=mock_client):
            assert get_provider_api_base("github-copilot/gpt-5") == "https://copilot-api.github.example.com"
