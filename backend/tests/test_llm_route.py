"""Tests for /api/v1/llm/generate endpoint behavior."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.core.auth as auth_module
from app.api import routes
from app.main import app
from app.services.llm_service import LLMServiceError


def test_llm_generate_returns_mapped_error(monkeypatch) -> None:
    async def fake_user() -> auth_module.TokenPayload:
        return auth_module.TokenPayload(sub="user-1", email="demo@esprit.dev", role="authenticated", exp=0)

    async def fake_check_quota(*args, **kwargs):
        return SimpleNamespace(tokens_remaining=999, has_quota=True)

    async def fake_add_tokens(*args, **kwargs):
        return None

    async def fake_generate(*args, **kwargs):
        raise LLMServiceError(message="invalid model", status_code=422)

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    monkeypatch.setattr(routes.usage_service, "check_quota", fake_check_quota)
    monkeypatch.setattr(routes.usage_service, "add_tokens_used", fake_add_tokens)
    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/llm/generate",
            headers={"Authorization": "Bearer ignored.for.override"},
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "invalid model"
    app.dependency_overrides.clear()


def test_llm_chat_completions_compat_returns_openai_shape(monkeypatch) -> None:
    async def fake_user() -> auth_module.TokenPayload:
        return auth_module.TokenPayload(sub="user-1", email="demo@esprit.dev", role="authenticated", exp=0)

    async def fake_check_quota(*args, **kwargs):
        return SimpleNamespace(tokens_remaining=999, has_quota=True)

    async def fake_add_tokens(*args, **kwargs):
        return None

    async def fake_generate(*args, **kwargs):
        return SimpleNamespace(
            content="ok",
            model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            tokens_used=7,
            finish_reason="stop",
            tool_calls=None,
            thinking_blocks=None,
        )

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    monkeypatch.setattr(routes.usage_service, "check_quota", fake_check_quota)
    monkeypatch.setattr(routes.usage_service, "add_tokens_used", fake_add_tokens)
    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/llm/generate/chat/completions",
            headers={"Authorization": "Bearer ignored.for.override"},
            json={
                "model": "openai/us.anthropic.claude-haiku-4-5-20251001-v1:0",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"]["content"] == "ok"
    assert payload["usage"]["total_tokens"] == 7
    app.dependency_overrides.clear()
