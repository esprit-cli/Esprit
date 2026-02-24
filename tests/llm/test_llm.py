"""Tests for LLM module utilities."""

import asyncio
from types import SimpleNamespace

import pytest

from esprit.llm.config import LLMConfig
from esprit.llm.llm import LLM, LLMRequestFailedError, _mask_email


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

    def test_returns_none_when_choices_missing(self) -> None:
        llm = LLM.__new__(LLM)
        response = SimpleNamespace(model="esprit/default")
        assert llm._extract_native_tool_calls(response) is None


class TestSupportsNativeToolCalling:
    def test_esprit_model_skips_litellm_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="esprit/default")
        monkeypatch.setattr(LLM, "_is_antigravity", lambda self: False)

        def _fail_probe(*_args, **_kwargs):  # noqa: ANN002
            raise AssertionError("supports_function_calling should not be called for esprit/* models")

        monkeypatch.setattr(
            "esprit.llm.llm.litellm.supports_function_calling",
            _fail_probe,
            raising=False,
        )

        assert llm.supports_native_tool_calling() is True

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


class TestSupportsReasoning:
    def test_esprit_model_bypasses_litellm_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="esprit/default")

        def _fail_probe(*_args, **_kwargs):  # noqa: ANN002
            raise AssertionError("supports_reasoning should not be called for esprit/* models")

        monkeypatch.setattr("esprit.llm.llm.supports_reasoning", _fail_probe)

        assert llm._supports_reasoning() is True


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


class TestRaiseErrorDetails:
    def test_includes_status_code_from_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        llm = LLM.__new__(LLM)
        monkeypatch.setattr("esprit.telemetry.posthog.error", lambda *_args, **_kwargs: None)

        response = SimpleNamespace(status_code=503)
        error = RuntimeError("service unavailable")
        error.response = response  # type: ignore[attr-defined]

        with pytest.raises(LLMRequestFailedError) as exc:
            llm._raise_error(error)

        assert exc.value.status_code == 503

    def test_maps_openai_responses_scope_error_to_guidance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = LLM.__new__(LLM)
        monkeypatch.setattr("esprit.telemetry.posthog.error", lambda *_args, **_kwargs: None)

        error = RuntimeError("OpenAIException: Missing scopes: api.responses.write")

        with pytest.raises(LLMRequestFailedError) as exc:
            llm._raise_error(error)

        assert exc.value.details is not None
        assert "api.responses.write" in exc.value.details
        assert "Use a Project API key/token" in exc.value.details


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


class TestOpenCodePublicFallback:
    def test_switches_to_preferred_public_model_on_rate_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="opencode/gpt-5-nano")

        public_models = {
            "gpt-5-nano",
            "minimax-m2.5-free",
            "kimi-k2.5-free",
        }

        monkeypatch.setattr("esprit.llm.llm.PROVIDERS_AVAILABLE", True, raising=False)
        monkeypatch.setattr(
            "esprit.llm.llm.get_available_models",
            lambda: {"opencode": [(model_id, model_id) for model_id in sorted(public_models)]},
            raising=False,
        )
        monkeypatch.setattr(
            "esprit.llm.llm.get_public_opencode_models",
            lambda _catalog=None: set(public_models),
            raising=False,
        )
        monkeypatch.setattr(
            "esprit.llm.llm.is_public_opencode_model",
            lambda model_name, _catalog=None: (
                (model_name or "").split("/", 1)[-1] in public_models
            ),
            raising=False,
        )

        err = RuntimeError("Rate limit exceeded")
        err.status_code = 429  # type: ignore[attr-defined]

        assert llm._try_opencode_model_fallback(err) is True
        assert llm.config.model_name == "opencode/minimax-m2.5-free"

    def test_no_fallback_when_auto_fallback_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="opencode/gpt-5-nano")

        monkeypatch.setenv("ESPRIT_AUTO_FALLBACK", "false")

        err = RuntimeError("Rate limit exceeded")
        err.status_code = 429  # type: ignore[attr-defined]

        assert llm._try_opencode_model_fallback(err) is False
        assert llm.config.model_name == "opencode/gpt-5-nano"

    def test_no_fallback_for_non_public_opencode_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="opencode/gpt-5.2-codex")

        public_models = {"gpt-5-nano", "minimax-m2.5-free"}

        monkeypatch.setattr("esprit.llm.llm.PROVIDERS_AVAILABLE", True, raising=False)
        monkeypatch.setattr(
            "esprit.llm.llm.get_available_models",
            lambda: {"opencode": [(model_id, model_id) for model_id in sorted(public_models)]},
            raising=False,
        )
        monkeypatch.setattr(
            "esprit.llm.llm.get_public_opencode_models",
            lambda _catalog=None: set(public_models),
            raising=False,
        )
        monkeypatch.setattr(
            "esprit.llm.llm.is_public_opencode_model",
            lambda model_name, _catalog=None: (
                (model_name or "").split("/", 1)[-1] in public_models
            ),
            raising=False,
        )

        err = RuntimeError("Rate limit exceeded")
        err.status_code = 429  # type: ignore[attr-defined]

        assert llm._try_opencode_model_fallback(err) is False
        assert llm.config.model_name == "opencode/gpt-5.2-codex"


