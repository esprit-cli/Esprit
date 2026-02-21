from types import SimpleNamespace

from esprit.llm.completion_args import CompletionArgsError
from esprit.llm.dedupe import check_duplicate


class TestCheckDuplicate:
    def test_uses_provider_aware_completion_args(self, monkeypatch) -> None:
        candidate = {"title": "SQLi", "endpoint": "/login"}
        existing = [{"id": "vuln-0001", "title": "SQL injection", "endpoint": "/login"}]
        captured: dict[str, object] = {}

        def _fake_config_get(name: str):
            values = {
                "esprit_llm": "anthropic/claude-sonnet-4-5-20250514",
                "esprit_dedupe_timeout": "123",
            }
            return values.get(name)

        def _fake_build_completion_args(**kwargs):
            assert kwargs["timeout"] == 123
            return {
                "model": kwargs["model_name"],
                "messages": kwargs["messages"],
                "timeout": kwargs["timeout"],
                "api_key": "oauth-token",
            }

        def _fake_completion(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                "<dedupe_result>"
                                "<is_duplicate>true</is_duplicate>"
                                "<duplicate_id>vuln-0001</duplicate_id>"
                                "<confidence>0.97</confidence>"
                                "<reason>Same endpoint and parameter.</reason>"
                                "</dedupe_result>"
                            )
                        )
                    )
                ]
            )

        monkeypatch.setattr("esprit.llm.dedupe.Config.get", _fake_config_get)
        monkeypatch.setattr("esprit.llm.dedupe.build_completion_args", _fake_build_completion_args)
        monkeypatch.setattr("esprit.llm.dedupe.litellm.completion", _fake_completion)

        result = check_duplicate(candidate, existing)

        assert captured["api_key"] == "oauth-token"
        assert captured["max_retries"] == 0
        assert result["is_duplicate"] is True
        assert result["duplicate_id"] == "vuln-0001"

    def test_returns_skip_result_when_completion_args_fail(self, monkeypatch) -> None:
        candidate = {"title": "SQLi"}
        existing = [{"id": "vuln-0001", "title": "SQL injection"}]

        def _fake_config_get(name: str):
            if name == "esprit_llm":
                return "anthropic/claude-sonnet-4-5-20250514"
            return None

        def _raise_completion_args_error(**_kwargs):
            raise CompletionArgsError("missing credentials", status_code=401)

        monkeypatch.setattr("esprit.llm.dedupe.Config.get", _fake_config_get)
        monkeypatch.setattr(
            "esprit.llm.dedupe.build_completion_args",
            _raise_completion_args_error,
        )

        result = check_duplicate(candidate, existing)

        assert result["is_duplicate"] is False
        assert result["confidence"] == 0.0
        assert "Deduplication skipped" in result["reason"]
