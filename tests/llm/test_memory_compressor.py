from types import SimpleNamespace

from esprit.llm.completion_args import CompletionArgsError
from esprit.llm.memory_compressor import summarize_messages


class TestSummarizeMessagesAuthPath:
    def test_uses_provider_aware_completion_args(self, monkeypatch) -> None:
        messages = [{"role": "user", "content": "hello"}]

        captured: dict[str, object] = {}

        def _fake_build_completion_args(**kwargs):
            assert kwargs["model_name"] == "anthropic/claude-sonnet-4-5-20250514"
            return {
                "model": kwargs["model_name"],
                "messages": kwargs["messages"],
                "timeout": kwargs["timeout"],
                "api_key": "oauth-token",
            }

        def _fake_completion(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="summary output"))]
            )

        monkeypatch.setattr(
            "esprit.llm.memory_compressor.build_completion_args",
            _fake_build_completion_args,
        )
        monkeypatch.setattr("esprit.llm.memory_compressor.litellm.completion", _fake_completion)

        result = summarize_messages(messages, "anthropic/claude-sonnet-4-5-20250514")

        assert captured["api_key"] == "oauth-token"
        assert result["role"] == "assistant"
        assert "summary output" in result["content"]

    def test_returns_fallback_message_when_completion_args_fail(self, monkeypatch) -> None:
        messages = [{"role": "user", "content": "hello"}]

        def _raise_completion_args_error(**_kwargs):
            raise CompletionArgsError("missing credentials", status_code=401)

        monkeypatch.setattr(
            "esprit.llm.memory_compressor.build_completion_args",
            _raise_completion_args_error,
        )

        result = summarize_messages(messages, "anthropic/claude-sonnet-4-5-20250514")

        assert result == messages[0]
