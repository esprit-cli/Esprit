from typing import Any, ClassVar

from rich.text import Text
from textual.widgets import Static

from esprit.interface.theme_tokens import get_marker_color, get_theme_tokens_from_tool_data

from .base_renderer import BaseToolRenderer
from .registry import register_tool_renderer


STATUS_MARKERS: dict[str, str] = {
    "pending": "[ ]",
    "in_progress": "[~]",
    "done": "[•]",
}


def _format_todo_lines(text: Text, result: dict[str, Any]) -> None:
    todos = result.get("todos")
    if not isinstance(todos, list) or not todos:
        text.append("\n  ")
        text.append("No todos", style="dim")
        return

    for todo in todos:
        status = todo.get("status", "pending")
        marker = STATUS_MARKERS.get(status, STATUS_MARKERS["pending"])

        title = todo.get("title", "").strip() or "(untitled)"

        text.append("\n  ")
        text.append(marker)
        text.append(" ")

        if status == "done":
            text.append(title, style="dim strike")
        elif status == "in_progress":
            text.append(title, style="italic")
        else:
            text.append(title)


def _theme_styles(tool_data: dict[str, Any]) -> tuple[str, str, str, str]:
    tokens = get_theme_tokens_from_tool_data(tool_data)
    todo_marker = get_marker_color(tokens, "todo")
    warning = str(tokens.get("warning", "#f59e0b"))
    error_color = str(tokens.get("error", "#ef4444"))
    muted = str(tokens.get("muted", "#9ca3af"))
    return todo_marker, warning, error_color, muted


@register_tool_renderer
class CreateTodoRenderer(BaseToolRenderer):
    tool_name: ClassVar[str] = "create_todo"
    css_classes: ClassVar[list[str]] = ["tool-call", "todo-tool"]

    @classmethod
    def render(cls, tool_data: dict[str, Any]) -> Static:
        result = tool_data.get("result")
        todo_marker, _warning, error_color, muted = _theme_styles(tool_data)

        text = Text()
        text.append("[todo] ", style=f"bold {todo_marker}")
        text.append("Todo", style=f"bold {todo_marker}")

        if isinstance(result, str) and result.strip():
            text.append("\n  ")
            text.append(result.strip(), style=f"dim {muted}")
        elif result and isinstance(result, dict):
            if result.get("success"):
                _format_todo_lines(text, result)
            else:
                error_msg = result.get("error", "Failed to create todo")
                text.append("\n  ")
                text.append(error_msg, style=error_color)
        else:
            text.append("\n  ")
            text.append("Creating...", style=f"dim {muted}")

        css_classes = cls.get_css_classes("completed")
        return Static(text, classes=css_classes)


@register_tool_renderer
class ListTodosRenderer(BaseToolRenderer):
    tool_name: ClassVar[str] = "list_todos"
    css_classes: ClassVar[list[str]] = ["tool-call", "todo-tool"]

    @classmethod
    def render(cls, tool_data: dict[str, Any]) -> Static:
        result = tool_data.get("result")
        todo_marker, _warning, error_color, muted = _theme_styles(tool_data)

        text = Text()
        text.append("[todo] ", style=f"bold {todo_marker}")
        text.append("Todos", style=f"bold {todo_marker}")

        if isinstance(result, str) and result.strip():
            text.append("\n  ")
            text.append(result.strip(), style=f"dim {muted}")
        elif result and isinstance(result, dict):
            if result.get("success"):
                _format_todo_lines(text, result)
            else:
                error_msg = result.get("error", "Unable to list todos")
                text.append("\n  ")
                text.append(error_msg, style=error_color)
        else:
            text.append("\n  ")
            text.append("Loading...", style=f"dim {muted}")

        css_classes = cls.get_css_classes("completed")
        return Static(text, classes=css_classes)


