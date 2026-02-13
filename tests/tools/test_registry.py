"""Tests for tool schema parsing and JSON export."""

from esprit.tools import get_tool_names, get_tools_json
from esprit.tools.registry import _parse_param_schema, _xml_to_json_schema


SAMPLE_TOOL_XML = '''
<tool name="demo_tool">
  <description>Main description.</description>
  <details>Extra details.</details>
  <parameters>
    <parameter name="query" type="string" required="true">
      <description>Regex: r"https?://[^\\s<>\"']+"</description>
    </parameter>
    <parameter name="headers" type="dict" required="false">
      <description>Optional headers & metadata</description>
    </parameter>
  </parameters>
  <examples>
    <function=demo_tool>
    <parameter=query>/api/test</parameter>
    </function>
  </examples>
</tool>
'''


class TestXmlSchemaConversion:
    def test_converts_schema_with_examples_and_unescaped_ampersand(self) -> None:
        schema = _xml_to_json_schema("demo_tool", SAMPLE_TOOL_XML)

        assert schema is not None
        function = schema["function"]
        assert function["name"] == "demo_tool"
        assert function["description"] == "Main description.\n\nExtra details."

        params = function["parameters"]
        properties = params["properties"]
        assert properties["query"]["type"] == "string"
        assert properties["headers"]["type"] == "object"
        assert set(params["required"]) == {"query"}

    def test_description_uses_details_without_leading_newlines(self) -> None:
        xml_with_details_only = '''
<tool name="details_only">
  <details>Details-only description.</details>
  <examples>
    <function=details_only>
    </function>
  </examples>
</tool>
'''
        schema = _xml_to_json_schema("details_only", xml_with_details_only)

        assert schema is not None
        assert schema["function"]["description"] == "Details-only description."


class TestParamSchemaParsing:
    def test_extracts_parameters_from_schema_with_examples(self) -> None:
        parsed = _parse_param_schema(SAMPLE_TOOL_XML)

        assert parsed["has_params"] is True
        assert parsed["params"] == {"query", "headers"}
        assert parsed["required"] == {"query"}


class TestToolJsonExport:
    def test_exports_json_schema_for_all_registered_tools(self) -> None:
        tool_names = set(get_tool_names())
        json_names = {tool["function"]["name"] for tool in get_tools_json()}

        assert json_names == tool_names
