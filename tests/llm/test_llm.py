"""Tests for LLM module utilities."""

from types import SimpleNamespace

import pytest

from esprit.llm.config import LLMConfig
from esprit.llm.llm import LLM, _mask_email


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


class TestExtractNativeToolCalls:
    def test_handles_malformed_entries_without_crashing(self) -> None:
        llm = LLM.__new__(LLM)

        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        tool_calls=[
                            SimpleNamespace(
                                function=SimpleNamespace(
                                    name="terminal_execute",
                                    arguments='{"command": "ls"}',
                                ),
                                id="call_1",
                            ),
                            SimpleNamespace(
                                function=SimpleNamespace(
                                    name="bad_json",
                                    arguments="{not-json",
                                ),
                                id="call_2",
                            ),
                            SimpleNamespace(id="call_3"),
                        ]
                    )
                )
            ]
        )

        parsed = llm._extract_native_tool_calls(response)
        assert parsed is not None
        assert parsed == [
            {
                "toolName": "terminal_execute",
                "args": {"command": "ls"},
                "tool_call_id": "call_1",
            },
            {
                "toolName": "bad_json",
                "args": {},
                "tool_call_id": "call_2",
            },
        ]

    def test_accepts_dict_style_tool_calls(self) -> None:
        llm = LLM.__new__(LLM)

        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        tool_calls=[
                            {
                                "id": "call_dict",
                                "function": {
                                    "name": "list_files",
                                    "arguments": {"path": "/tmp"},
                                },
                            }
                        ]
                    )
                )
            ]
        )

        parsed = llm._extract_native_tool_calls(response)
        assert parsed == [
            {
                "toolName": "list_files",
                "args": {"path": "/tmp"},
                "tool_call_id": "call_dict",
            }
        ]


class TestSupportsNativeToolCalling:
    def test_returns_true_for_antigravity_models(self, monkeypatch: pytest.MonkeyPatch) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="antigravity/claude-sonnet-4-5")
        monkeypatch.setattr(LLM, "_is_antigravity", lambda self: True)

        assert llm.supports_native_tool_calling() is True

    def test_uses_litellm_function_support(self, monkeypatch: pytest.MonkeyPatch) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="ollama/llama3")
        monkeypatch.setattr(LLM, "_is_antigravity", lambda self: False)
        monkeypatch.setattr(
            "esprit.llm.llm.litellm.supports_function_calling",
            lambda model: False,
            raising=False,
        )

        assert llm.supports_native_tool_calling() is False


class TestSystemPromptToolGating:
    def test_omits_xml_tool_prompt_when_native_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(LLM, "supports_native_tool_calling", lambda self: True)
        llm = LLM(LLMConfig(model_name="anthropic/claude-3-5-sonnet-20241022"), "EspritAgent")

        assert "<agents_graph_tools>" not in llm.system_prompt

    def test_keeps_xml_tool_prompt_when_native_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(LLM, "supports_native_tool_calling", lambda self: False)
        llm = LLM(LLMConfig(model_name="ollama/llama3"), "EspritAgent")

        assert "<agents_graph_tools>" in llm.system_prompt


class TestPromptCacheControl:
    def test_marks_system_identity_and_first_user_for_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="anthropic/claude-3-5-sonnet-20241022")

        monkeypatch.setattr("esprit.llm.llm.supports_prompt_caching", lambda model: True)

        messages = [
            {"role": "system", "content": "system prompt"},
            {
                "role": "user",
                "content": (
                    "\n\n<agent_identity>\n"
                    "<agent_name>EspritAgent</agent_name>\n"
                    "<agent_id>agent_123</agent_id>\n"
                    "</agent_identity>\n\n"
                ),
            },
            {"role": "user", "content": "scan https://example.com"},
            {"role": "assistant", "content": "acknowledged"},
        ]

        updated = llm._add_cache_control(messages)

        for idx in (0, 1, 2, 3):
            content = updated[idx]["content"]
            assert isinstance(content, list)
            assert content[0]["type"] == "text"
            assert content[0]["cache_control"] == {"type": "ephemeral"}
        assert updated[3]["content"][0]["text"] == "acknowledged"

    def test_skips_changes_when_prompt_caching_not_supported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="anthropic/claude-3-5-sonnet-20241022")

        monkeypatch.setattr("esprit.llm.llm.supports_prompt_caching", lambda model: False)

        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "task"},
        ]

        updated = llm._add_cache_control(messages)
        assert updated == messages

    def test_preserves_existing_cache_control(self, monkeypatch: pytest.MonkeyPatch) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="anthropic/claude-3-5-sonnet-20241022")

        monkeypatch.setattr("esprit.llm.llm.supports_prompt_caching", lambda model: True)

        messages = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "system prompt",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
            {"role": "user", "content": "task"},
        ]

        updated = llm._add_cache_control(messages)
        assert updated[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
