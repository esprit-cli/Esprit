from types import SimpleNamespace

from esprit.llm import memory_compressor as mc


def test_summarize_messages_openai_oauth_sets_store_false(monkeypatch) -> None:
    captured: dict = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="summary text"),
                )
            ]
        )

    monkeypatch.setattr(mc.litellm, "completion", fake_completion)
    monkeypatch.setattr(mc, "PROVIDERS_AVAILABLE", True, raising=False)
    monkeypatch.setattr(mc, "resolve_api_base", lambda _model: None)
    monkeypatch.setattr(mc, "get_provider_api_key", lambda _model: "oauth-token")
    monkeypatch.setattr(
        mc,
        "get_provider_api_base",
        lambda _model: "https://chatgpt.com/backend-api/codex",
    )
    monkeypatch.setattr(mc, "get_provider_headers", lambda _model: {})
    monkeypatch.setattr(mc, "get_auth_client", lambda: SimpleNamespace(detect_provider=lambda _model: "openai"))
    monkeypatch.setattr(mc, "should_use_oauth", lambda _model: True)

    result = mc.summarize_messages(
        [{"role": "user", "content": "hello"}],
        "openai/gpt-5.2",
    )

    assert result["role"] == "assistant"
    assert captured["store"] is False
    assert captured["extra_body"]["store"] is False


def test_resolve_model_for_counting_maps_esprit_alias() -> None:
    assert mc._resolve_model_for_counting("esprit/default") == "anthropic/claude-3-5-haiku-latest"


def test_resolve_model_for_counting_maps_bedrock_alias() -> None:
    assert (
        mc._resolve_model_for_counting("bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0")
        == "anthropic/claude-3-5-haiku-latest"
    )


def test_summarize_messages_esprit_routes_via_proxy(monkeypatch) -> None:
    captured: dict = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="summary text"))]
        )

    monkeypatch.setattr(mc.litellm, "completion", fake_completion)
    monkeypatch.setattr(mc, "ESPRIT_PROVIDER_AVAILABLE", True, raising=False)
    monkeypatch.setattr(mc, "PROVIDERS_AVAILABLE", False, raising=False)
    monkeypatch.setattr(mc, "LLM_PROXY_URL", "https://esprit.dev/api/v1/llm/generate")
    monkeypatch.setattr(
        mc,
        "_load_esprit_credentials",
        lambda: SimpleNamespace(access_token="esprit-token"),
    )
    monkeypatch.setattr(
        mc,
        "resolve_bedrock_model",
        lambda _alias: "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )

    result = mc.summarize_messages(
        [{"role": "user", "content": "hello"}],
        "esprit/default",
    )

    assert result["role"] == "assistant"
    assert captured["model"] == "openai/us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert captured["api_base"] == "https://esprit.dev/api/v1/llm/generate"
    assert captured["api_key"] == "esprit-token"
    assert captured["extra_headers"]["X-Esprit-Provider"] == "bedrock"
    assert (
        captured["extra_headers"]["X-Esprit-Model"]
        == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    )


def test_summarize_messages_esprit_without_credentials_skips(monkeypatch) -> None:
    def fail_completion(**_kwargs):
        raise AssertionError("litellm.completion should not be called")

    monkeypatch.setattr(mc.litellm, "completion", fail_completion)
    monkeypatch.setattr(mc, "ESPRIT_PROVIDER_AVAILABLE", True, raising=False)
    monkeypatch.setattr(mc, "_load_esprit_credentials", lambda: None)

    original = {"role": "user", "content": "hello"}
    result = mc.summarize_messages([original], "esprit/default")

    assert result == original
