import inspect
import html
import logging
import os
import re
from collections.abc import Callable
from functools import wraps
from inspect import signature
from pathlib import Path
from typing import Any

from esprit.utils.resource_paths import get_esprit_resource_path


tools: list[dict[str, Any]] = []
_tools_by_name: dict[str, Callable[..., Any]] = {}
_tool_param_schemas: dict[str, dict[str, Any]] = {}
logger = logging.getLogger(__name__)


class ImplementedInClientSideOnlyError(Exception):
    def __init__(
        self,
        message: str = "This tool is implemented in the client side only",
    ) -> None:
        self.message = message
        super().__init__(self.message)


def _process_dynamic_content(content: str) -> str:
    if "{{DYNAMIC_SKILLS_DESCRIPTION}}" in content:
        try:
            from esprit.skills import generate_skills_description

            skills_description = generate_skills_description()
            content = content.replace("{{DYNAMIC_SKILLS_DESCRIPTION}}", skills_description)
        except ImportError:
            logger.warning("Could not import skills utilities for dynamic schema generation")
            content = content.replace(
                "{{DYNAMIC_SKILLS_DESCRIPTION}}",
                "List of skills to load for this agent (max 5). Skill discovery failed.",
            )

    return content


def _load_xml_schema(path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
    try:
        content = path.read_text()

        content = _process_dynamic_content(content)

        start_tag = '<tool name="'
        end_tag = "</tool>"
        tools_dict: dict[str, str] = {}

        pos = 0
        while True:
            start_pos = content.find(start_tag, pos)
            if start_pos == -1:
                break

            name_start = start_pos + len(start_tag)
            name_end = content.find('"', name_start)
            if name_end == -1:
                break
            tool_name = content[name_start:name_end]

            end_pos = content.find(end_tag, name_end)
            if end_pos == -1:
                break
            end_pos += len(end_tag)

            tool_element = content[start_pos:end_pos]
            tools_dict[tool_name] = tool_element

            pos = end_pos

            if pos >= len(content):
                break
    except (IndexError, ValueError, UnicodeError) as e:
        logger.warning(f"Error loading schema file {path}: {e}")
        return None
    else:
        return tools_dict


_XML_TYPE_TO_JSON: dict[str, str] = {
    "string": "string",
    "boolean": "boolean",
    "number": "number",
    "integer": "integer",
    "dict": "object",
    "list": "array",
}

_EXAMPLES_BLOCK_RE = re.compile(r"<examples>.*?</examples>", re.DOTALL | re.IGNORECASE)
_PARAMETER_BLOCK_RE = re.compile(
    r"<parameter(?P<attrs>[^>]*)>(?P<body>.*?)</parameter>",
    re.DOTALL | re.IGNORECASE,
)
_TAG_ATTR_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_-]*)="([^"]*)"')


def _strip_examples_block(xml_string: str) -> str:
    return _EXAMPLES_BLOCK_RE.sub("", xml_string)


def _extract_first_tag_body(xml_string: str, tag: str) -> str:
    match = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", xml_string, re.DOTALL | re.IGNORECASE)
    if not match:
        return ""
    return html.unescape(match.group(1).strip())


def _parse_tag_attributes(attr_text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for key, value in _TAG_ATTR_RE.findall(attr_text):
        attrs[key.lower()] = html.unescape(value.strip())
    return attrs


def _iter_tool_parameters(tool_xml: str) -> list[dict[str, str]]:
    params_section = _extract_first_tag_body(tool_xml, "parameters")
    if not params_section:
        return []

    parsed: list[dict[str, str]] = []
    for match in _PARAMETER_BLOCK_RE.finditer(params_section):
        attrs = _parse_tag_attributes(match.group("attrs"))
        name = attrs.get("name")
        if not name:
            continue

        param_desc = _extract_first_tag_body(match.group("body"), "description")
        parsed.append({
            "name": name,
            "type": attrs.get("type", "string"),
            "required": attrs.get("required", "false"),
            "description": param_desc,
        })

    return parsed


def _xml_to_json_schema(tool_name: str, xml_string: str) -> dict[str, Any] | None:
    """Convert a single tool's XML schema to litellm-compatible JSON tool definition."""
    sanitized_xml = _strip_examples_block(xml_string)

    # Build description from <description> + <details>
    description = _extract_first_tag_body(sanitized_xml, "description")
    details = _extract_first_tag_body(sanitized_xml, "details")
    if details:
        description = f"{description}\n\n{details}" if description else details

    # Build parameters
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param in _iter_tool_parameters(sanitized_xml):
        param_name = param["name"]
        param_type = param.get("type", "string").lower()
        json_type = _XML_TYPE_TO_JSON.get(param_type, "string")
        param_desc = param.get("description", "")

        param_schema: dict[str, Any] = {"type": json_type, "description": param_desc}
        if json_type == "array":
            # OpenAI-compatible function schemas require explicit `items` for arrays.
            # XML `type="list"` in our tool schemas currently represents list[str].
            param_schema["items"] = {"type": "string"}

        properties[param_name] = param_schema

        if param.get("required", "false").lower() == "true":
            required.append(param_name)

    parameters: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = required

    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description,
            "parameters": parameters,
        },
    }


