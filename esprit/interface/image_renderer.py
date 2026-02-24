"""Convert base64 PNG screenshots to Rich renderables.

Rendering priority:
1. **Kitty TGP** (pixel-perfect) — Ghostty, kitty, WezTerm w/ TGP
2. **Sixel** (pixel-perfect) — xterm, foot, mlterm, many others
3. **Halfcell** (textual-image half-block) — any true-color terminal
4. **Quarter-block** (Unicode quadrant chars, 2×2 px/cell) — universal fallback
5. **Half-block** — legacy fallback, ``mode="half"``

The ``screenshot_to_rich_text`` function is the public API used by the whole
rendering pipeline.  It auto-selects the best available renderer.
"""

from __future__ import annotations

import base64
import io
import logging
import os
from collections.abc import Mapping

from rich.console import Group
from rich.style import Style
from rich.text import Text

from esprit.interface.theme_tokens import get_marker_color

logger = logging.getLogger(__name__)

_PILLOW_AVAILABLE: bool | None = None

# ---------------------------------------------------------------------------
# Quarter-block lookup table
# ---------------------------------------------------------------------------
# Each entry maps a 4-bit mask (TL, TR, BL, BR) to its Unicode quadrant char.
# 1 = foreground pixel, 0 = background pixel.
_QUARTER_BLOCKS: list[str] = [
    " ",  # 0b0000
    "▗",  # 0b0001
    "▖",  # 0b0010
    "▄",  # 0b0011
    "▝",  # 0b0100
    "▐",  # 0b0101
    "▞",  # 0b0110
    "▟",  # 0b0111
    "▘",  # 0b1000
    "▚",  # 0b1001
    "▌",  # 0b1010
    "▙",  # 0b1011
    "▀",  # 0b1100
    "▜",  # 0b1101
    "▛",  # 0b1110
    "█",  # 0b1111
]

# Pre-computed masks: for each of the 16 block chars, which of the 4 pixel
# positions belong to the foreground.  Index order: (TL, TR, BL, BR).
_QUARTER_MASKS: list[tuple[bool, bool, bool, bool]] = [
    tuple(bool(i & (1 << (3 - b))) for b in range(4))  # type: ignore[misc]
    for i in range(16)
]


