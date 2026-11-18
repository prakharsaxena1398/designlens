"""svgparse.py — Extract colors, text elements and canvas geometry from SVG.

Parses an SVG document (string or bytes) with xml.etree and collects:

  * fill / stroke / stop-color / color presentation attributes
  * the same properties inside inline `style="..."` declarations
  * fill="url(#gradient)" references are IGNORED (not resolvable to a hex)
  * named CSS colors resolved through wcag.NAMED_COLORS
  * unique palette: {normalized_hex: element_count}

Also reports:
  * text element count (any element whose tag localname is "text")
  * canvas width/height from width/height attributes or viewBox
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from wcag import NAMED_COLORS, normalize_hex

# Properties that carry paint in SVG.
_PAINT_PROPS = ("fill", "stroke", "stop-color", "color")

_NONE = {"none", "transparent", "inherit", "currentcolor", "currentColor"}

_URL_RE = re.compile(r"url\(")


def _localname(tag: str) -> str:
    """Strip any XML namespace: '{http://www.w3.org/2000/svg}text' -> 'text'."""
    if isinstance(tag, str) and "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _resolve_paint(raw: str) -> str | None:
    """Return a normalized hex for a paint value, or None if not a solid color."""
    if raw is None:
        return None
    value = raw.strip().strip("'\"").lower()
    if not value or value in _NONE or _URL_RE.search(value):
        return None
    if value.startswith("#"):
        try:
            return normalize_hex("#" + value.lstrip("#"))
        except ValueError:
            return None
    if value in NAMED_COLORS:
        return normalize_hex(value)
    return None


def _iter_elements(root: ET.Element):
    """Yield every element in the tree, including the root."""
    yield root
    for parent in root.iter():
        for child in parent:
            yield child


def parse_svg(source: str | bytes) -> dict:
    """Parse an SVG and return a structured report.

    Returns dict with keys:
      canvas       : {"width": int|None, "height": int|None, "viewbox": str|None}
      palette      : dict  normalized_hex -> use count (all elements)
      text_colors  : dict  normalized_hex -> count (paint of <text> elements)
      shape_colors : dict  normalized_hex -> count (paint of non-text elements)
      text_count   : int
      errors       : list of human-readable parse problems (never raises on
                     well-formed-but-odd SVGs; raises ET.ParseError only if the
                     document is not XML at all).
    """
    root = ET.fromstring(source if isinstance(source, bytes) else source.encode("utf-8"))

    palette: dict[str, int] = {}
    text_colors: dict[str, int] = {}
    shape_colors: dict[str, int] = {}
    text_count = 0
    style_attr_re = re.compile(r"([^:;]+)\s*:\s*([^;]+)")

    for el in _iter_elements(root):
        tag = _localname(el.tag)
        is_text = tag == "text"
        if is_text:
            text_count += 1

        def record(hex_color: str) -> None:
            palette[hex_color] = palette.get(hex_color, 0) + 1
            bucket = text_colors if is_text else shape_colors
            bucket[hex_color] = bucket.get(hex_color, 0) + 1

        # presentation attributes
        for prop in _PAINT_PROPS:
            hex_color = _resolve_paint(el.attrib.get(prop))
            if hex_color:
                record(hex_color)

        # inline style attribute
        style = el.attrib.get("style")
        if style:
            for prop, value in style_attr_re.findall(style):
                prop = prop.strip().lower()
                if prop in _PAINT_PROPS:
                    hex_color = _resolve_paint(value)
                    if hex_color:
                        record(hex_color)

    canvas = _canvas_info(root)
    return {
        "canvas": canvas,
        "palette": dict(sorted(palette.items(), key=lambda kv: -kv[1])),
        "text_colors": dict(sorted(text_colors.items(), key=lambda kv: -kv[1])),
        "shape_colors": dict(sorted(shape_colors.items(), key=lambda kv: -kv[1])),
        "text_count": text_count,
        "errors": [],
    }


def _canvas_info(root: ET.Element) -> dict:
    width = root.attrib.get("width")
    height = root.attrib.get("height")
    viewbox = root.attrib.get("viewBox") or root.attrib.get("viewbox")

    def _num(attr: str | None):
        if attr is None:
            return None
        m = re.match(r"^\s*([0-9.]+)", attr)
        return int(float(m.group(1))) if m else None

    w, h = _num(width), _num(height)
    if (w is None or h is None) and viewbox:
        parts = viewbox.replace(",", " ").split()
        if len(parts) == 4:
            try:
                w = w if w is not None else int(float(parts[2]))
                h = h if h is not None else int(float(parts[3]))
            except ValueError:
                pass
    return {"width": w, "height": h, "viewbox": viewbox}


def palette_list(palette: dict[str, int]) -> list[tuple[str, int]]:
    """Palette as a stable list of (hex, count) sorted by count desc."""
    return sorted(palette.items(), key=lambda kv: -kv[1])
