"""Tests for Antigravity/Cloud Code format conversion."""

import json

import pytest

from esprit.providers.antigravity_format import (
    _convert_messages,
    _convert_tools,
    _sanitize_schema,
    build_cloudcode_request,
    parse_finish_reason,
    parse_sse_chunk,
)


# ── Schema Sanitization ───────────────────────────────────────


class TestSanitizeSchema:
    def test_basic_string(self) -> None:
        result = _sanitize_schema({"type": "string"})
        assert result == {"type": "STRING"}

    def test_integer(self) -> None:
        result = _sanitize_schema({"type": "integer", "description": "A number"})
        assert result == {"type": "INTEGER", "description": "A number"}

    def test_array_with_items(self) -> None:
        result = _sanitize_schema({
            "type": "array",
            "items": {"type": "string"},
        })
        assert result["type"] == "ARRAY"
        assert result["items"]["type"] == "STRING"

    def test_object_with_properties(self) -> None:
        result = _sanitize_schema({
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name"],
        })
        assert result["type"] == "OBJECT"
        assert "name" in result["properties"]
        assert result["required"] == ["name"]

    def test_nullable_type_picks_non_null(self) -> None:
        result = _sanitize_schema({"type": ["string", "null"]})
        assert result["type"] == "STRING"

    def test_anyof_picks_first_non_null(self) -> None:
        result = _sanitize_schema({
            "anyOf": [
                {"type": "null"},
                {"type": "integer", "description": "count"},
            ]
        })
        assert result["type"] == "INTEGER"
        assert result["description"] == "count"

    def test_unsupported_keywords_stripped(self) -> None:
        result = _sanitize_schema({
            "type": "string",
            "additionalProperties": False,
            "default": "foo",
            "$schema": "http://...",
            "title": "MyField",
        })
        assert "additionalProperties" not in result
        assert "default" not in result
        assert "$schema" not in result
        assert "title" not in result

    def test_enum(self) -> None:
        result = _sanitize_schema({"type": "string", "enum": ["a", "b", "c"]})
        assert result["enum"] == ["a", "b", "c"]

    def test_non_dict_returns_none(self) -> None:
        assert _sanitize_schema("not a dict") is None
        assert _sanitize_schema(42) is None
        assert _sanitize_schema(None) is None


# ── Message Conversion ────────────────────────────────────────


class TestConvertMessages:
    def test_system_message(self) -> None:
        msgs = [{"role": "system", "content": "You are helpful."}]
        sys_instr, contents = _convert_messages(msgs)
        assert sys_instr is not None
        assert sys_instr["parts"][0]["text"] == "You are helpful."
        assert contents == []

    def test_user_message(self) -> None:
        msgs = [{"role": "user", "content": "Hello"}]
        sys_instr, contents = _convert_messages(msgs)
        assert sys_instr is None
        assert len(contents) == 1
        assert contents[0]["role"] == "user"
        assert contents[0]["parts"][0]["text"] == "Hello"

    def test_assistant_message(self) -> None:
        msgs = [{"role": "assistant", "content": "Hi there!"}]
        _, contents = _convert_messages(msgs)
        assert contents[0]["role"] == "model"
        assert contents[0]["parts"][0]["text"] == "Hi there!"

    def test_tool_call_id_to_function_name_mapping(self) -> None:
        """Critical fix C3: functionResponse.name should be the function name, not the tool_call_id."""
        msgs = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_abc123",
                        "type": "function",
                        "function": {
                            "name": "terminal_execute",
                            "arguments": '{"command": "ls"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_abc123",
                "content": "file1.txt\nfile2.txt",
            },
        ]
        _, contents = _convert_messages(msgs)

        # Assistant message with tool call
        assert len(contents) >= 2
        fc = contents[0]["parts"][0]["functionCall"]
        assert fc["name"] == "terminal_execute"
        assert fc["id"] == "call_abc123"

        # Tool result — name should be "terminal_execute", NOT "call_abc123"
        fr = contents[1]["parts"][0]["functionResponse"]
        assert fr["name"] == "terminal_execute"
        assert fr["id"] == "call_abc123"

    def test_tool_call_id_fallback_when_no_mapping(self) -> None:
        """If tool response appears without prior assistant tool_call, falls back to tool_call_id."""
        msgs = [
            {
                "role": "tool",
                "tool_call_id": "call_orphan",
                "content": "some result",
            },
        ]
        _, contents = _convert_messages(msgs)
        fr = contents[0]["parts"][0]["functionResponse"]
        # Falls back to using the id as name
        assert fr["name"] == "call_orphan"

    def test_multiple_tool_calls_mapped_correctly(self) -> None:
        """Multiple tool calls should each map to the correct function name."""
        msgs = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                    {"id": "call_2", "type": "function", "function": {"name": "write_file", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "contents"},
            {"role": "tool", "tool_call_id": "call_2", "content": "ok"},
        ]
        _, contents = _convert_messages(msgs)
        # Tool responses
        assert contents[1]["parts"][0]["functionResponse"]["name"] == "read_file"
        assert contents[2]["parts"][0]["functionResponse"]["name"] == "write_file"

    def test_tool_result_json_parsed(self) -> None:
        """JSON string tool results should be parsed to dict."""
        msgs = [
            {"role": "tool", "tool_call_id": "call_x", "content": '{"status": "ok"}'},
        ]
        _, contents = _convert_messages(msgs)
        resp = contents[0]["parts"][0]["functionResponse"]["response"]
        assert resp == {"status": "ok"}

    def test_tool_result_plain_string_wrapped(self) -> None:
        """Non-JSON tool results should be wrapped in {"result": ...}."""
        msgs = [
            {"role": "tool", "tool_call_id": "call_x", "content": "plain text"},
        ]
        _, contents = _convert_messages(msgs)
        resp = contents[0]["parts"][0]["functionResponse"]["response"]
        assert resp == {"result": "plain text"}


