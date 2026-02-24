"""
LLM proxy service.

Routes LLM requests through our API so users don't need their own API keys.
Handles rate limiting and usage tracking.
"""

import structlog
from litellm import acompletion

from app.core.config import get_settings
from app.models.schemas import LLMGenerateRequest, LLMGenerateResponse

logger = structlog.get_logger()
settings = get_settings()


class LLMService:
    """Service for proxying LLM requests."""

    def __init__(self) -> None:
        self.model = settings.llm_model
        self.api_key = settings.llm_api_key

    async def generate(
        self,
        request: LLMGenerateRequest,
        user_id: str,
    ) -> LLMGenerateResponse:
        """
        Generate LLM response using our API key.
        """
        model = request.model or self.model

        try:
            response = await acompletion(
                model=model,
                messages=request.messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                api_key=self.api_key,
            )

            content = response.choices[0].message.content or ""
            tokens_used = response.usage.total_tokens if response.usage else 0

            logger.info(
                "LLM generation completed",
                user_id=user_id,
                model=model,
                tokens_used=tokens_used,
                scan_id=request.scan_id,
            )

            return LLMGenerateResponse(
                content=content,
                model=model,
                tokens_used=tokens_used,
                finish_reason=response.choices[0].finish_reason,
            )

        except Exception as e:
            logger.error("LLM generation failed", error=str(e), user_id=user_id)
            raise


# Singleton instance
llm_service = LLMService()
