from typing import Any, ClassVar

from rich.text import Text
from textual.widgets import Static

from esprit.interface.theme_tokens import get_marker_color, get_theme_tokens_from_tool_data

from .base_renderer import BaseToolRenderer
from .registry import register_tool_renderer


@register_tool_renderer
class WebSearchRenderer(BaseToolRenderer):
    tool_name: ClassVar[str] = "web_search"
    css_classes: ClassVar[list[str]] = ["tool-call", "web-search-tool"]

    @classmethod
    def render(cls, tool_data: dict[str, Any]) -> Static:
        args = tool_data.get("args", {})
        query = args.get("query", "")
        tokens = get_theme_tokens_from_tool_data(tool_data)
        info = str(tokens.get("info", "#60a5fa"))
        web_marker = get_marker_color(tokens, "web")
        muted = str(tokens.get("muted", "#9ca3af"))

        text = Text()
        text.append("[web] ", style=f"bold {web_marker}")
        text.append("Searching the web...", style=f"bold {info}")

        if query:
            text.append("\n  ")
            text.append(query, style=f"dim {muted}")

        css_classes = cls.get_css_classes("completed")
        return Static(text, classes=css_classes)
