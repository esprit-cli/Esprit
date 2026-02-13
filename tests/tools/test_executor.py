"""Tests for tool executor helpers."""

from esprit.tools.executor import _extract_plain_result


class TestExtractPlainResult:
    def test_uses_last_closing_result_tag(self) -> None:
        observation = (
            "<tool_result>\n"
            "<tool_name>terminal_execute</tool_name>\n"
            "<result>A literal </result> marker from tool output</result>\n"
            "</tool_result>"
        )

        parsed = _extract_plain_result(observation, "terminal_execute")
        assert parsed == "A literal </result> marker from tool output"

    def test_returns_original_when_result_tags_missing(self) -> None:
        observation = "plain text without XML wrapper"
        parsed = _extract_plain_result(observation, "terminal_execute")
        assert parsed == observation
