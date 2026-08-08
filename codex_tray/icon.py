"""Generate the compact image shown in the Windows notification area."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .presentation import format_tray_title


GREEN = "#107c10"
AMBER = "#ffb900"
RED = "#d13438"
GRAY = "#605e5c"
PURPLE = "#5c2d91"


def set_windows_dpi_awareness() -> bool:
    """Opt into per-monitor DPI scaling when the process runs on Windows.

    The calls are best-effort so the same module remains importable and
    testable on non-Windows hosts. The notification icon itself is still
    generated at a high-resolution 64×64 source size for Windows scaling.
    """

    if os.name != "nt":
        return False
    try:
        # Windows 10 1703+: per-monitor v2. The signed constant is accepted
        # by SetProcessDpiAwarenessContext even though ctypes receives an int.
        if hasattr(ctypes.windll.user32, "SetProcessDpiAwarenessContext"):
            return bool(ctypes.windll.user32.SetProcessDpiAwarenessContext(-4))
    except (AttributeError, OSError):
        pass
    try:
        # Windows 8.1 fallback.
        return ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0
    except (AttributeError, OSError):
        return False


def color_for_remaining(snapshot: Any) -> str:
    if getattr(snapshot, "unlimited", None) is True:
        return PURPLE
    remaining = getattr(snapshot, "remaining_percent", None)
    if remaining is None:
        remaining = getattr(snapshot, "remaining", None)
    if remaining is None:
        remaining = getattr(snapshot, "balance", None)
    if isinstance(remaining, bool):
        remaining = None
    if remaining is None:
        return GRAY
    try:
        remaining = float(remaining)
    except (TypeError, ValueError):
        return GRAY
    if not 0 <= remaining <= 100:
        return GRAY
    if remaining <= 20:
        return RED
    if remaining <= 50:
        return AMBER
    return GREEN


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def create_icon_image(snapshot: Any, size: int = 64, *, status: str | None = None) -> Image.Image:
    """Create a square RGBA icon with the current remaining percentage."""

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = max(2, size // 16)
    draw.rounded_rectangle(
        (margin, margin, size - margin - 1, size - margin - 1),
        radius=max(4, size // 8),
        fill=color_for_remaining(snapshot),
    )

    label = format_tray_title(snapshot, status=status)
    font_size = max(12, int(size * (0.34 if len(label) > 2 else 0.42)))
    font = _font(font_size)
    bounds = draw.textbbox((0, 0), label, font=font)
    text_width = bounds[2] - bounds[0]
    text_height = bounds[3] - bounds[1]
    position = ((size - text_width) / 2 - bounds[0], (size - text_height) / 2 - bounds[1])
    draw.text(position, label, fill="white", font=font)
    return image
