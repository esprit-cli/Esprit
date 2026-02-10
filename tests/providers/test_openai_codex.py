"""Tests for OpenAI Codex provider utilities."""

import base64
import json

import pytest

from esprit.providers.openai_codex import (
    _decode_jwt_payload,
    _extract_account_id,
    _extract_email,
    _generate_pkce,
    _generate_state,
)


def _make_jwt(payload: dict, header: dict | None = None) -> str:
    """Build a minimal unsigned JWT (header.payload.signature)."""
    h = header or {"alg": "RS256", "typ": "JWT"}

    def _b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{_b64(h)}.{_b64(payload)}.fakesig"


class TestDecodeJWTPayload:
    def test_valid_jwt(self) -> None:
        payload = {"sub": "user-123", "email": "alice@example.com"}
        token = _make_jwt(payload)
        result = _decode_jwt_payload(token)
        assert result is not None
        assert result["sub"] == "user-123"
        assert result["email"] == "alice@example.com"

    def test_invalid_jwt_too_few_parts(self) -> None:
        assert _decode_jwt_payload("onlytwoparts.here") is None

    def test_invalid_jwt_bad_base64(self) -> None:
        assert _decode_jwt_payload("a.!!!invalid!!!.c") is None

    def test_jwt_with_padding_needed(self) -> None:
        payload = {"name": "test"}
        token = _make_jwt(payload)
        # Ensure it works (padding is added internally)
        result = _decode_jwt_payload(token)
        assert result["name"] == "test"


class TestExtractAccountId:
    def test_chatgpt_account_id_in_id_token(self) -> None:
        tokens = {
            "id_token": _make_jwt({"chatgpt_account_id": "acct_abc"}),
        }
        assert _extract_account_id(tokens) == "acct_abc"

    def test_nested_auth_claim(self) -> None:
        tokens = {
            "id_token": _make_jwt({
                "https://api.openai.com/auth": {"chatgpt_account_id": "acct_xyz"}
            }),
        }
        assert _extract_account_id(tokens) == "acct_xyz"

    def test_organizations_fallback(self) -> None:
        tokens = {
            "id_token": _make_jwt({
                "organizations": [{"id": "org-123", "name": "Test Org"}]
            }),
        }
        assert _extract_account_id(tokens) == "org-123"

    def test_no_account_id(self) -> None:
        tokens = {
            "id_token": _make_jwt({"sub": "user-only"}),
        }
        assert _extract_account_id(tokens) is None

    def test_missing_tokens(self) -> None:
        assert _extract_account_id({}) is None


class TestExtractEmail:
    def test_email_in_id_token(self) -> None:
        tokens = {
            "id_token": _make_jwt({"email": "alice@openai.com"}),
        }
        assert _extract_email(tokens) == "alice@openai.com"

    def test_email_in_access_token(self) -> None:
        tokens = {
            "access_token": _make_jwt({"email": "bob@openai.com"}),
        }
        assert _extract_email(tokens) == "bob@openai.com"

    def test_prefers_id_token_over_access_token(self) -> None:
        tokens = {
            "id_token": _make_jwt({"email": "from_id@test.com"}),
            "access_token": _make_jwt({"email": "from_access@test.com"}),
        }
        assert _extract_email(tokens) == "from_id@test.com"

    def test_no_email(self) -> None:
        tokens = {
            "id_token": _make_jwt({"sub": "no-email"}),
        }
        assert _extract_email(tokens) is None

    def test_empty_tokens(self) -> None:
        assert _extract_email({}) is None


class TestPKCE:
    def test_verifier_and_challenge_differ(self) -> None:
        verifier, challenge = _generate_pkce()
        assert verifier != challenge

    def test_verifier_length(self) -> None:
        verifier, _ = _generate_pkce()
        assert len(verifier) == 43

    def test_challenge_is_base64url(self) -> None:
        _, challenge = _generate_pkce()
        # Should not contain padding
        assert "=" not in challenge
        # Should be valid base64url
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        assert all(c in allowed for c in challenge)

    def test_unique_per_call(self) -> None:
        v1, c1 = _generate_pkce()
        v2, c2 = _generate_pkce()
        assert v1 != v2
        assert c1 != c2


class TestGenerateState:
    def test_state_is_nonempty(self) -> None:
        state = _generate_state()
        assert len(state) > 0

    def test_state_unique(self) -> None:
        assert _generate_state() != _generate_state()
