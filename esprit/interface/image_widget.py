"""Smart browser screenshot widget with textual-image fallback chain."""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

from rich.text import Text
from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


logger = logging.getLogger(__name__)

_TEXTUAL_IMAGE_AVAILABLE: bool | None = None


def _check_textual_image() -> bool:
    """Check if textual-image is installed and usable."""
    global _TEXTUAL_IMAGE_AVAILABLE  # noqa: PLW0603
    if _TEXTUAL_IMAGE_AVAILABLE is None:
        try:
            from textual_image.widget import Image  # noqa: F401

            _TEXTUAL_IMAGE_AVAILABLE = True
        except ImportError:
            _TEXTUAL_IMAGE_AVAILABLE = False
    return _TEXTUAL_IMAGE_AVAILABLE


def _decode_base64_to_pil(b64_data: str) -> Any:
    """Decode a base64 PNG string to a PIL Image."""
    from PIL import Image as PILImage

    image_data = base64.b64decode(b64_data)
    return PILImage.open(io.BytesIO(image_data))


class BrowserScreenshotWidget(Widget):  # type: ignore[misc]
    """Renders a browser screenshot using the best available method.

    Priority:
      1. textual-image (Kitty TGP / Sixel / halfblock) — pixel-perfect on supported terminals
      2. Half-block renderer from image_renderer.py — works everywhere
    """

    screenshot_b64: reactive[str | None] = reactive(default=None)
    url_label: reactive[str] = reactive(default="")

    def __init__(
        self, screenshot_b64: str | None = None, url: str = "", **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.screenshot_b64 = screenshot_b64
        self.url_label = url

    def compose(self) -> Any:
        yield VerticalScroll(Static("", id="screenshot_content"), id="screenshot_scroll")

    def on_mount(self) -> None:
        self._render_screenshot()

    def watch_screenshot_b64(self, _old: str | None, _new: str | None) -> None:
        if self.is_mounted:
            self._render_screenshot()

    def update_screenshot(self, new_b64: str | None, url: str = "") -> None:
        """Update the displayed screenshot."""
        self.url_label = url
        self.screenshot_b64 = new_b64

    def _render_screenshot(self) -> None:
        """Render the screenshot using the best available method."""
        try:
            content_widget = self.query_one("#screenshot_content", Static)
        except Exception:  # noqa: BLE001
            # Recreate the content widget if it was removed (e.g. by textual-image)
            try:
                scroll = self.query_one("#screenshot_scroll", VerticalScroll)
                content_widget = Static("", id="screenshot_content")
                scroll.mount(content_widget)
            except Exception:  # noqa: BLE001
                return

        if not self.screenshot_b64:
            content_widget.update(Text("No screenshot available", style="dim"))
            return

        if _check_textual_image():
            self._render_with_textual_image(content_widget)
        else:
            self._render_with_halfblock(content_widget)

    def _render_with_textual_image(self, content_widget: Static) -> None:
        """Render using textual-image for pixel-perfect quality."""
        try:
            from textual_image.widget import Image

            # Decode first — only remove children if decode succeeds
            pil_img = _decode_base64_to_pil(self.screenshot_b64)  # type: ignore[arg-type]

            # Remove old content and mount the Image widget
            scroll = self.query_one("#screenshot_scroll", VerticalScroll)
            for child in list(scroll.children):
                child.remove()

            # Create the Image widget
            img_widget = Image(pil_img)
            scroll.mount(img_widget)

            # Add URL label above if present
            if self.url_label:
                url_static = Static(
                    Text.assemble(
                        ("  \U0001f310 ", "dim"),
                        (self.url_label, "dim #06b6d4"),
                    )
                )
                scroll.mount(url_static, before=0)

        except Exception:  # noqa: BLE001
            logger.debug("textual-image rendering failed, falling back to halfblock", exc_info=True)
            # content_widget may have been removed; re-query or recreate
            try:
                content_widget = self.query_one("#screenshot_content", Static)
            except Exception:  # noqa: BLE001
                try:
                    scroll = self.query_one("#screenshot_scroll", VerticalScroll)
                    content_widget = Static("", id="screenshot_content")
                    scroll.mount(content_widget)
                except Exception:  # noqa: BLE001
                    return
            self._render_with_halfblock(content_widget)

    def _render_with_halfblock(self, content_widget: Static) -> None:
        """Render using the half-block character technique."""
        try:
            from esprit.interface.image_renderer import screenshot_to_rich_text

            result = screenshot_to_rich_text(
                self.screenshot_b64, max_width=0, url_label=self.url_label  # type: ignore[arg-type]
            )
            if result is not None:
                content_widget.update(result)
                return
        except Exception:  # noqa: BLE001
            logger.debug("Half-block rendering failed", exc_info=True)

        content_widget.update(Text("Unable to render browser preview", style="dim"))
