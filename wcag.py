"""wcag.py — Exact WCAG 2.2 contrast math and color utilities.

This module contains NO heuristics. Every function here is deterministic
mathematics straight from the WCAG 2.2 specification:

    L_rel = 0.2126 * R + 0.7152 * G + 0.0722 * B
    where R, G, B are linearized sRGB channel values.

    contrast ratio = (L1 + 0.05) / (L2 + 0.05),  L1 >= L2

Thresholds:
    AA normal text : 4.5
    AA large text  : 3.0
    AAA normal text: 7.0
    AAA large text : 4.5

"Large text" per WCAG is >= 18pt (24px) regular or >= 14pt (18.66px) bold.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# WCAG thresholds (constants, never configurable)
# ---------------------------------------------------------------------------

AA_NORMAL = 4.5
AA_LARGE = 3.0
AAA_NORMAL = 7.0
AAA_LARGE = 4.5

# Named CSS colors resolved to hex. Kept deliberately small (~20 most common).
NAMED_COLORS = {
    "black": "#000000",
    "white": "#FFFFFF",
    "red": "#FF0000",
    "green": "#008000",
    "lime": "#00FF00",
    "blue": "#0000FF",
    "yellow": "#FFFF00",
    "orange": "#FFA500",
    "purple": "#800080",
    "gray": "#808080",
    "grey": "#808080",
    "silver": "#C0C0C0",
    "navy": "#000080",
    "teal": "#008080",
    "olive": "#808000",
    "maroon": "#800000",
    "aqua": "#00FFFF",
    "cyan": "#00FFFF",
    "fuchsia": "#FF00FF",
    "magenta": "#FF00FF",
    "darkgray": "#A9A9A9",
    "darkgrey": "#A9A9A9",
    "lightgray": "#D3D3D3",
    "lightgrey": "#D3D3D3",
    "whitesmoke": "#F5F5F5",
}

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_hex(value: str) -> tuple[int, int, int]:
    """Parse a hex color string into an (r, g, b) tuple of ints 0-255.

    Accepts '#RRGGBB', 'RRGGBB', '#RGB' and 'RGB' (3-digit form is expanded
    by duplicating each digit, per CSS spec). Also accepts the ~20 common
    CSS color names in NAMED_COLORS.

    Raises ValueError with a helpful message on anything else.
    """
    if not isinstance(value, str):
        raise ValueError(f"Not a color string: {value!r}")
    s = value.strip().lower()
    if s in NAMED_COLORS:
        s = NAMED_COLORS[s].lstrip("#").lower()
    else:
        s = s.lstrip("#")
    if len(s) not in (3, 6) or not all(c in "0123456789abcdef" for c in s):
        raise ValueError(f"Invalid hex color: {value!r}")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def to_hex(rgb: tuple[int, int, int]) -> str:
    """Format an (r, g, b) tuple as uppercase '#RRGGBB'."""
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def normalize_hex(value: str) -> str:
    """Parse then re-format, so '#abc' -> '#AABBCC' and 'white' -> '#FFFFFF'."""
    return to_hex(parse_hex(value))


# ---------------------------------------------------------------------------
# Luminance and contrast — the exact WCAG 2.2 math
# ---------------------------------------------------------------------------

def _linearize(channel: int) -> float:
    """sRGB linearization of a single 0-255 channel value."""
    c = channel / 255.0
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG 2.2 relative luminance. #000000 -> 0.0, #FFFFFF -> 1.0."""
    r, g, b = rgb
    return 0.2126 * _linearize(r) + 0.7152 * _linearize(g) + 0.0722 * _linearize(b)


def luminance_hex(hex_value: str) -> float:
    return relative_luminance(parse_hex(hex_value))


def contrast_ratio(fg: str | tuple[int, int, int], bg: str | tuple[int, int, int]) -> float:
    """Contrast ratio between two colors, rounded to 2 decimals.

    Ratio is symmetric: order of arguments does not matter.
    """
    rgb1 = fg if isinstance(fg, tuple) else parse_hex(fg)
    rgb2 = bg if isinstance(bg, tuple) else parse_hex(bg)
    l1 = relative_luminance(rgb1)
    l2 = relative_luminance(rgb2)
    lighter, darker = max(l1, l2), min(l1, l2)
    if darker >= 1.0:
        return 1.0
    ratio = (lighter + 0.05) / (darker + 0.05)
    return round(ratio, 2)