def _avg_color(pixels: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    """Return the average RGB color of *pixels*."""
    n = len(pixels)
    if n == 0:
        return (0, 0, 0)
    r = sum(p[0] for p in pixels) // n
    g = sum(p[1] for p in pixels) // n
    b = sum(p[2] for p in pixels) // n
    return (r, g, b)


def _color_dist_sq(
    a: tuple[int, int, int], b: tuple[int, int, int]
) -> int:
    """Squared Euclidean distance between two RGB colors."""
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def _best_quarter_block(
    tl: tuple[int, int, int],
    tr: tuple[int, int, int],
    bl: tuple[int, int, int],
    br: tuple[int, int, int],
) -> tuple[str, str, str]:
    """Choose the best quarter-block char and fg/bg colors for a 2x2 pixel quad.

    Returns ``(char, fg_hex, bg_hex)``.
    """
    quad = (tl, tr, bl, br)

    # Fast path: all four pixels identical → space with bg color
    if tl == tr == bl == br:
        h = f"#{tl[0]:02x}{tl[1]:02x}{tl[2]:02x}"
        return (" ", h, h)

    best_err = float("inf")
    best_char = " "
    best_fg: tuple[int, int, int] = (0, 0, 0)
    best_bg: tuple[int, int, int] = (0, 0, 0)

    for idx in range(16):
        mask = _QUARTER_MASKS[idx]
        fg_pixels = [quad[j] for j in range(4) if mask[j]]
        bg_pixels = [quad[j] for j in range(4) if not mask[j]]

        fg_c = _avg_color(fg_pixels) if fg_pixels else (0, 0, 0)
        bg_c = _avg_color(bg_pixels) if bg_pixels else (0, 0, 0)

        err = 0
        for j in range(4):
            ref = fg_c if mask[j] else bg_c
            err += _color_dist_sq(quad[j], ref)
            if err >= best_err:
                break
        else:
            # Only update best if we didn't break early
            best_err = err
            best_char = _QUARTER_BLOCKS[idx]
            best_fg = fg_c
            best_bg = bg_c
            if err == 0:
                break

    fg_hex = f"#{best_fg[0]:02x}{best_fg[1]:02x}{best_fg[2]:02x}"
    bg_hex = f"#{best_bg[0]:02x}{best_bg[1]:02x}{best_bg[2]:02x}"
    return (best_char, fg_hex, bg_hex)

# Panel widths from tui_styles.tcss
_LEFT_PANEL_WIDTH = 38
_RIGHT_PANEL_WIDTH = 40
_PANEL_BORDER_OVERHEAD = 4  # border characters between panels
# Layout breakpoints (from tui.py)
_THREE_PANE_MIN = 170
_LEFT_ONLY_MIN = 120
# Small margin for the 2-char left indent we prepend to each line
_RENDER_MARGIN = 4


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

    If max_width is 0, auto-detect based on terminal dimensions and the
    responsive TUI layout (which hides panels on narrow terminals).
    Otherwise clamp to terminal width.
    """
    try:
        term_cols = os.get_terminal_size().columns
    except (ValueError, OSError):
        term_cols = 200  # generous fallback

    if max_width <= 0:
        # Mirror the responsive layout logic from tui.py
        if term_cols >= _THREE_PANE_MIN:
            overhead = _LEFT_PANEL_WIDTH + _RIGHT_PANEL_WIDTH + _PANEL_BORDER_OVERHEAD
        elif term_cols >= _LEFT_ONLY_MIN:
            overhead = _LEFT_PANEL_WIDTH + _PANEL_BORDER_OVERHEAD
        else:
            overhead = 0
        available = term_cols - overhead - _RENDER_MARGIN
        return max(40, available)

    return min(max_width, term_cols - 4)


def _make_url_header(
    url_label: str,
    target_w: int,
    theme_tokens: Mapping[str, str] | None,
) -> Text:
    """Build the dim URL header line shown above an image preview."""
    info = str(theme_tokens.get("info", "#06b6d4")) if theme_tokens else "#06b6d4"
    web_marker = (
        get_marker_color(theme_tokens, "web") if theme_tokens else "#06b6d4"
    )
    header = Text()
    header.append("  [web] ", style=f"bold {web_marker}")
    max_label = max(8, target_w - 9)
    label = url_label if len(url_label) <= max_label else url_label[: max_label - 1] + "…"
    header.append(label, style=f"dim {info}")
    return header


def screenshot_to_rich_text(
    base64_png: str,
    max_width: int = 0,
    url_label: str = "",
    theme_tokens: Mapping[str, str] | None = None,
    mode: str = "quarter",
):
    """Convert a base64-encoded PNG screenshot to the best available Rich renderable.

    Rendering priority (auto-selected):
    1. Kitty TGP — pixel-perfect on Ghostty, kitty, WezTerm
    2. Sixel — pixel-perfect on xterm, foot, mlterm, etc.
    3. Halfcell — textual-image half-block (better than our block chars)
    4. Quarter-block — pure-unicode 2×2-pixel-per-cell fallback
    5. Half-block — legacy fallback when ``mode="half"``

    Args:
        base64_png: Base64-encoded PNG image data.
        max_width: Maximum width in terminal columns. 0 = auto-detect from terminal size.
        url_label: Optional URL to display as a dim header above the preview.
        theme_tokens: Optional theme colour mapping.
        mode: Ignored when a graphics protocol is available.  For pure-unicode
              fallback: ``"quarter"`` (default) or ``"half"``.

    Returns:
        A Rich renderable (Text, Group, or protocol image) or None on failure.
    """
    if not _check_pillow():
        return None

    try:
        from PIL import Image

        # Guard against absurdly large payloads (~50 MB base64 ≈ ~37 MB raw)
        if len(base64_png) > 50_000_000:
            logger.debug("Screenshot base64 too large (%d bytes), skipping", len(base64_png))
            return None

        image_data = base64.b64decode(base64_png)
        img = Image.open(io.BytesIO(image_data))
        img = img.convert("RGB")

        target_w = _get_available_width(max_width)

        orig_w, orig_h = img.size
        if orig_w == 0 or orig_h == 0:
            return None

        # ------------------------------------------------------------------
        # Tier 1-3: Try a pixel-accurate graphics protocol (TGP / Sixel /
        # Halfcell) via textual-image.  Only available when image_protocol
        # was imported before Textual started (done at the top of tui.py).
        # ------------------------------------------------------------------
        try:
            from esprit.interface.image_protocol import get_best_image_renderable, PROTOCOL
            if PROTOCOL != "quarter":
                proto_renderable = get_best_image_renderable(img, width=target_w)
                if proto_renderable is not None:
                    if url_label:
                        header = _make_url_header(url_label, target_w, theme_tokens)
                        return Group(header, proto_renderable)
                    return proto_renderable
        except Exception:
            pass  # fall through to block-char renderer

        use_quarter = mode == "quarter"

        # Quarter-block: each cell covers a 2×2 pixel block, so we need
        # 2× the horizontal pixels to fill the same terminal width.
        pixel_w = target_w * 2 if use_quarter else target_w
        scale = pixel_w / orig_w
        new_w = pixel_w
        new_h = int(orig_h * scale)

        if use_quarter:
            # Terminal cells are ~2:1 (height ≈ 2× width).  In quarter mode
            # each cell packs 2×2 pixels, so each pixel spans (cell_w/2) wide
            # by (cell_h/2) ≈ cell_w tall — giving a 1:2 pixel aspect ratio.
            # Halve the pixel height to compensate and restore correct AR.
            new_h = max(2, new_h // 2)

        # Ensure even height for pixel pairing (both modes use row pairs)
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

        # Final resize to exact target.
        # LANCZOS gives the best quality for downscaling.  We intentionally
        # skip post-resize sharpening: the quarter-block algorithm already
        # selects the optimal 2-color partition per cell, and sharpening
        # before that quantisation step creates ringing / halo artefacts
        # that look much worse than the slight softness it tries to fix.
        img = step_img.resize((new_w, new_h), Image.LANCZOS)

        pixels = img.load()

        text = Text()

        # Optional URL header
        if url_label:
            text.append_text(_make_url_header(url_label, target_w, theme_tokens))
            text.append("\n")

        # Render pixel grid as block characters
        if use_quarter:
            # Quarter-block: 2x2 pixel block per terminal cell
            for y in range(0, new_h, 2):
                text.append("  ")  # left margin
                for x in range(0, new_w, 2):
                    tl = pixels[x, y]
                    tr = pixels[x + 1, y] if x + 1 < new_w else tl
                    bl = pixels[x, y + 1] if y + 1 < new_h else tl
                    br = (
                        pixels[x + 1, y + 1]
                        if x + 1 < new_w and y + 1 < new_h
                        else bl
                    )
                    ch, fg_hex, bg_hex = _best_quarter_block(tl, tr, bl, br)
                    text.append(ch, style=Style(color=fg_hex, bgcolor=bg_hex))
                if y + 2 < new_h:
                    text.append("\n")
        else:
            # Half-block: 1x2 pixel pair per terminal cell
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
