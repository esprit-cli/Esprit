from typing import Any, ClassVar

from rich.text import Text
from textual.widgets import Static

from esprit.interface.theme_tokens import get_marker_color, get_theme_tokens_from_tool_data

from .base_renderer import BaseToolRenderer
from .registry import register_tool_renderer


@register_tool_renderer
class ThinkRenderer(BaseToolRenderer):
    tool_name: ClassVar[str] = "think"
    css_classes: ClassVar[list[str]] = ["tool-call", "thinking-tool"]

    @classmethod
    def render(cls, tool_data: dict[str, Any]) -> Static:
        args = tool_data.get("args", {})
        thought = args.get("thought", "")
        tokens = get_theme_tokens_from_tool_data(tool_data)
        accent = str(tokens.get("accent", "#a855f7"))
        marker_color = get_marker_color(tokens, "think")
        muted = str(tokens.get("muted", "#9ca3af"))

        text = Text()
        text.append("[think] ", style=f"bold {marker_color}")
        text.append("Thinking", style=f"bold {accent}")
        text.append("\n  ")

        if thought:
            text.append(thought, style=f"italic {muted}")
        else:
            text.append("Thinking...", style=f"italic {muted}")

        css_classes = cls.get_css_classes("completed")
        return Static(text, classes=css_classes)
