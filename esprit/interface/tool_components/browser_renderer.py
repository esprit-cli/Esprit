from functools import cache
from typing import Any, ClassVar

from pygments.lexers import get_lexer_by_name
from pygments.styles import get_style_by_name
from rich.console import Group
from rich.text import Text
from textual.widgets import Static

from esprit.interface.theme_tokens import get_theme_tokens_from_tool_data

from .base_renderer import BaseToolRenderer
from .registry import register_tool_renderer


@cache
def _get_style_colors() -> dict[Any, str]:
    style = get_style_by_name("native")
    return {token: f"#{style_def['color']}" for token, style_def in style if style_def["color"]}


@register_tool_renderer
class BrowserRenderer(BaseToolRenderer):
    tool_name: ClassVar[str] = "browser_action"
    css_classes: ClassVar[list[str]] = ["tool-call", "browser-tool"]

    SIMPLE_ACTIONS: ClassVar[dict[str, str]] = {
        "back": "going back in browser history",
        "forward": "going forward in browser history",
        "scroll_down": "scrolling down",
        "scroll_up": "scrolling up",
        "refresh": "refreshing browser tab",
        "close_tab": "closing browser tab",
        "switch_tab": "switching browser tab",
        "list_tabs": "listing browser tabs",
        "view_source": "viewing page source",
        "get_console_logs": "getting console logs",
        "screenshot": "taking screenshot of browser tab",
        "wait": "waiting...",
        "close": "closing browser",
    }

    @classmethod
    def _get_token_color(cls, token_type: Any) -> str | None:
        colors = _get_style_colors()
        while token_type:
            if token_type in colors:
                return colors[token_type]
            token_type = token_type.parent
        return None

    @classmethod
    def _highlight_js(cls, code: str) -> Text:
        lexer = get_lexer_by_name("javascript")
        text = Text()

        for token_type, token_value in lexer.get_tokens(code):
            if not token_value:
                continue
            color = cls._get_token_color(token_type)
            text.append(token_value, style=color)

        return text

    @classmethod
    def render(cls, tool_data: dict[str, Any]) -> Static:
        args = tool_data.get("args", {})
        status = tool_data.get("status", "unknown")
        tokens = get_theme_tokens_from_tool_data(tool_data)

        action = args.get("action", "")
        content = cls._build_content(action, args, tokens)

        css_classes = cls.get_css_classes(status)

        # Show screenshot preview when the action is completed
        if status == "completed":
            preview = cls._build_screenshot_preview(tool_data, args, tokens)
            if preview is not None:
                return Static(Group(content, Text(""), preview), classes=css_classes)

        return Static(content, classes=css_classes)

    @classmethod
    def _build_screenshot_preview(
        cls, tool_data: dict[str, Any], args: dict[str, Any], tokens: dict[str, Any]
    ) -> Text | None:
        """Try to render a screenshot preview from the tool result."""
        result = tool_data.get("result")
        if not isinstance(result, dict):
            return None

        screenshot_b64 = result.get("screenshot")
        if not screenshot_b64 or not isinstance(screenshot_b64, str) or screenshot_b64 == "[rendered]":
            return None

        try:
            from esprit.interface.image_renderer import screenshot_to_rich_text

            url_label = result.get("url") or args.get("url") or ""
            return screenshot_to_rich_text(
                screenshot_b64, max_width=0, url_label=url_label, theme_tokens=tokens
            )
        except Exception:
            return None

    @classmethod
    def _build_url_action(
        cls, text: Text, label: str, url: str | None, color: str, suffix: str = ""
    ) -> None:
        text.append(label, style=color)
        if url:
            text.append(url, style=color)
            if suffix:
                text.append(suffix, style=color)

    @classmethod
    def _build_content(cls, action: str, args: dict[str, Any], tokens: dict[str, Any]) -> Text:
        info = str(tokens.get("info", "#06b6d4"))
        text = Text()
        text.append("[web] ", style=f"bold {info}")

        if action in cls.SIMPLE_ACTIONS:
            text.append(cls.SIMPLE_ACTIONS[action], style=info)
            return text

        url = args.get("url")

        url_actions = {
            "launch": ("launching ", " on browser" if url else "browser"),
            "goto": ("navigating to ", ""),
            "new_tab": ("opening tab ", ""),
        }
        if action in url_actions:
            label, suffix = url_actions[action]
            if action == "launch" and not url:
                text.append("launching browser", style=info)
            else:
                cls._build_url_action(text, label, url, info, suffix)
            return text

        click_actions = {
            "click": "clicking",
            "double_click": "double clicking",
            "hover": "hovering",
        }
        if action in click_actions:
            text.append(click_actions[action], style=info)
            return text

        handlers: dict[str, tuple[str, str | None]] = {
            "type": ("typing ", args.get("text")),
            "press_key": ("pressing key ", args.get("key")),
            "save_pdf": ("saving PDF to ", args.get("file_path")),
        }
        if action in handlers:
            label, value = handlers[action]
            text.append(label, style=info)
            if value:
                text.append(str(value), style=info)
            return text

        if action == "execute_js":
            text.append("executing javascript", style=info)
            js_code = args.get("js_code")
            if js_code:
                text.append("\n")
                text.append_text(cls._highlight_js(js_code))
            return text

        if action:
            text.append(action, style=info)
        return text
