from esprit.llm.utils import clean_content, parse_tool_invocations


def test_parse_strict_tool_call_format() -> None:
    payload = """
<function=terminal_execute>
<parameter=command>echo hello</parameter>
</function>
"""
    invocations = parse_tool_invocations(payload)
    assert invocations == [{"toolName": "terminal_execute", "args": {"command": "echo hello"}}]


def test_parse_malformed_function_and_parameter_tags() -> None:
    payload = """
<function>create_vulnerability_report
<parameter>title>SQL Injection in login</parameter>
<parameter>target>https://example.test/login</parameter>
</function>
"""
    invocations = parse_tool_invocations(payload)
    assert invocations == [
        {
            "toolName": "create_vulnerability_report",
            "args": {
                "title": "SQL Injection in login",
                "target": "https://example.test/login",
            },
        }
    ]


def test_parse_function_name_only_block() -> None:
    payload = "<function>create_agent</function>"
    invocations = parse_tool_invocations(payload)
    assert invocations == [{"toolName": "create_agent", "args": {}}]


def test_fix_incomplete_relaxed_tool_call() -> None:
    payload = "<function>create_agent\n<parameter>task>Enumerate auth paths</parameter>"
    invocations = parse_tool_invocations(payload)
    assert invocations == [
        {"toolName": "create_agent", "args": {"task": "Enumerate auth paths"}}
    ]


def test_clean_content_removes_relaxed_function_block() -> None:
    payload = "Thinking...\n<function>create_agent</function>\nDone."
    cleaned = clean_content(payload)
    assert "Thinking..." in cleaned
    assert "Done." in cleaned
    assert "<function" not in cleaned