class TestBuildCompletionArgs:
    @pytest.mark.parametrize("configured_model", ["openai/codex-5.3", "codex-5.3"])
    def test_codex_oauth_forces_openai_prefix(
        self, monkeypatch: pytest.MonkeyPatch, configured_model: str
    ) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name=configured_model, timeout=120)

        monkeypatch.setattr(LLM, "_supports_vision", lambda self: True)
        monkeypatch.setattr(LLM, "_supports_reasoning", lambda self: False)
        monkeypatch.setattr("esprit.llm.llm.PROVIDERS_AVAILABLE", True, raising=False)
        monkeypatch.setattr("esprit.llm.llm.get_provider_api_key", lambda _model: "oauth-token")
        monkeypatch.setattr("esprit.llm.llm.get_provider_api_base", lambda _model: None)
        monkeypatch.setattr("esprit.llm.llm.get_provider_headers", lambda _model: {})
        monkeypatch.setattr("esprit.llm.llm.should_use_oauth", lambda _model: True)
        monkeypatch.setattr("esprit.llm.llm.resolve_api_base", lambda _model: None)

        args = llm._build_completion_args([{"role": "user", "content": "hi"}])

        assert args["model"] == "openai/gpt-5.3-codex"
        assert args["api_key"] == "oauth-token"

    def test_codex_alias_normalizes_without_oauth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="openai/codex-5.2", timeout=120)

        monkeypatch.setattr(LLM, "_supports_vision", lambda self: True)
        monkeypatch.setattr(LLM, "_supports_reasoning", lambda self: False)
        monkeypatch.setattr("esprit.llm.llm.PROVIDERS_AVAILABLE", True, raising=False)
        monkeypatch.setattr("esprit.llm.llm.get_provider_api_key", lambda _model: None)
        monkeypatch.setattr("esprit.llm.llm.get_provider_api_base", lambda _model: None)
        monkeypatch.setattr("esprit.llm.llm.get_provider_headers", lambda _model: {})
        monkeypatch.setattr("esprit.llm.llm.should_use_oauth", lambda _model: False)
        monkeypatch.setattr("esprit.llm.llm.resolve_api_base", lambda _model: None)
        monkeypatch.setattr("esprit.llm.llm.Config.get", lambda _name: None)

        args = llm._build_completion_args([{"role": "user", "content": "hi"}])

        assert args["model"] == "openai/gpt-5.2-codex"

    def test_provider_api_base_used_when_no_explicit_base(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="openai/gpt-5.3-codex", timeout=120)

        monkeypatch.setattr(LLM, "_supports_vision", lambda self: True)
        monkeypatch.setattr(LLM, "_supports_reasoning", lambda self: False)
        monkeypatch.setattr("esprit.llm.llm.PROVIDERS_AVAILABLE", True, raising=False)
        monkeypatch.setattr("esprit.llm.llm.get_provider_api_key", lambda _model: "oauth-token")
        monkeypatch.setattr(
            "esprit.llm.llm.get_provider_api_base",
            lambda _model: "https://chatgpt.com/backend-api/codex",
        )
        monkeypatch.setattr("esprit.llm.llm.get_provider_headers", lambda _model: {})
        monkeypatch.setattr("esprit.llm.llm.should_use_oauth", lambda _model: True)
        monkeypatch.setattr("esprit.llm.llm.resolve_api_base", lambda _model: None)

        args = llm._build_completion_args([{"role": "user", "content": "hi"}])

        assert args["api_base"] == "https://chatgpt.com/backend-api/codex"

    def test_openai_oauth_sets_store_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="openai/gpt-5.2", timeout=120)

        auth_client = SimpleNamespace(detect_provider=lambda _model: "openai")

        monkeypatch.setattr(LLM, "_supports_vision", lambda self: True)
        monkeypatch.setattr(LLM, "_supports_reasoning", lambda self: False)
        monkeypatch.setattr("esprit.llm.llm.PROVIDERS_AVAILABLE", True, raising=False)
        monkeypatch.setattr("esprit.llm.llm.get_auth_client", lambda: auth_client)
        monkeypatch.setattr("esprit.llm.llm.get_provider_api_key", lambda _model: "oauth-token")
        monkeypatch.setattr(
            "esprit.llm.llm.get_provider_api_base",
            lambda _model: "https://chatgpt.com/backend-api/codex",
        )
        monkeypatch.setattr("esprit.llm.llm.get_provider_headers", lambda _model: {})
        monkeypatch.setattr("esprit.llm.llm.should_use_oauth", lambda _model: True)
        monkeypatch.setattr("esprit.llm.llm.resolve_api_base", lambda _model: None)

        args = llm._build_completion_args([{"role": "user", "content": "hi"}])

        assert args["store"] is False
        assert args["extra_body"]["store"] is False

