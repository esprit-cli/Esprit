"""Tests for XML tool invocation parsing helpers."""

from esprit.llm.utils import parse_tool_invocations


class TestParseToolInvocations:
    def test_terminal_execute_falls_back_to_raw_body_command(self) -> None:
        invocations = parse_tool_invocations("<function=terminal_execute>\nls -la\n</function>")

        assert invocations == [{"toolName": "terminal_execute", "args": {"command": "ls -la"}}]

    def test_terminal_execute_prefers_parameter_tags_when_present(self) -> None:
        invocations = parse_tool_invocations(
            "<function=terminal_execute>\n<parameter=command>pwd</parameter>\n</function>"
        )

        assert invocations == [{"toolName": "terminal_execute", "args": {"command": "pwd"}}]
