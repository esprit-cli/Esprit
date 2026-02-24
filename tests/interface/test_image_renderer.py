"""Tests for the quarter-block / half-block image renderer."""

from __future__ import annotations

import base64
import io

import pytest
from rich.text import Text

from esprit.interface.image_renderer import (
    _avg_color,
    _best_quarter_block,
    _color_dist_sq,
    screenshot_to_rich_text,
)


# ---------------------------------------------------------------------------
# Test image helpers
# ---------------------------------------------------------------------------

def _make_solid_png_b64(color: tuple[int, int, int], size: tuple[int, int] = (4, 4)) -> str:
    """Create a solid-colour PNG and return its base64 encoding."""
    from PIL import Image

    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _make_checkerboard_png_b64(
    c1: tuple[int, int, int],
    c2: tuple[int, int, int],
    size: tuple[int, int] = (4, 4),
) -> str:
    """Create a 2-colour checkerboard PNG."""
    from PIL import Image

    img = Image.new("RGB", size)
    pixels = img.load()
    for y in range(size[1]):
        for x in range(size[0]):
            pixels[x, y] = c1 if (x + y) % 2 == 0 else c2
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestAvgColor:
    def test_single_pixel(self) -> None:
        assert _avg_color([(100, 150, 200)]) == (100, 150, 200)

    def test_two_pixels(self) -> None:
        assert _avg_color([(0, 0, 0), (100, 200, 50)]) == (50, 100, 25)

    def test_empty_list(self) -> None:
        assert _avg_color([]) == (0, 0, 0)

    def test_four_identical_pixels(self) -> None:
        px = (42, 84, 126)
        assert _avg_color([px, px, px, px]) == px


class TestColorDistSq:
    def test_identical_colors(self) -> None:
        assert _color_dist_sq((10, 20, 30), (10, 20, 30)) == 0

    def test_known_distance(self) -> None:
        # (0,0,0) vs (3,4,0) → 9 + 16 + 0 = 25
        assert _color_dist_sq((0, 0, 0), (3, 4, 0)) == 25

    def test_symmetric(self) -> None:
        a, b = (10, 20, 30), (40, 50, 60)
        assert _color_dist_sq(a, b) == _color_dist_sq(b, a)


# ---------------------------------------------------------------------------
# _best_quarter_block tests
# ---------------------------------------------------------------------------

class TestBestQuarterBlock:
    def test_solid_quad_returns_space(self) -> None:
        px = (128, 64, 32)
        ch, fg, bg = _best_quarter_block(px, px, px, px)
        assert ch == " "
        assert fg == bg == "#804020"

    def test_two_color_horizontal_split(self) -> None:
        """Top row one color, bottom row another → ▀ or ▄ (equivalent, swapped fg/bg)."""
        top = (255, 0, 0)
        bot = (0, 0, 255)
        ch, fg, bg = _best_quarter_block(top, top, bot, bot)
        # Both ▀ (fg=top, bg=bot) and ▄ (fg=bot, bg=top) have zero error
        assert ch in ("▀", "▄")
        assert {fg, bg} == {"#ff0000", "#0000ff"}

    def test_two_color_vertical_split(self) -> None:
        """Left col one color, right col another → ▌ or ▐ (equivalent, swapped fg/bg)."""
        left = (0, 255, 0)
        right = (255, 0, 0)
        ch, fg, bg = _best_quarter_block(left, right, left, right)
        assert ch in ("▌", "▐")
        assert {fg, bg} == {"#00ff00", "#ff0000"}

    def test_single_pixel_different(self) -> None:
        """Only top-left differs → ▘ or ▟ (complement, swapped fg/bg)."""
        a = (255, 255, 255)
        b = (0, 0, 0)
        ch, fg, bg = _best_quarter_block(a, b, b, b)
        # ▘ (fg=TL, bg=rest) and ▟ (fg=rest, bg=TL) are equivalent
        assert ch in ("▘", "▟")
        assert {fg, bg} == {"#ffffff", "#000000"}

    def test_returns_three_strings(self) -> None:
        result = _best_quarter_block((0, 0, 0), (255, 255, 255), (0, 0, 0), (255, 255, 255))
        assert len(result) == 3
        assert all(isinstance(s, str) for s in result)


# ---------------------------------------------------------------------------
# screenshot_to_rich_text integration tests
# ---------------------------------------------------------------------------

class TestQuarterBlockRendering:
    def test_solid_color_produces_text(self) -> None:
        b64 = _make_solid_png_b64((200, 100, 50))
        result = screenshot_to_rich_text(b64, max_width=10, mode="quarter")
        assert isinstance(result, Text)
        # Solid color renders as spaces — just verify we got non-empty content
        assert len(result.plain) > 0

    def test_solid_color_uses_spaces(self) -> None:
        """A uniform image should render as spaces (fast-path)."""
        b64 = _make_solid_png_b64((200, 100, 50), size=(20, 20))
        result = screenshot_to_rich_text(b64, max_width=10, mode="quarter")
        assert result is not None
        # After stripping margin whitespace, the block chars should be spaces
        chars = result.plain.replace("\n", "").strip()
        # Every character in the rendered body should be a space
        assert all(c == " " for c in chars)

    def test_checkerboard_produces_text(self) -> None:
        b64 = _make_checkerboard_png_b64((255, 0, 0), (0, 0, 255), size=(20, 20))
        result = screenshot_to_rich_text(b64, max_width=10, mode="quarter")
        assert isinstance(result, Text)
        assert len(result.plain.strip()) > 0

    def test_default_mode_is_quarter(self) -> None:
        b64 = _make_solid_png_b64((100, 100, 100))
        result = screenshot_to_rich_text(b64, max_width=10)
        assert result is not None


class TestHalfBlockRendering:
    def test_half_mode_produces_text(self) -> None:
        b64 = _make_solid_png_b64((200, 100, 50))
        result = screenshot_to_rich_text(b64, max_width=10, mode="half")
        assert isinstance(result, Text)
        assert len(result.plain.strip()) > 0

    def test_half_mode_uses_half_block_char(self) -> None:
        """Half-block mode should use ▀ characters."""
        b64 = _make_checkerboard_png_b64((255, 0, 0), (0, 0, 255), size=(20, 20))
        result = screenshot_to_rich_text(b64, max_width=10, mode="half")
        assert result is not None
        assert "▀" in result.plain
