from __future__ import annotations

import logging

import esprit.llm  # noqa: F401


def test_http_stack_debug_loggers_are_clamped() -> None:
    for logger_name in ("httpx", "httpcore", "httpcore.http2", "hpack", "hpack.hpack"):
        logger = logging.getLogger(logger_name)
        assert logger.getEffectiveLevel() >= logging.WARNING
        assert logger.propagate is False
