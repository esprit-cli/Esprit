"""
LLM proxy service.

Routes Esprit subscription LLM calls through AWS Bedrock.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import litellm.exceptions as litellm_exceptions
import structlog
from litellm import acompletion

from app.core.config import get_settings
from app.models.schemas import LLMGenerateRequest, LLMGenerateResponse

logger = structlog.get_logger()
settings = get_settings()

BEDROCK_PROVIDER = "bedrock"
BEDROCK_ALIASES: dict[str, str] = {
    "default": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "haiku-4.5": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-haiku-4-5": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-haiku-4-5-20251001": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "kimi-k2.5": "moonshotai.kimi-k2.5",
    "kimi-k2": "moonshotai.kimi-k2.5",
}
ALLOWED_BEDROCK_MODELS = {
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "moonshotai.kimi-k2.5",
}


@dataclass
class LLMServiceError(Exception):
    """User-facing LLM service error."""

    message: str
    status_code: int
    details: str | None = None

    def __str__(self) -> str:
        return self.message


class LLMService:
    """Service for proxying LLM requests."""

    def __init__(self) -> None:
        self.default_model = settings.llm_model

    def _resolve_provider(self, provider_hint: str | None) -> str:
        provider = (provider_hint or settings.llm_provider or BEDROCK_PROVIDER).strip().lower()
        if provider.startswith("esprit/"):
            provider = provider.split("/", 1)[1]
        return provider

    def _resolve_bedrock_model(self, request_model: str | None, model_hint: str | None) -> str:
        raw_model = (model_hint or request_model or self.default_model or "default").strip()
        if not raw_model:
            raw_model = "default"

        # Strip known routing prefixes.
        if "/" in raw_model:
            prefix, remainder = raw_model.split("/", 1)
            normalized_prefix = prefix.strip().lower()
            if normalized_prefix in {"esprit", BEDROCK_PROVIDER}:
                raw_model = remainder.strip()
            else:
                raise LLMServiceError(
                    message=f"Unsupported model provider prefix: {prefix}",
                    status_code=422,
                )

        normalized = raw_model.lower()
        resolved = BEDROCK_ALIASES.get(normalized, raw_model)
        if resolved not in ALLOWED_BEDROCK_MODELS:
            raise LLMServiceError(
                message=(
                    "Unsupported model for Esprit cloud proxy. "
                    "Use one of: default, haiku, kimi-k2.5."
                ),
                status_code=422,
            )
        return resolved

    def _extract_content(self, response: Any) -> str:
        message = response.choices[0].message
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        chunks.append(text)
                    continue
                if isinstance(item, str):
                    chunks.append(item)
            return "".join(chunks)
        return str(content or "")

    def _map_llm_exception(
        self,
        exc: Exception,
        provider: str,
        model: str,
    ) -> LLMServiceError:
        error_details = f"{type(exc).__name__}: {exc}"

        if isinstance(exc, litellm_exceptions.RateLimitError):
            return LLMServiceError(
                message="Upstream model rate limit exceeded. Please retry shortly.",
                status_code=429,
                details=error_details,
            )
        if isinstance(exc, (litellm_exceptions.BadRequestError, litellm_exceptions.UnsupportedParamsError)):
            return LLMServiceError(
                message="Invalid LLM request parameters.",
                status_code=422,
                details=error_details,
            )
        if isinstance(exc, litellm_exceptions.ContextWindowExceededError):
            return LLMServiceError(
                message="Request exceeds model context window.",
                status_code=422,
                details=error_details,
            )
        if isinstance(exc, litellm_exceptions.NotFoundError):
            return LLMServiceError(
                message="Requested model is unavailable.",
                status_code=422,
                details=error_details,
            )
        if isinstance(exc, (litellm_exceptions.AuthenticationError, litellm_exceptions.PermissionDeniedError)):
            return LLMServiceError(
                message="Cloud model credentials are invalid or unauthorized.",
                status_code=502,
                details=error_details,
            )
        if isinstance(exc, (litellm_exceptions.APIConnectionError, litellm_exceptions.ServiceUnavailableError)):
            return LLMServiceError(
                message="Cloud model service is unavailable. Please try again.",
                status_code=503,
                details=error_details,
            )

        logger.error(
            "Unhandled LLM generation failure",
            provider=provider,
            model=model,
            error=error_details,
        )
        return LLMServiceError(
            message="Unexpected cloud LLM error.",
            status_code=500,
            details=error_details,
        )

    async def generate(
        self,
        request: LLMGenerateRequest,
        user_id: str,
        provider_hint: str | None = None,
        model_hint: str | None = None,
    ) -> LLMGenerateResponse:
        """Generate LLM response through the Bedrock-backed Esprit proxy."""
        if not request.messages:
            raise LLMServiceError(message="Messages cannot be empty.", status_code=422)

        provider = self._resolve_provider(provider_hint)
        if provider != BEDROCK_PROVIDER:
            raise LLMServiceError(
                message="Unsupported provider for Esprit cloud proxy. Only bedrock is supported.",
                status_code=422,
            )

        bedrock_model = self._resolve_bedrock_model(request.model, model_hint)
        litellm_model = f"{BEDROCK_PROVIDER}/{bedrock_model}"

        completion_args: dict[str, Any] = {
            "model": litellm_model,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "aws_region_name": settings.aws_region,
        }
        if settings.aws_access_key_id and settings.aws_secret_access_key:
            completion_args["aws_access_key_id"] = settings.aws_access_key_id
            completion_args["aws_secret_access_key"] = settings.aws_secret_access_key

        try:
            response = await acompletion(**completion_args)
        except Exception as exc:  # noqa: BLE001
            mapped_error = self._map_llm_exception(exc, provider=provider, model=litellm_model)
            logger.error(
                "LLM generation failed",
                user_id=user_id,
                provider=provider,
                model=litellm_model,
                error=mapped_error.details or mapped_error.message,
            )
            raise mapped_error from exc

        content = self._extract_content(response)
        tokens_used = response.usage.total_tokens if response.usage else 0

        logger.info(
            "LLM generation completed",
            user_id=user_id,
            provider=provider,
            model=litellm_model,
            tokens_used=tokens_used,
            scan_id=request.scan_id,
        )

        return LLMGenerateResponse(
            content=content,
            model=bedrock_model,
            tokens_used=tokens_used,
            finish_reason=response.choices[0].finish_reason,
        )


# Singleton instance
llm_service = LLMService()
