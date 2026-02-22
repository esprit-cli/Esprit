"""Terminal image protocol detection and best-renderer selection.

This module MUST be imported before Textual takes over the terminal (i.e. at the
top of tui.py) so that:
  - ``textual_image.widget`` triggers its ``get_cell_size()`` query while
    stdout is still a real TTY.
  - ``textual_image.renderable`` evaluates ``sys.__stdout__.isatty()`` → True
    so it detects Kitty-TGP / Sixel correctly.

After that import, ``PROTOCOL`` and ``get_best_image_renderable()`` are stable
for the lifetime of the process.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image as PILImage
    from rich.console import RenderableType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Eager protocol detection
# ---------------------------------------------------------------------------
# Import textual_image.widget NOW (before Textual starts) to:
#   1. Run get_cell_size() terminal query
#   2. Let textual_image.renderable evaluate is_tty while stdout is a real TTY

PROTOCOL: str = "quarter"   # "tgp" | "sixel" | "halfcell" | "quarter"

_RENDERABLE_CLS = None

try:
    # This import triggers get_cell_size() and protocol detection
    import textual_image.widget  # noqa: F401 — side-effect import is intentional
    from textual_image.renderable import (
        Image as _AutoRenderable,
        TGPImage,
        SixelImage,
        HalfcellImage,
    )
    _RENDERABLE_CLS = _AutoRenderable

    if _AutoRenderable is TGPImage:
        PROTOCOL = "tgp"
    elif _AutoRenderable is SixelImage:
        PROTOCOL = "sixel"
    elif _AutoRenderable is HalfcellImage:
        PROTOCOL = "halfcell"
    else:
        # UnicodeImage — only 5 grayscale chars, worse than our quarter-block
        _RENDERABLE_CLS = None
        PROTOCOL = "quarter"

    logger.debug("Terminal image protocol: %s (%s)", PROTOCOL, _AutoRenderable)

except Exception:
    logger.debug("textual-image unavailable; using quarter-block fallback", exc_info=True)


def get_best_image_renderable(
    pil_img: "PILImage.Image",
    width: int,
) -> "RenderableType | None":
    """Return a Rich renderable for *pil_img* using the best protocol available.

    Returns ``None`` if only the quarter-block text renderer is available
    (caller should use :func:`~esprit.interface.image_renderer.screenshot_to_rich_text`
    directly in that case).
    """
    if _RENDERABLE_CLS is None:
        return None

    try:
        return _RENDERABLE_CLS(pil_img, width=width)
    except Exception:
        logger.debug("Protocol renderable failed, caller will fall back", exc_info=True)
        return None
