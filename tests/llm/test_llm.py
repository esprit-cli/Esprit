"""Tests for LLM module utilities."""

import pytest

from esprit.llm.llm import _mask_email


class TestMaskEmail:
    """Tests for PII masking of email addresses."""

    def test_standard_email(self) -> None:
        assert _mask_email("alice@example.com") == "ali***@exa***"

    def test_short_local_part(self) -> None:
        result = _mask_email("ab@x.com")
        assert result == "ab***@x.c***"

    def test_no_at_sign(self) -> None:
        result = _mask_email("notanemail")
        assert result == "not***"

    def test_empty_string(self) -> None:
        result = _mask_email("")
        assert result == "***"

    def test_single_char_local(self) -> None:
        result = _mask_email("a@b.co")
        assert result == "a***@b.c***"