# ── Tool Conversion ───────────────────────────────────────────


class TestConvertTools:
    def test_basic_function(self) -> None:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather for a location",
                    "parameters": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                        "required": ["location"],
                    },
                },
            }
        ]
        result = _convert_tools(tools)
        assert result is not None
        decls = result[0]["functionDeclarations"]
        assert len(decls) == 1
        assert decls[0]["name"] == "get_weather"
        assert "parameters" in decls[0]

    def test_empty_tools(self) -> None:
        assert _convert_tools(None) is None
        assert _convert_tools([]) is None

    def test_non_function_tools_skipped(self) -> None:
        tools = [{"type": "code_interpreter"}]
        assert _convert_tools(tools) is None


# ── Request Building ──────────────────────────────────────────


class TestBuildRequest:
    def test_basic_request(self) -> None:
        msgs = [{"role": "user", "content": "Hello"}]
        req = build_cloudcode_request(msgs, "claude-opus-4-6", "proj-123")
        assert req["model"] == "claude-opus-4-6"
        assert req["project"] == "proj-123"
        assert req["requestType"] == "agent"
        assert "contents" in req["request"]

    def test_thinking_config_claude(self) -> None:
        """Cloud Code uses snake_case for Claude thinking config."""
        msgs = [{"role": "user", "content": "Think about this"}]
        req = build_cloudcode_request(msgs, "claude-opus-4-6-thinking", "proj-123", max_tokens=8192)
        tc = req["request"]["generationConfig"]["thinkingConfig"]
        assert "include_thoughts" in tc
        assert "thinking_budget" in tc
        # maxOutputTokens must be > thinking_budget
        assert req["request"]["generationConfig"]["maxOutputTokens"] > tc["thinking_budget"]

    def test_thinking_config_gemini(self) -> None:
        """Gemini uses camelCase for thinking config."""
        msgs = [{"role": "user", "content": "Think"}]
        req = build_cloudcode_request(msgs, "gemini-2.5-flash-thinking", "proj-123")
        tc = req["request"]["generationConfig"]["thinkingConfig"]
        assert "includeThoughts" in tc
        assert "thinkingBudget" in tc

    def test_tools_with_claude_validated_mode(self) -> None:
        tools = [
            {"type": "function", "function": {"name": "f1", "description": "d1", "parameters": {"type": "object"}}},
        ]
        req = build_cloudcode_request(
            [{"role": "user", "content": "hi"}],
            "claude-sonnet-4-5",
            "proj",
            tools=tools,
        )
        assert req["request"]["toolConfig"]["functionCallingConfig"]["mode"] == "VALIDATED"


# ── Response Parsing ──────────────────────────────────────────


class TestParseSSEChunk:
    def test_text_content(self) -> None:
        chunk = {
            "response": {
                "candidates": [
                    {"content": {"parts": [{"text": "Hello!"}]}}
                ]
            }
        }
        text, thinking, tools, usage = parse_sse_chunk(chunk)
        assert text == "Hello!"
        assert thinking == []
        assert tools == []

    def test_thinking_block(self) -> None:
        chunk = {
            "response": {
                "candidates": [
                    {"content": {"parts": [{"text": "reasoning...", "thought": True}]}}
                ]
            }
        }
        text, thinking, tools, usage = parse_sse_chunk(chunk)
        assert text == ""
        assert len(thinking) == 1
        assert thinking[0]["thinking"] == "reasoning..."

    def test_function_call(self) -> None:
        chunk = {
            "response": {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "read_file",
                                        "args": {"path": "/tmp/x"},
                                        "id": "call_xyz",
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        }
        text, thinking, tools, usage = parse_sse_chunk(chunk)
        assert text == ""
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "read_file"
        assert tools[0]["id"] == "call_xyz"

    def test_usage_metadata(self) -> None:
        chunk = {
            "response": {
                "candidates": [],
                "usageMetadata": {
                    "promptTokenCount": 100,
                    "cachedContentTokenCount": 20,
                    "candidatesTokenCount": 50,
                },
            }
        }
        text, thinking, tools, usage = parse_sse_chunk(chunk)
        assert usage["input_tokens"] == 80
        assert usage["output_tokens"] == 50
        assert usage["cached_tokens"] == 20


class TestParseFinishReason:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("STOP", "end_turn"),
            ("MAX_TOKENS", "max_tokens"),
            ("TOOL_USE", "tool_use"),
            ("SAFETY", "SAFETY"),
        ],
    )
    def test_known_reasons(self, raw: str, expected: str) -> None:
        chunk = {"response": {"candidates": [{"finishReason": raw}]}}
        assert parse_finish_reason(chunk) == expected

    def test_no_candidates(self) -> None:
        assert parse_finish_reason({"response": {"candidates": []}}) is None
