"""Tests for LLM module utilities."""

import asyncio
from types import SimpleNamespace

import pytest

from esprit.llm.config import LLMConfig
from esprit.llm.llm import LLM, LLMRequestFailedError, _mask_email
from esprit.llm.utils import normalize_messages_for_provider


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


class TestRaiseError:
    def test_includes_status_code_from_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        llm = LLM.__new__(LLM)
        monkeypatch.setattr("esprit.telemetry.posthog.error", lambda *_args, **_kwargs: None)

        response = SimpleNamespace(status_code=503)
        error = RuntimeError("service unavailable")
        error.response = response  # type: ignore[attr-defined]

        with pytest.raises(LLMRequestFailedError) as exc:
            llm._raise_error(error)

        assert exc.value.status_code == 503


class TestStreamIdleTimeout:
    @pytest.mark.asyncio
    async def test_iter_with_idle_timeout_raises_on_stalled_stream(self) -> None:
        llm = LLM.__new__(LLM)

        async def stalled() -> SimpleNamespace:
            while True:
                await asyncio.sleep(1)
                yield SimpleNamespace()

        with pytest.raises(TimeoutError):
            async for _ in llm._iter_with_idle_timeout(stalled(), timeout_seconds=0.01):
                pass

    @pytest.mark.asyncio
    async def test_iter_with_idle_timeout_passes_through_chunks(self) -> None:
        llm = LLM.__new__(LLM)

        async def ready() -> int:
            yield 1
            yield 2

        chunks: list[int] = []
        async for chunk in llm._iter_with_idle_timeout(ready(), timeout_seconds=0.5):
            chunks.append(chunk)

        assert chunks == [1, 2]

class TestMessageNormalization:
    def test_tool_without_tool_call_id_is_downgraded(self) -> None:
        messages = [
            {"role": "assistant", "content": "working"},
            {"role": "tool", "content": "result without id"},
        ]

        normalized = normalize_messages_for_provider(messages)

        assert normalized[1]["role"] == "user"
        assert "tool metadata was incomplete" in normalized[1]["content"]

    def test_orphan_tool_call_id_is_downgraded(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": "I will inspect files",
            },
            {"role": "tool", "tool_call_id": "call_missing", "content": "result"},
        ]

        normalized = normalize_messages_for_provider(messages)

        assert normalized[1]["role"] == "user"
        assert "tool metadata was incomplete" in normalized[1]["content"]

    def test_valid_native_tool_sequence_is_preserved(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "list_files", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ]

        normalized = normalize_messages_for_provider(messages)

        assert normalized == messages

    def test_tool_sequence_with_interleaved_non_tool_message_is_downgraded(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "list_files", "arguments": "{}"},
                    }
                ],
            },
            {"role": "user", "content": "status update from another agent"},
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ]

        normalized = normalize_messages_for_provider(messages)

        assert "tool_calls" not in normalized[0]
        assert normalized[1]["role"] == "user"
        assert normalized[2]["role"] == "user"
        assert "tool metadata was incomplete" in normalized[2]["content"]

    def test_partial_tool_results_for_multi_call_assistant_are_downgraded(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "list_files", "arguments": "{}"},
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ]

        normalized = normalize_messages_for_provider(messages)

        assert "tool_calls" not in normalized[0]
        assert normalized[1]["role"] == "user"
        assert "tool metadata was incomplete" in normalized[1]["content"]

    def test_valid_multi_tool_sequence_is_preserved(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "list_files", "arguments": "{}"},
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "result 1"},
            {"role": "tool", "tool_call_id": "call_2", "content": "result 2"},
            {"role": "assistant", "content": "done"},
        ]

        normalized = normalize_messages_for_provider(messages)

        assert normalized == messages

    def test_prepare_messages_normalizes_invalid_tool_history(self) -> None:
        llm = LLM(LLMConfig(model_name="ollama/llama3"), "EspritAgent")
        conversation_history = [
            {"role": "assistant", "content": "processing"},
            {"role": "tool", "content": "orphan result without id"},
        ]

        prepared = llm._prepare_messages(conversation_history)

        assert any(
            message.get("role") == "user"
            and "tool metadata was incomplete" in str(message.get("content"))
            for message in prepared
        )
        assert conversation_history[1]["role"] == "user"


class TestRaiseErrorMapping:
    def test_maps_masked_openai_codex_scope_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="openai/gpt-5.1-codex-mini")
        monkeypatch.setattr("esprit.telemetry.posthog.error", lambda *_args, **_kwargs: None)

        class _FakeOpenAIError(Exception):
            llm_provider = "openai"

        err = _FakeOpenAIError("OpenAIException - argument of type 'NoneType' is not iterable")

        with pytest.raises(LLMRequestFailedError) as exc:
            llm._raise_error(err)

        assert "LLM request failed" in str(exc.value)
        assert "api.responses.write" in str(exc.value.details)

    def test_keeps_generic_error_details_for_non_openai_case(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="anthropic/claude-haiku-4-5-20251001")
        monkeypatch.setattr("esprit.telemetry.posthog.error", lambda *_args, **_kwargs: None)

        err = RuntimeError("plain failure")

        with pytest.raises(LLMRequestFailedError) as exc:
            llm._raise_error(err)

        assert exc.value.details == "plain failure"


class TestRateLimitPacing:
    def test_compute_retry_delay_respects_retry_after(self, monkeypatch: pytest.MonkeyPatch) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="anthropic/claude-haiku-4-5-20251001")

        config_values = {
            "esprit_llm_retry_max_wait_s": "120",
            "esprit_llm_retry_jitter_ratio": "0",
        }
        monkeypatch.setattr("esprit.llm.llm.Config.get", lambda name: config_values.get(name))
        monkeypatch.setattr("esprit.llm.llm.random.uniform", lambda _a, _b: 0.0)

        err = RuntimeError("rate limited")
        err.response = SimpleNamespace(headers={"retry-after": "45"})  # type: ignore[attr-defined]

        delay = llm._compute_retry_delay(err, attempt=0)
        assert delay == 45.0

    def test_429_registers_global_cooldown_without_provider_rotation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="anthropic/claude-haiku-4-5-20251001")

        class _PacerStub:
            def __init__(self) -> None:
                self.retry_after_values: list[float | None] = []

            def register_rate_limit(self, retry_after_s: float | None = None) -> None:
                self.retry_after_values.append(retry_after_s)

        pacer = _PacerStub()
        monkeypatch.setattr("esprit.llm.llm.get_request_pacer", lambda: pacer)
        monkeypatch.setattr("esprit.llm.llm.PROVIDERS_AVAILABLE", False)

        err = RuntimeError("429")
        err.status_code = 429  # type: ignore[attr-defined]
        err.response = SimpleNamespace(headers={"retry-after": "11"})  # type: ignore[attr-defined]

        rotated = llm._try_rotate_on_rate_limit(err)
        assert rotated is False
        assert pacer.retry_after_values == [11.0]
