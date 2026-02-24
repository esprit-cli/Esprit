"""Tests for cloud LLM proxy routing and validation."""

from __future__ import annotations

from types import SimpleNamespace

import litellm.exceptions as litellm_exceptions
import pytest

import app.services.llm_service as llm_service_module
from app.models.schemas import LLMGenerateRequest
from app.services.llm_service import LLMService, LLMServiceError


@pytest.mark.asyncio
async def test_generate_routes_default_alias_to_bedrock(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_acompletion(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")],
            usage=SimpleNamespace(total_tokens=10),
        )

    monkeypatch.setattr(llm_service_module, "acompletion", fake_acompletion)

    service = LLMService()
    request = LLMGenerateRequest(messages=[{"role": "user", "content": "hello"}], model="default")
    response = await service.generate(request, user_id="u1", provider_hint="bedrock")

    assert response.model == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert captured["model"] == "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert response.tokens_used == 10


@pytest.mark.asyncio
async def test_generate_rejects_non_bedrock_provider() -> None:
    service = LLMService()
    request = LLMGenerateRequest(messages=[{"role": "user", "content": "hello"}], model="default")

    with pytest.raises(LLMServiceError) as excinfo:
        await service.generate(request, user_id="u1", provider_hint="openai")

    assert excinfo.value.status_code == 422


@pytest.mark.asyncio
async def test_generate_maps_rate_limit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_acompletion(**kwargs: object) -> SimpleNamespace:
        raise litellm_exceptions.RateLimitError(
            message="too many requests",
            llm_provider="bedrock",
            model="bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        )

    monkeypatch.setattr(llm_service_module, "acompletion", fake_acompletion)

    service = LLMService()
    request = LLMGenerateRequest(messages=[{"role": "user", "content": "hello"}], model="default")

    with pytest.raises(LLMServiceError) as excinfo:
        await service.generate(request, user_id="u1", provider_hint="bedrock")

    assert excinfo.value.status_code == 429
