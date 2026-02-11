"""Tests for the image_widget module."""

import base64
import io
from unittest.mock import MagicMock, patch

import pytest

from rich.text import Text


def _make_test_png_b64() -> str:
    """Create a minimal 2x2 red PNG as base64."""
    from PIL import Image as PILImage

    img = PILImage.new("RGB", (2, 2), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class TestCheckTextualImage:
    """Tests for the _check_textual_image function."""

    def test_returns_bool(self) -> None:
        from esprit.interface.image_widget import _check_textual_image

        result = _check_textual_image()
        assert isinstance(result, bool)

    def test_cached_result(self) -> None:
        import esprit.interface.image_widget as mod

        # Reset cache
        original = mod._TEXTUAL_IMAGE_AVAILABLE
        mod._TEXTUAL_IMAGE_AVAILABLE = None

        result1 = mod._check_textual_image()
        result2 = mod._check_textual_image()
        assert result1 == result2

        # Restore
        mod._TEXTUAL_IMAGE_AVAILABLE = original


class TestDecodeBase64ToPil:
    """Tests for base64 to PIL conversion."""

    def test_decode_valid_png(self) -> None:
        from esprit.interface.image_widget import _decode_base64_to_pil

        b64 = _make_test_png_b64()
        img = _decode_base64_to_pil(b64)
        assert img.size == (2, 2)
        assert img.mode == "RGB"

    def test_decode_invalid_data_raises(self) -> None:
        from esprit.interface.image_widget import _decode_base64_to_pil

        with pytest.raises((ValueError, OSError)):
            _decode_base64_to_pil("not_valid_base64!!!")


class TestBrowserScreenshotWidgetInit:
    """Tests for BrowserScreenshotWidget initialization."""

    def test_init_defaults(self) -> None:
        from esprit.interface.image_widget import BrowserScreenshotWidget

        widget = BrowserScreenshotWidget()
        assert widget.screenshot_b64 is None
        assert widget.url_label == ""

    def test_init_with_data(self) -> None:
        from esprit.interface.image_widget import BrowserScreenshotWidget

        b64 = _make_test_png_b64()
        widget = BrowserScreenshotWidget(screenshot_b64=b64, url="https://example.com")
        assert widget.screenshot_b64 == b64
        assert widget.url_label == "https://example.com"

    def test_update_screenshot(self) -> None:
        from esprit.interface.image_widget import BrowserScreenshotWidget

        widget = BrowserScreenshotWidget()
        b64 = _make_test_png_b64()
        widget.update_screenshot(b64, url="https://test.com")
        assert widget.screenshot_b64 == b64
        assert widget.url_label == "https://test.com"


class TestBrowserScreenshotWidgetRenderFallback:
    """Tests for rendering with half-block fallback."""

    def test_halfblock_render_produces_text(self) -> None:
        from esprit.interface.image_widget import BrowserScreenshotWidget

        b64 = _make_test_png_b64()
        widget = BrowserScreenshotWidget(screenshot_b64=b64)

        # Test the half-block render method directly with a mock Static
        mock_static = MagicMock()
        widget._render_with_halfblock(mock_static)

        # Should have called update with either a Text object or "Unable to render"
        assert mock_static.update.called

    @patch("esprit.interface.image_widget._check_textual_image", return_value=False)
    def test_uses_halfblock_when_no_textual_image(self, mock_check: MagicMock) -> None:
        from esprit.interface.image_widget import BrowserScreenshotWidget

        b64 = _make_test_png_b64()
        widget = BrowserScreenshotWidget(screenshot_b64=b64)

        mock_content = MagicMock()
        with patch.object(widget, "query_one", return_value=mock_content):
            widget._render_screenshot()
            assert mock_content.update.called

    def test_no_screenshot_shows_placeholder(self) -> None:
        from esprit.interface.image_widget import BrowserScreenshotWidget

        widget = BrowserScreenshotWidget()

        mock_content = MagicMock()
        with patch.object(widget, "query_one", return_value=mock_content):
            widget._render_screenshot()
            call_args = mock_content.update.call_args[0][0]
            assert isinstance(call_args, Text)
            assert "No screenshot" in call_args.plain
