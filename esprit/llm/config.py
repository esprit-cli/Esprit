from esprit.config import Config

# Default model for Esprit (using cross-region inference profile)
DEFAULT_MODEL = "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0"


class LLMConfig:
    def __init__(
        self,
        model_name: str | None = None,
        enable_prompt_caching: bool = True,
        skills: list[str] | None = None,
        timeout: int | None = None,
        scan_mode: str = "deep",
    ):
        self.model_name = model_name or Config.get("esprit_llm") or DEFAULT_MODEL

        if not self.model_name:
            raise ValueError("ESPRIT_LLM environment variable must be set and not empty")

        self.enable_prompt_caching = enable_prompt_caching
        self.skills = skills or []

        self.timeout = timeout or int(Config.get("llm_timeout") or "300")

        self.scan_mode = scan_mode if scan_mode in ["quick", "standard", "deep"] else "deep"