class TestEspritProxyRouting:
    @pytest.mark.asyncio
    async def test_stream_uses_esprit_proxy_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="esprit/default", timeout=120)

        async def fake_esprit_stream(_messages, tools=None):  # noqa: ANN001
            _ = tools
            yield SimpleNamespace(content="ok", tool_invocations=None, thinking_blocks=None)

        async def fail_acompletion(*_args, **_kwargs):  # noqa: ANN002
            raise AssertionError("acompletion should not be used for esprit/* models")

        monkeypatch.setattr(llm, "_stream_esprit_proxy", fake_esprit_stream)
        monkeypatch.setattr("esprit.llm.llm.acompletion", fail_acompletion)

        outputs: list[str] = []
        async for item in llm._stream([{"role": "user", "content": "ping"}]):
            outputs.append(item.content)

        assert outputs == ["ok"]

    @pytest.mark.asyncio
    async def test_stream_esprit_proxy_posts_directly_to_proxy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="esprit/default", timeout=120)
        llm._total_stats = SimpleNamespace(
            requests=0,
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            cost=0.0,
            last_input_tokens=0,
        )
        llm._reasoning_effort = "high"

        monkeypatch.setattr(LLM, "_supports_reasoning", lambda self: False)
        monkeypatch.setattr(LLM, "_update_usage_stats", lambda self, _response: None)
        monkeypatch.setattr(
            "esprit.providers.esprit_subs._load_esprit_credentials",
            lambda: SimpleNamespace(access_token="token"),
        )
        monkeypatch.setattr(
            "esprit.providers.esprit_subs.resolve_bedrock_model",
            lambda _model: "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        )
        monkeypatch.setattr(
            "esprit.providers.esprit_subs.LLM_PROXY_URL",
            "https://example.test/api/v1/llm/generate",
        )

        captured: dict[str, object] = {}

        class FakeResponse:
            status_code = 200
            text = ""

            @staticmethod
            def json() -> dict[str, object]:
                return {
                    "choices": [{"message": {"content": "proxy-ok"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                }

        class FakeAsyncClient:
            def __init__(self, timeout: int):  # noqa: D401
                captured["timeout"] = timeout

            async def __aenter__(self) -> "FakeAsyncClient":
                return self

            async def __aexit__(self, _exc_type, _exc, _tb) -> None:  # noqa: ANN001
                return None

            async def post(
                self, url: str, headers: dict[str, str], json: dict[str, object]  # noqa: A002
            ) -> FakeResponse:
                captured["url"] = url
                captured["headers"] = headers
                captured["json"] = json
                return FakeResponse()

        monkeypatch.setattr("esprit.llm.llm.httpx.AsyncClient", FakeAsyncClient)

        outputs: list[str] = []
        async for item in llm._stream_esprit_proxy([{"role": "user", "content": "ping"}]):
            outputs.append(item.content)

        assert outputs == ["proxy-ok"]
        assert captured["url"] == "https://example.test/api/v1/llm/generate"
        assert captured["timeout"] == 120
        assert captured["headers"]["X-Esprit-Provider"] == "bedrock"
        assert captured["headers"]["X-Esprit-Model"] == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        assert captured["json"]["model"] == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        assert captured["json"]["stream"] is False

    @pytest.mark.asyncio
    async def test_stream_esprit_proxy_accepts_flat_payload_shape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="esprit/default", timeout=120)
        llm._total_stats = SimpleNamespace(
            requests=0,
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            cost=0.0,
            last_input_tokens=0,
        )
        llm._reasoning_effort = "high"

        monkeypatch.setattr(LLM, "_supports_reasoning", lambda self: False)
        monkeypatch.setattr(
            "esprit.providers.esprit_subs._load_esprit_credentials",
            lambda: SimpleNamespace(access_token="token"),
        )
        monkeypatch.setattr(
            "esprit.providers.esprit_subs.resolve_bedrock_model",
            lambda _model: "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        )
        monkeypatch.setattr(
            "esprit.providers.esprit_subs.LLM_PROXY_URL",
            "https://example.test/api/v1/llm/generate",
        )

        observed: dict[str, int] = {}

        def capture_usage(self: LLM, response: object) -> None:
            usage = getattr(response, "usage", None)
            observed["completion_tokens"] = int(getattr(usage, "completion_tokens", 0) or 0)

        monkeypatch.setattr(LLM, "_update_usage_stats", capture_usage)

        class FakeResponse:
            status_code = 200
            text = ""

            @staticmethod
            def json() -> dict[str, object]:
                return {
                    "content": "proxy-flat",
                    "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                    "tokens_used": 7,
                    "finish_reason": "stop",
                }

        class FakeAsyncClient:
            def __init__(self, timeout: int):  # noqa: D401
                _ = timeout

            async def __aenter__(self) -> "FakeAsyncClient":
                return self

            async def __aexit__(self, _exc_type, _exc, _tb) -> None:  # noqa: ANN001
                return None

            async def post(
                self, url: str, headers: dict[str, str], json: dict[str, object]  # noqa: A002
            ) -> FakeResponse:
                _ = (url, headers, json)
                return FakeResponse()

        monkeypatch.setattr("esprit.llm.llm.httpx.AsyncClient", FakeAsyncClient)

        outputs: list[str] = []
        async for item in llm._stream_esprit_proxy([{"role": "user", "content": "ping"}]):
            outputs.append(item.content)

        assert outputs == ["proxy-flat"]
        assert observed["completion_tokens"] == 7

    @pytest.mark.asyncio
    async def test_stream_esprit_proxy_surfaces_http_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="esprit/default", timeout=120)
        llm._total_stats = SimpleNamespace(
            requests=0,
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            cost=0.0,
            last_input_tokens=0,
        )
        llm._reasoning_effort = "high"

        monkeypatch.setattr(LLM, "_supports_reasoning", lambda self: False)
        monkeypatch.setattr(
            "esprit.providers.esprit_subs._load_esprit_credentials",
            lambda: SimpleNamespace(access_token="token"),
        )
        monkeypatch.setattr(
            "esprit.providers.esprit_subs.resolve_bedrock_model",
            lambda _model: "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        )
        monkeypatch.setattr(
            "esprit.providers.esprit_subs.LLM_PROXY_URL",
            "https://example.test/api/v1/llm/generate",
        )

        class FakeErrorResponse:
            status_code = 500
            text = "Internal Server Error"

            @staticmethod
            def json() -> dict[str, object]:
                return {}

        class FakeAsyncClient:
            def __init__(self, timeout: int):  # noqa: D401
                _ = timeout

            async def __aenter__(self) -> "FakeAsyncClient":
                return self

            async def __aexit__(self, _exc_type, _exc, _tb) -> None:  # noqa: ANN001
                return None

            async def post(
                self, url: str, headers: dict[str, str], json: dict[str, object]  # noqa: A002
            ) -> FakeErrorResponse:
                _ = (url, headers, json)
                return FakeErrorResponse()

        monkeypatch.setattr("esprit.llm.llm.httpx.AsyncClient", FakeAsyncClient)

        with pytest.raises(LLMRequestFailedError) as exc:
            async for _ in llm._stream_esprit_proxy([{"role": "user", "content": "ping"}]):
                pass

        assert exc.value.status_code == 500
        assert "Internal Server Error" in (exc.value.details or "")

    @pytest.mark.asyncio
    async def test_stream_esprit_proxy_includes_request_id_in_details(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = LLM.__new__(LLM)
        llm.config = SimpleNamespace(model_name="esprit/default", timeout=120)
        llm._total_stats = SimpleNamespace(
            requests=0,
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            cost=0.0,
            last_input_tokens=0,
        )
        llm._reasoning_effort = "high"

        monkeypatch.setattr(LLM, "_supports_reasoning", lambda self: False)
        monkeypatch.setattr(
            "esprit.providers.esprit_subs._load_esprit_credentials",
            lambda: SimpleNamespace(access_token="token"),
        )
        monkeypatch.setattr(
            "esprit.providers.esprit_subs.resolve_bedrock_model",
            lambda _model: "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        )
        monkeypatch.setattr(
            "esprit.providers.esprit_subs.LLM_PROXY_URL",
            "https://example.test/api/v1/llm/generate",
        )

        class FakeErrorResponse:
            status_code = 500
            text = "Internal Server Error"
            headers = {"x-vercel-id": "test-edge-id"}

            @staticmethod
            def json() -> dict[str, object]:
                return {}

        class FakeAsyncClient:
            def __init__(self, timeout: int):
                _ = timeout

            async def __aenter__(self) -> "FakeAsyncClient":
                return self

            async def __aexit__(self, _exc_type, _exc, _tb) -> None:
                return None

            async def post(
                self, url: str, headers: dict[str, str], json: dict[str, object]
            ) -> FakeErrorResponse:
                _ = (url, headers, json)
                return FakeErrorResponse()

        monkeypatch.setattr("esprit.llm.llm.httpx.AsyncClient", FakeAsyncClient)

        with pytest.raises(LLMRequestFailedError) as exc:
            async for _ in llm._stream_esprit_proxy([{"role": "user", "content": "ping"}]):
                pass

        assert "request_id: test-edge-id" in (exc.value.details or "")


class TestRaiseError:
    def test_preserves_llm_request_failed_error_details(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = LLM.__new__(LLM)
        monkeypatch.setattr("esprit.telemetry.posthog.error", lambda *_args, **_kwargs: None)

        with pytest.raises(LLMRequestFailedError) as exc:
            llm._raise_error(
                LLMRequestFailedError(
                    "LLM request failed: Esprit proxy returned HTTP 500",
                    details="Internal Server Error [request_id: abc123]",
                    status_code=500,
                )
            )

        assert exc.value.status_code == 500
        assert exc.value.details == "Internal Server Error [request_id: abc123]"