# ---------------------------------------------------------------------------
# Pass / fail helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConformanceResult:
    level: str          # "AA" or "AAA"
    text_size: str      # "normal" or "large"
    threshold: float
    ratio: float
    passed: bool

    def as_row(self) -> dict:
        return {
            "Level": self.level,
            "Text size": self.text_size,
            "Threshold": self.threshold,
            "Contrast ratio": self.ratio,
            "Result": "PASS" if self.passed else "FAIL",
        }


def passes(level: str, text_size: str, ratio: float) -> bool:
    """True if the ratio meets the given level/size threshold."""
    thresholds = {
        ("AA", "normal"): AA_NORMAL,
        ("AA", "large"): AA_LARGE,
        ("AAA", "normal"): AAA_NORMAL,
        ("AAA", "large"): AAA_LARGE,
    }
    try:
        return ratio >= thresholds[(level.upper(), text_size.lower())]
    except KeyError:
        raise ValueError(f"Unknown level/size: {level}/{text_size}") from None


def evaluate(fg: str, bg: str) -> list[ConformanceResult]:
    """Evaluate a fg/bg pair against all four WCAG thresholds."""
    ratio = contrast_ratio(fg, bg)
    return [
        ConformanceResult(lvl, size, thr, ratio, passes(lvl, size, ratio))
        for (lvl, size), thr in sorted(
            {
                ("AA", "normal"): AA_NORMAL,
                ("AA", "large"): AA_LARGE,
                ("AAA", "large"): AAA_LARGE,
                ("AAA", "normal"): AAA_NORMAL,
            }.items()
        )
    ]


# ---------------------------------------------------------------------------
# Fix suggestion: lighten / darken until the pair passes 4.5 (AA normal)
# ---------------------------------------------------------------------------

def _mix(rgb: tuple[int, int, int], target: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    """Linear interpolation between rgb and target; amount in [0, 1]."""
    amount = max(0.0, min(1.0, amount))
    return tuple(
        int(round(a + (b - a) * amount)) for a, b in zip(rgb, target)
    )


def lighten_until_pass(
    fg: str,
    bg: str,
    threshold: float = AA_NORMAL,
    max_steps: int = 40,
) -> tuple[str, float, int] | None:
    """Blend the foreground toward white until it passes `threshold` on `bg`.

    Returns (suggested_hex, achieved_ratio, blend_percent) or None if even
    pure white does not pass.
    """
    return _adjust_until_pass(fg, bg, (255, 255, 255), threshold, max_steps)


def darken_until_pass(
    fg: str,
    bg: str,
    threshold: float = AA_NORMAL,
    max_steps: int = 40,
) -> tuple[str, float, int] | None:
    """Blend the foreground toward black until it passes `threshold` on `bg`.

    Returns (suggested_hex, achieved_ratio, blend_percent) or None if even
    pure black does not pass.
    """
    return _adjust_until_pass(fg, bg, (0, 0, 0), threshold, max_steps)


def suggest_fix(fg: str, bg: str, threshold: float = AA_NORMAL) -> tuple[str, float, int] | None:
    """Try both directions (toward white and toward black) and return the
    smaller adjustment that reaches `threshold`. None if impossible."""
    if contrast_ratio(fg, bg) >= threshold:
        return normalize_hex(fg), contrast_ratio(fg, bg), 0
    options = []
    white = _adjust_until_pass(fg, bg, (255, 255, 255), threshold, 40)
    if white:
        options.append(white)
    black = _adjust_until_pass(fg, bg, (0, 0, 0), threshold, 40)
    if black:
        options.append(black)
    if not options:
        return None
    return min(options, key=lambda o: o[2])


def _adjust_until_pass(
    fg: str,
    bg: str,
    target: tuple[int, int, int],
    threshold: float,
    max_steps: int,
) -> tuple[str, float, int] | None:
    rgb = parse_hex(fg)
    for step in range(1, max_steps + 1):
        amount = step / max_steps
        candidate = _mix(rgb, target, amount)
        ratio = contrast_ratio(candidate, parse_hex(bg))
        if ratio >= threshold:
            return to_hex(candidate), ratio, int(round(amount * 100))
    return None