def _parse_param_schema(tool_xml: str) -> dict[str, Any]:
    params: set[str] = set()
    required: set[str] = set()

    sanitized_xml = _strip_examples_block(tool_xml)
    for param in _iter_tool_parameters(sanitized_xml):
        name = param.get("name")
        if not name:
            continue
        params.add(name)
        if param.get("required", "false").lower() == "true":
            required.add(name)

    return {"params": params, "required": required, "has_params": bool(params or required)}


def _get_module_name(func: Callable[..., Any]) -> str:
    module = inspect.getmodule(func)
    if not module:
        return "unknown"

    module_name = module.__name__
    if ".tools." in module_name:
        parts = module_name.split(".tools.")[-1].split(".")
        if len(parts) >= 1:
            return parts[0]
    return "unknown"


def _get_schema_path(func: Callable[..., Any]) -> Path | None:
    module = inspect.getmodule(func)
    if not module or not module.__name__:
        return None

    module_name = module.__name__

    if ".tools." not in module_name:
        return None

    parts = module_name.split(".tools.")[-1].split(".")
    if len(parts) < 2:
        return None

    folder = parts[0]
    file_stem = parts[1]
    schema_file = f"{file_stem}_schema.xml"

    return get_esprit_resource_path("tools", folder, schema_file)


def register_tool(
    func: Callable[..., Any] | None = None, *, sandbox_execution: bool = True
) -> Callable[..., Any]:
    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        func_dict: dict[str, Any] = {
            "name": f.__name__,
            "function": f,
            "module": _get_module_name(f),
            "sandbox_execution": sandbox_execution,
        }

        sandbox_mode = os.getenv("ESPRIT_SANDBOX_MODE", "false").lower() == "true"
        if not sandbox_mode:
            try:
                schema_path = _get_schema_path(f)
                xml_tools = _load_xml_schema(schema_path) if schema_path else None

                if xml_tools is not None and f.__name__ in xml_tools:
                    func_dict["xml_schema"] = xml_tools[f.__name__]
                else:
                    func_dict["xml_schema"] = (
                        f'<tool name="{f.__name__}">'
                        "<description>Schema not found for tool.</description>"
                        "</tool>"
                    )
            except (TypeError, FileNotFoundError) as e:
                logger.warning(f"Error loading schema for {f.__name__}: {e}")
                func_dict["xml_schema"] = (
                    f'<tool name="{f.__name__}">'
                    "<description>Error loading schema.</description>"
                    "</tool>"
                )

        if not sandbox_mode:
            xml_schema = func_dict.get("xml_schema")
            param_schema = _parse_param_schema(xml_schema if isinstance(xml_schema, str) else "")
            _tool_param_schemas[str(func_dict["name"])] = param_schema

            # Generate JSON schema for native tool calling
            if isinstance(xml_schema, str):
                json_schema = _xml_to_json_schema(f.__name__, xml_schema)
                if json_schema:
                    func_dict["json_schema"] = json_schema

        tools.append(func_dict)
        _tools_by_name[str(func_dict["name"])] = f

        @wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return f(*args, **kwargs)

        return wrapper

    if func is None:
        return decorator
    return decorator(func)


def get_tool_by_name(name: str) -> Callable[..., Any] | None:
    return _tools_by_name.get(name)


def get_tool_names() -> list[str]:
    return list(_tools_by_name.keys())


def get_tool_param_schema(name: str) -> dict[str, Any] | None:
    return _tool_param_schemas.get(name)


def needs_agent_state(tool_name: str) -> bool:
    tool_func = get_tool_by_name(tool_name)
    if not tool_func:
        return False
    sig = signature(tool_func)
    return "agent_state" in sig.parameters


def should_execute_in_sandbox(tool_name: str) -> bool:
    for tool in tools:
        if tool.get("name") == tool_name:
            return bool(tool.get("sandbox_execution", True))
    return True


def get_tools_json(tool_names: list[str] | None = None) -> list[dict[str, Any]]:
    """Return JSON tool definitions for native tool calling via litellm.

    Args:
        tool_names: If provided, only return schemas for these tools.
                    If None, return all tools that have JSON schemas.
    """
    result: list[dict[str, Any]] = []
    for tool in tools:
        json_schema = tool.get("json_schema")
        if not json_schema:
            continue
        if tool_names is not None and tool["name"] not in tool_names:
            continue
        result.append(json_schema)
    return result


def get_tools_prompt() -> str:
    tools_by_module: dict[str, list[dict[str, Any]]] = {}
    for tool in tools:
        module = tool.get("module", "unknown")
        if module not in tools_by_module:
            tools_by_module[module] = []
        tools_by_module[module].append(tool)

    xml_sections: list[str] = []
    for module, module_tools in sorted(tools_by_module.items()):
        tag_name = f"{module}_tools"
        section_parts: list[str] = [f"<{tag_name}>"]
        for tool in module_tools:
            tool_xml = tool.get("xml_schema", "")
            if tool_xml:
                indented_tool = "\n".join(f"  {line}" for line in tool_xml.split("\n"))
                section_parts.append(indented_tool)
        section_parts.append(f"</{tag_name}>")
        xml_sections.append("\n".join(section_parts))

    return "\n\n".join(xml_sections)


def clear_registry() -> None:
    tools.clear()
    _tools_by_name.clear()
    _tool_param_schemas.clear()
