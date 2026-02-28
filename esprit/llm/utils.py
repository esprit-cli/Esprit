import html
import re
from typing import Any


_FUNCTION_OPEN_PATTERN = r"<function(?:=[^>]+)?>"


def _truncate_to_first_function(content: str) -> str:
    if not content:
        return content

    function_starts = [match.start() for match in re.finditer(_FUNCTION_OPEN_PATTERN, content)]

    if len(function_starts) >= 2:
        second_function_start = function_starts[1]

        return content[:second_function_start].rstrip()

    return content


def _extract_implicit_function_name(body: str) -> str:
    stripped = body.strip()
    if not stripped:
        return ""

    first_line = stripped.splitlines()[0].strip()
    if not first_line:
        return ""

    # Common malformed fallback form:
    #   <function>tool_name
    #   <parameter>...</parameter>
    if not first_line.startswith("<"):
        candidate = first_line.rstrip(">").strip()
        match = re.match(r"^[A-Za-z_][A-Za-z0-9_.:-]*$", candidate)
        return candidate if match else ""

    name_tag_match = re.match(r"<name>\s*([A-Za-z_][A-Za-z0-9_.:-]*)\s*</name>", first_line)
    if name_tag_match:
        return name_tag_match.group(1).strip()

    return ""


def _parse_function_parameters(body: str) -> dict[str, str]:
    args: dict[str, str] = {}

    strict_matches = re.finditer(r"<parameter=([^>]+)>(.*?)</parameter>", body, re.DOTALL)
    for match in strict_matches:
        param_name = match.group(1).strip()
        param_value = html.unescape(match.group(2).strip())
        if param_name:
            args[param_name] = param_value

    # Relaxed fallback for malformed parameter blocks:
    #   <parameter>name>value</parameter>
    #   <parameter>name:value</parameter>
    relaxed_matches = re.finditer(
        r"<parameter>\s*([A-Za-z_][A-Za-z0-9_.:-]*)\s*(?:>|:|=)\s*(.*?)</parameter>",
        body,
        re.DOTALL,
    )
    for match in relaxed_matches:
        param_name = match.group(1).strip()
        param_value = html.unescape(match.group(2).strip())
        if param_name:
            args[param_name] = param_value

    named_matches = re.finditer(
        r'<parameter[^>]*name="([A-Za-z_][A-Za-z0-9_.:-]*)"[^>]*>(.*?)</parameter>',
        body,
        re.DOTALL | re.IGNORECASE,
    )
    for match in named_matches:
        param_name = match.group(1).strip()
        param_value = html.unescape(match.group(2).strip())
        if param_name:
            args[param_name] = param_value

    return args


def parse_tool_invocations(content: str) -> list[dict[str, Any]] | None:
    content = fix_incomplete_tool_call(content)

    tool_invocations: list[dict[str, Any]] = []

    function_matches = re.finditer(
        r"<function(?:=([^>]+))?>\n?(.*?)</function>",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    for match in function_matches:
        fn_name = (match.group(1) or "").strip()
        fn_body = match.group(2)
        if not fn_name:
            fn_name = _extract_implicit_function_name(fn_body)
        if not fn_name:
            continue

        args = _parse_function_parameters(fn_body)

        tool_invocations.append({"toolName": fn_name, "args": args})

    return tool_invocations if tool_invocations else None


def fix_incomplete_tool_call(content: str) -> str:
    """Fix incomplete tool calls by adding missing </function> tag."""
    if (
        re.search(_FUNCTION_OPEN_PATTERN, content)
        and len(re.findall(_FUNCTION_OPEN_PATTERN, content)) == 1
        and "</function>" not in content
    ):
        content = content.rstrip()
        content = content + "function>" if content.endswith("</") else content + "\n</function>"
    return content


def format_tool_call(tool_name: str, args: dict[str, Any]) -> str:
    xml_parts = [f"<function={tool_name}>"]

    for key, value in args.items():
        xml_parts.append(f"<parameter={key}>{value}</parameter>")

    xml_parts.append("</function>")

    return "\n".join(xml_parts)


def clean_content(content: str) -> str:
    if not content:
        return ""

    content = fix_incomplete_tool_call(content)

    tool_pattern = r"<function(?:=[^>]+)?>.*?</function>"
    cleaned = re.sub(tool_pattern, "", content, flags=re.DOTALL)

    incomplete_tool_pattern = r"<function(?:=[^>]+)?>.*$"
    cleaned = re.sub(incomplete_tool_pattern, "", cleaned, flags=re.DOTALL)

    partial_tag_pattern = r"<f(?:u(?:n(?:c(?:t(?:i(?:o(?:n(?:=(?:[^>]*)?)?)?)?)?)?)?)?)?$"
    cleaned = re.sub(partial_tag_pattern, "", cleaned)

    hidden_xml_patterns = [
        r"<inter_agent_message>.*?</inter_agent_message>",
        r"<agent_completion_report>.*?</agent_completion_report>",
    ]
    for pattern in hidden_xml_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.DOTALL | re.IGNORECASE)

    cleaned = re.sub(r"\n\s*\n", "\n\n", cleaned)

    return cleaned.strip()