@register_tool_renderer
class UpdateTodoRenderer(BaseToolRenderer):
    tool_name: ClassVar[str] = "update_todo"
    css_classes: ClassVar[list[str]] = ["tool-call", "todo-tool"]

    @classmethod
    def render(cls, tool_data: dict[str, Any]) -> Static:
        result = tool_data.get("result")
        todo_marker, _warning, error_color, muted = _theme_styles(tool_data)

        text = Text()
        text.append("[todo] ", style=f"bold {todo_marker}")
        text.append("Todo Updated", style=f"bold {todo_marker}")

        if isinstance(result, str) and result.strip():
            text.append("\n  ")
            text.append(result.strip(), style=f"dim {muted}")
        elif result and isinstance(result, dict):
            if result.get("success"):
                _format_todo_lines(text, result)
            else:
                error_msg = result.get("error", "Failed to update todo")
                text.append("\n  ")
                text.append(error_msg, style=error_color)
        else:
            text.append("\n  ")
            text.append("Updating...", style=f"dim {muted}")

        css_classes = cls.get_css_classes("completed")
        return Static(text, classes=css_classes)


@register_tool_renderer
class MarkTodoDoneRenderer(BaseToolRenderer):
    tool_name: ClassVar[str] = "mark_todo_done"
    css_classes: ClassVar[list[str]] = ["tool-call", "todo-tool"]

    @classmethod
    def render(cls, tool_data: dict[str, Any]) -> Static:
        result = tool_data.get("result")
        todo_marker, _warning, error_color, muted = _theme_styles(tool_data)

        text = Text()
        text.append("[todo] ", style=f"bold {todo_marker}")
        text.append("Todo Completed", style=f"bold {todo_marker}")

        if isinstance(result, str) and result.strip():
            text.append("\n  ")
            text.append(result.strip(), style=f"dim {muted}")
        elif result and isinstance(result, dict):
            if result.get("success"):
                _format_todo_lines(text, result)
            else:
                error_msg = result.get("error", "Failed to mark todo done")
                text.append("\n  ")
                text.append(error_msg, style=error_color)
        else:
            text.append("\n  ")
            text.append("Marking done...", style=f"dim {muted}")

        css_classes = cls.get_css_classes("completed")
        return Static(text, classes=css_classes)


@register_tool_renderer
class MarkTodoPendingRenderer(BaseToolRenderer):
    tool_name: ClassVar[str] = "mark_todo_pending"
    css_classes: ClassVar[list[str]] = ["tool-call", "todo-tool"]

    @classmethod
    def render(cls, tool_data: dict[str, Any]) -> Static:
        result = tool_data.get("result")
        todo_marker, warning, error_color, muted = _theme_styles(tool_data)

        text = Text()
        text.append("[todo] ", style=f"bold {todo_marker}")
        text.append("Todo Reopened", style=f"bold {warning}")

        if isinstance(result, str) and result.strip():
            text.append("\n  ")
            text.append(result.strip(), style=f"dim {muted}")
        elif result and isinstance(result, dict):
            if result.get("success"):
                _format_todo_lines(text, result)
            else:
                error_msg = result.get("error", "Failed to reopen todo")
                text.append("\n  ")
                text.append(error_msg, style=error_color)
        else:
            text.append("\n  ")
            text.append("Reopening...", style=f"dim {muted}")

        css_classes = cls.get_css_classes("completed")
        return Static(text, classes=css_classes)


@register_tool_renderer
class DeleteTodoRenderer(BaseToolRenderer):
    tool_name: ClassVar[str] = "delete_todo"
    css_classes: ClassVar[list[str]] = ["tool-call", "todo-tool"]

    @classmethod
    def render(cls, tool_data: dict[str, Any]) -> Static:
        result = tool_data.get("result")
        todo_marker, _warning, error_color, muted = _theme_styles(tool_data)

        text = Text()
        text.append("[todo] ", style=f"bold {todo_marker}")
        text.append("Todo Removed", style=f"bold {todo_marker}")

        if isinstance(result, str) and result.strip():
            text.append("\n  ")
            text.append(result.strip(), style=f"dim {muted}")
        elif result and isinstance(result, dict):
            if result.get("success"):
                _format_todo_lines(text, result)
            else:
                error_msg = result.get("error", "Failed to remove todo")
                text.append("\n  ")
                text.append(error_msg, style=error_color)
        else:
            text.append("\n  ")
            text.append("Removing...", style=f"dim {muted}")

        css_classes = cls.get_css_classes("completed")
        return Static(text, classes=css_classes)
