"""Convert base64 PNG screenshots to Rich Text using half-block terminal art."""

from __future__ import annotations

import base64
import io
import logging
import os

from rich.style import Style
from rich.text import Text

logger = logging.getLogger(__name__)

_PILLOW_AVAILABLE: bool | None = None

# Left panel (38) + right panel (40) + borders/padding (~6)
_TUI_SIDE_PANELS_WIDTH = 84
# Minimum left/right margin inside the chat area
_CHAT_AREA_PADDING = 6


def _check_pillow() -> bool:
    global _PILLOW_AVAILABLE  # noqa: PLW0603
    if _PILLOW_AVAILABLE is None:
        try:
            from PIL import Image  # noqa: F401

            _PILLOW_AVAILABLE = True
        except ImportError:
            _PILLOW_AVAILABLE = False
    return _PILLOW_AVAILABLE


def _get_available_width(max_width: int) -> int:
    """Determine the best preview width based on terminal size.

    If max_width is 0, auto-detect from terminal dimensions.
    Otherwise clamp to terminal width.
    """
    try:
        term_cols = os.get_terminal_size().columns
    except (ValueError, OSError):
        term_cols = 200  # generous fallback

    if max_width <= 0:
        # Auto: fill the chat panel area
        available = term_cols - _TUI_SIDE_PANELS_WIDTH - _CHAT_AREA_PADDING
        return max(40, available)

    return min(max_width, term_cols - 4)


def screenshot_to_rich_text(
    base64_png: str,
    max_width: int = 0,
    url_label: str = "",
) -> Text | None:
    """Convert a base64-encoded PNG screenshot to Rich Text using half-block characters.

    Each terminal character cell encodes two vertical pixels using the upper-half-block
    character (▀) with foreground color = top pixel, background color = bottom pixel.

    Args:
        base64_png: Base64-encoded PNG image data.
        max_width: Maximum width in terminal columns. 0 = auto-detect from terminal size.
        url_label: Optional URL to display as a dim header above the preview.

    Returns:
        A Rich Text object with the rendered preview, or None on failure.
    """
    if not _check_pillow():
        return None

    try:
        from PIL import Image, ImageFilter

        image_data = base64.b64decode(base64_png)
        img = Image.open(io.BytesIO(image_data))
        img = img.convert("RGB")

        target_w = _get_available_width(max_width)

        orig_w, orig_h = img.size
        scale = target_w / orig_w
        new_w = target_w
        new_h = int(orig_h * scale)
        # Ensure even height for pixel pairing
        if new_h % 2 != 0:
            new_h += 1

        # Multi-step downscale for sharper results when shrinking a lot
        # Halve dimensions progressively until within 2x of target, then final resize
        step_img = img
        step_w, step_h = orig_w, orig_h
        while step_w > new_w * 2.5 and step_h > new_h * 2.5:
            step_w = step_w // 2
            step_h = step_h // 2
            step_img = step_img.resize((step_w, step_h), Image.LANCZOS)

        # Final resize to exact target
        img = step_img.resize((new_w, new_h), Image.LANCZOS)

        # Sharpen after downscale to recover edge detail
        img = img.filter(ImageFilter.SHARPEN)

        pixels = img.load()

        text = Text()

        # Optional URL header
        if url_label:
            text.append("  🌐 ", style="dim")
            max_label = target_w - 6
            label = url_label if len(url_label) <= max_label else url_label[: max_label - 1] + "…"
            text.append(label, style="dim #06b6d4")
            text.append("\n")

        # Render pixel pairs as half-block characters
        for y in range(0, new_h, 2):
            text.append("  ")  # left margin
            for x in range(new_w):
                top_r, top_g, top_b = pixels[x, y]
                if y + 1 < new_h:
                    bot_r, bot_g, bot_b = pixels[x, y + 1]
                else:
                    bot_r, bot_g, bot_b = top_r, top_g, top_b

                fg = f"#{top_r:02x}{top_g:02x}{top_b:02x}"
                bg = f"#{bot_r:02x}{bot_g:02x}{bot_b:02x}"
                text.append("▀", style=Style(color=fg, bgcolor=bg))

            if y + 2 < new_h:
                text.append("\n")

        return text

    except Exception:
        logger.debug("Failed to render screenshot preview", exc_info=True)
        return None
