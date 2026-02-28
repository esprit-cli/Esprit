import logging
import warnings

import litellm

from .config import LLMConfig
from .llm import LLM, LLMRequestFailedError


__all__ = [
    "LLM",
    "LLMConfig",
    "LLMRequestFailedError",
]

litellm._logging._disable_debugging()
logging.getLogger("asyncio").setLevel(logging.CRITICAL)
logging.getLogger("asyncio").propagate = False

# Prevent lower-level HTTP debug logs from leaking auth headers/tokens.
for noisy_logger_name in ("httpx", "httpcore", "httpcore.http2", "hpack", "hpack.hpack"):
    noisy_logger = logging.getLogger(noisy_logger_name)
    noisy_logger.setLevel(logging.WARNING)
    noisy_logger.propagate = False

warnings.filterwarnings("ignore", category=RuntimeWarning, module="asyncio")
