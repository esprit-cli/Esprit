from __future__ import annotations

from rich.console import Console

from esprit.interface.tool_components.browser_renderer import BrowserRenderer
from esprit.interface.tool_components.reporting_renderer import CreateVulnerabilityReportRenderer
from esprit.interface.tool_components.thinking_renderer import ThinkRenderer
from esprit.interface.tool_components.todo_renderer import CreateTodoRenderer
from esprit.interface.tool_components.web_search_renderer import WebSearchRenderer


def _plain_text(renderable: object) -> str:
    console = Console(width=160, record=True)
    console.print(renderable)
    return console.export_text()


def test_reporting_renderer_uses_bug_tag() -> None:
    widget = CreateVulnerabilityReportRenderer.render(
        {
            "args": {"title": "SQL Injection"},
            "result": {"severity": "high", "cvss_score": 8.2},
        }
    )
    plain = _plain_text(widget.renderable)
    assert "[bug]" in plain
    assert "Vulnerability Report" in plain


def test_todo_renderer_uses_todo_tag() -> None:
    widget = CreateTodoRenderer.render(
        {
            "result": {
                "success": True,
                "todos": [{"status": "pending", "title": "Validate scope"}],
            }
        }
    )
    plain = _plain_text(widget.renderable)
    assert "[todo]" in plain
    assert "Todo" in plain


def test_web_and_think_renderers_use_ascii_tags() -> None:
    web_widget = WebSearchRenderer.render({"args": {"query": "oauth misconfig"}})
    think_widget = ThinkRenderer.render({"args": {"thought": "Need broader endpoint coverage"}})
    web_plain = _plain_text(web_widget.renderable)
    think_plain = _plain_text(think_widget.renderable)
    assert "[web]" in web_plain
    assert "[think]" in think_plain
    assert web_plain.strip()
    assert think_plain.strip()


def test_browser_renderer_uses_web_tag() -> None:
    widget = BrowserRenderer.render({"args": {"action": "goto", "url": "https://example.com"}})
    plain = _plain_text(widget.renderable)
    assert "[web]" in plain
