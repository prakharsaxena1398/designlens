"""critique.py — Heuristic UI critique engine (DEMO deterministic + LIVE vision LLM).

Two completely separate critique paths, never mixed:

DEMO MODE (default, offline):
  For images  : deterministic PIL checks — dimensions/aspect, border-sampled
                whitespace estimate, dominant color extraction, dominant
                color vs white/black contrast.
  For SVGs    : deterministic checks on parsed metadata — canvas size and
                layout class, worst palette-pair WCAG contrast (exact math
                from wcag.py), text element density, palette size.
  Every demo finding is labeled "[DEMO MODE ...]" at the report level so it
  can never be mistaken for AI output.

LIVE MODE:
  Sends the screenshot as a base64 data URL to an OpenAI-compatible chat
  completions endpoint and asks for severity-tagged heuristic findings as
  JSON, validated with pydantic. Requires OPENAI_API_KEY.
"""

from __future__ import annotations

import base64
import json
import os
import re
from collections import Counter
from typing import Literal, Optional

import requests
from PIL import Image
from pydantic import BaseModel, Field, ValidationError

import wcag
from svgparse import parse_svg

SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2, "info": 3}
SEVERITY_LABEL = {"critical": "Critical", "major": "Major", "minor": "Minor", "info": "Info"}


# ---------------------------------------------------------------------------
# Finding model (shared by both modes)
# ---------------------------------------------------------------------------

class Finding(BaseModel):
    id: str
    heuristic: str
    severity: Literal["critical", "major", "minor", "info"]
    observation: str
    recommended_fix: str

    def as_row(self) -> dict:
        return {
            "ID": self.id,
            "Heuristic": self.heuristic,
            "Severity": SEVERITY_LABEL[self.severity],
            "Observation": self.observation,
            "Recommended fix": self.recommended_fix,
        }


class CritiqueReport(BaseModel):
    mode: str
    source: str
    findings: list[Finding] = Field(default_factory=list)

    def scorecard(self) -> dict[str, int]:
        counts = Counter(f.severity for f in self.findings)
        return {s: counts.get(s, 0) for s in ("critical", "major", "minor", "info")}

    def prioritized_fixes(self) -> list[str]:
        ordered = sorted(
            self.findings,
            key=lambda f: (SEVERITY_ORDER[f.severity], f.id),
        )
        return [f"{i + 1}. [{SEVERITY_LABEL[f.severity]}] {f.id}: {f.recommended_fix}"
                for i, f in enumerate(ordered)]

    def table_rows(self) -> list[dict]:
        return [f.as_row() for f in
                sorted(self.findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.id))]


# ---------------------------------------------------------------------------
# DEMO MODE — SVG metadata checklist
# ---------------------------------------------------------------------------

def _svg_findings(svg_source: bytes) -> CritiqueReport:
    info = parse_svg(svg_source)
    canvas = info["canvas"]
    palette: dict[str, int] = info["palette"]
    text_count = info["text_count"]
    findings: list[Finding] = []

    if not palette:
        return CritiqueReport(mode="demo", source="SVG metadata", findings=[Finding(
            id="DL-C0", heuristic="Visibility of system status", severity="minor",
            observation="[DEMO MODE] No solid colors found in the SVG; nothing to audit.",
            recommended_fix="Add explicit fill/stroke colors so the palette can be audited.",
        )])

    w, h = canvas["width"], canvas["height"]
    aspect = (w / h) if (w and h) else None
    if w and h and w <= 480:
        layout = "mobile-ish layout"
        heuristic = "Visibility of system status / platform fit"
        note = (f"Canvas {w}x{h} (aspect {aspect:.2f}) reads as a {layout}. "
                f"Verify touch targets are at least 44x44 px and the layout reflows on small screens.")
        severity = "minor" if text_count > 8 else "info"
    elif aspect is not None and 1.4 <= aspect <= 1.9:
        layout = "desktop or card layout"
        heuristic = "Aesthetic and minimalist design"
        note = (f"Canvas {w}x{h} (aspect {aspect:.2f}) reads as a {layout}. "
                f"Confirm information density stays scannable at this width.")
        severity = "info"
    else:
        layout = "custom canvas"
        heuristic = "Visibility of system status"
        note = (f"Canvas {w}x{h} (aspect {aspect if aspect is not None else 'n/a'}) "
                f"does not match common mobile (9:16-9:19.5) or desktop (16:9) frames.")
        severity = "minor"

    findings.append(Finding(
        id="DL-C1", heuristic=heuristic, severity=severity,
        observation=f"[DEMO MODE] Canvas geometry: {note}",
        recommended_fix=("Keep primary actions within thumb reach and test at 360 px width."
                         if layout == "mobile-ish layout"
                         else "Group related metrics and keep the widest content column under ~75 characters of detail text."),
    ))

    # Exact-math contrast between text colors and shape colors extracted from
    # the SVG. Pairing text with surfaces is a heuristic (the ratio itself is
    # exact math): for each text color we evaluate the worst (lowest-contrast)
    # surface that is lighter than the text when the text is dark, or darker
    # than the text when the text is light — i.e. the most plausible
    # text-on-surface direction for that tone.
    text_colors = info.get("text_colors") or dict(palette)
    shape_colors = info.get("shape_colors") or dict(palette)
    combos: list[tuple[str, str, float]] = []
    for tcolor, _tcount in text_colors.items():
        t_lum = wcag.relative_luminance(wcag.parse_hex(tcolor))
        candidates = [
            (scolor, wcag.contrast_ratio(tcolor, scolor))
            for scolor in shape_colors
            if scolor != tcolor
            and (wcag.relative_luminance(wcag.parse_hex(scolor)) > t_lum) == (t_lum < 0.5)
        ]
        if not candidates:
            candidates = [(s, wcag.contrast_ratio(tcolor, s))
                          for s in shape_colors if s != tcolor]
        scolor, ratio = min(candidates, key=lambda kv: kv[1])
        combos.append((tcolor, scolor, ratio))
    combos.sort(key=lambda c: c[2])
    worst = combos[0] if combos else None

    failing = [c for c in combos if c[2] < wcag.AA_NORMAL]

    if failing:
        c1, c2, ratio = worst
        if ratio < wcag.AA_LARGE:
            severity = "critical"
            bound = "3.0 (AA large text)"
        else:
            severity = "major"
            bound = "4.5 (AA normal text)"
        fix = wcag.suggest_fix(c1, c2, wcag.AA_NORMAL)
        if fix:
            suggested, new_ratio, blend = fix
            fix_text = (f"Adjust {c1} toward an accessible tone, e.g. {suggested} "
                        f"reaches {new_ratio}:1 against {c2} ({blend}% blend).")
        else:
            fix_text = f"No single-color blend of {c1} reaches 4.5:1 on {c2}; change the surface color instead."
        listing = "; ".join(f"{t} on {s} = {r}:1" for t, s, r in failing[:4])
        findings.append(Finding(
            id="DL-C2", heuristic="WCAG 2.2 contrast (exact math on extracted palette)",
            severity=severity,
            observation=(f"[DEMO MODE] {len(failing)} text/surface combination(s) fall below AA: "
                         f"{listing}. Worst pair {c1} on {c2} is {ratio}:1, below {bound}."),
            recommended_fix=fix_text,
        ))
    else:
        findings.append(Finding(
            id="DL-C2", heuristic="WCAG 2.2 contrast (exact math on extracted palette)",
            severity="info",
            observation=(f"[DEMO MODE] All {len(combos)} text/surface combinations meet "
                         f"AA normal text (worst pair {worst[0]}/{worst[1]} at {worst[2]}:1)." if worst
                         else "[DEMO MODE] Palette too small for pairwise checks."),
            recommended_fix="No contrast remediation required; re-check after any palette edit.",
        ))

    # Text density heuristic
    density = text_count / max(1, (w * h) / 100000) if w and h else text_count
    if layout == "mobile-ish layout" and text_count > 10:
        findings.append(Finding(
            id="DL-C3", heuristic="Recognition rather than recall / text density",
            severity="major",
            observation=(f"[DEMO MODE] {text_count} text elements on a {w}x{h} canvas "
                         f"(density {density:.2f} per 100k px) is heavy for a mobile screen."),
            recommended_fix="Collapse secondary text into expandable rows or move detail to a second screen.",
        ))
    else:
        findings.append(Finding(
            id="DL-C3", heuristic="Recognition rather than recall / text density",
            severity="info",
            observation=(f"[DEMO MODE] {text_count} text elements on a {w}x{h} canvas "
                         f"(density {density:.2f} per 100k px) is within a reasonable range."),
            recommended_fix="Keep labels short; prefer icons with text labels for recurring actions.",
        ))

    # Palette size heuristic
    if len(palette) > 10:
        findings.append(Finding(
            id="DL-C4", heuristic="Consistency and standards (color discipline)",
            severity="minor",
            observation=(f"[DEMO MODE] {len(palette)} distinct colors detected; systems with more "
                         "than 10 colors tend to drift away from a shared design language."),
            recommended_fix="Consolidate onto a tokenized palette (1 accent, 2-3 neutrals, fixed semantic colors).",
        ))
    else:
        findings.append(Finding(
            id="DL-C4", heuristic="Consistency and standards (color discipline)",
            severity="info",
            observation=f"[DEMO MODE] {len(palette)} distinct colors detected, consistent with a tokenized palette.",
            recommended_fix="Document the palette as named tokens so future screens reuse the same values.",
        ))

    return CritiqueReport(mode="demo", source="SVG metadata", findings=findings)


# ---------------------------------------------------------------------------
# DEMO MODE — image checklist via PIL
# ---------------------------------------------------------------------------

def _border_whitespace(img: Image.Image, ring: int = 4, sample_step: int = 8) -> float:
    """Fraction of near-white pixels in a border ring around the image."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    near_white = 0
    total = 0
    px = rgb.load()
    coords = []
    coords += [(x, y) for x in range(0, w, sample_step) for y in list(range(0, min(ring, h)))]
    coords += [(x, y) for x in range(0, w, sample_step) for y in range(max(0, h - ring), h)]
    coords += [(x, y) for y in range(0, h, sample_step) for x in list(range(0, min(ring, w)))]
    coords += [(x, y) for y in range(0, h, sample_step) for x in range(max(0, w - ring), w)]
    for x, y in coords:
        r, g, b = px[x, y]
        total += 1
        if r > 235 and g > 235 and b > 235:
            near_white += 1
    return near_white / total if total else 0.0


def _dominant_color(img: Image.Image, max_colors: int = 8) -> str:
    small = img.convert("RGB").resize((min(120, img.width), min(120, img.height)))
    quant = small.quantize(colors=max_colors)
    counts = sorted(quant.getcolors(maxcolors=256 * 256 * 256) or [], reverse=True)
    if not counts:
        return "#FFFFFF"
    idx = counts[0][1]
    palette = quant.getpalette() or []
    r, g, b = palette[idx * 3: idx * 3 + 3]
    return wcag.to_hex((r, g, b))


def _image_findings(img: Image.Image, source_name: str) -> CritiqueReport:
    w, h = img.size
    findings: list[Finding] = []
    demo_tag = "[DEMO MODE — deterministic checks, no AI vision]"

    aspect = w / h if h else 0.0
    if 0.45 <= aspect <= 0.62:
        layout, heuristic = "mobile-portrait", "Visibility of system status"
        note = f"Aspect {aspect:.2f} suggests a mobile-portrait screen ({w}x{h} px)."
        severity = "info"
        fix = "Verify touch targets of at least 44x44 px and test at 320-390 px widths."
    elif 1.5 <= aspect <= 1.9:
        layout, heuristic = "desktop/landscape", "Aesthetic and minimalist design"
        note = f"Aspect {aspect:.2f} suggests a desktop or card layout ({w}x{h} px)."
        severity = "info"
        fix = "Keep the primary content column readable; avoid ultra-wide text lines."
    else:
        layout, heuristic = "unusual aspect", "Visibility of system status"
        note = f"Aspect {aspect:.2f} ({w}x{h} px) matches neither common mobile nor desktop frames."
        severity = "minor"
        fix = "Design against standard device frames so proportions stay predictable."

    findings.append(Finding(
        id="DL-D1", heuristic=heuristic, severity=severity,
        observation=f"{demo_tag} Canvas: {note}",
        recommended_fix=fix,
    ))

    if min(w, h) < 400:
        findings.append(Finding(
            id="DL-D2", heuristic="Help users recognize and recover (resolution)",
            severity="minor",
            observation=(f"{demo_tag} Image is small ({w}x{h} px); fine text and 1px rules may "
                         "be unreadable or aliased at this resolution."),
            recommended_fix="Export screenshots at 2x device pixel ratio for legible review artifacts.",
        ))
    else:
        findings.append(Finding(
            id="DL-D2", heuristic="Help users recognize and recover (resolution)",
            severity="info",
            observation=f"{demo_tag} Resolution {w}x{h} px is adequate for reviewing text sizing.",
            recommended_fix="No action needed; keep exporting at the same fidelity.",
        ))

    ws = _border_whitespace(img)
    if ws >= 0.6:
        findings.append(Finding(
            id="DL-D3", heuristic="Aesthetic and minimalist design (whitespace)",
            severity="info",
            observation=(f"{demo_tag} Border sampling estimates {ws * 100:.0f}% whitespace on the "
                         "outer frame; the layout appears to breathe."),
            recommended_fix="Preserve margins; avoid letting content creep to the edges in future revisions.",
        ))
    elif ws >= 0.2:
        findings.append(Finding(
            id="DL-D3", heuristic="Aesthetic and minimalist design (whitespace)",
            severity="minor",
            observation=(f"{demo_tag} Border sampling estimates only {ws * 100:.0f}% whitespace on the "
                         "outer frame; content may feel cramped at the edges."),
            recommended_fix="Add an outer margin of at least 16-24 px around primary containers.",
        ))
    else:
        findings.append(Finding(
            id="DL-D3", heuristic="Aesthetic and minimalist design (whitespace)",
            severity="major",
            observation=(f"{demo_tag} Border sampling estimates {ws * 100:.0f}% whitespace on the "
                         "outer frame; the layout is edge-to-edge with little visual rest."),
            recommended_fix="Introduce a page gutter and card padding rhythm (e.g. 24 px outer, 16 px inner).",
        ))

    dominant = _dominant_color(img)
    vs_white = wcag.contrast_ratio(dominant, "#FFFFFF")
    vs_black = wcag.contrast_ratio(dominant, "#000000")
    best_text = "white" if vs_white >= vs_black else "black"
    best_ratio = max(vs_white, vs_black)
    if best_ratio < wcag.AA_NORMAL:
        findings.append(Finding(
            id="DL-D4", heuristic="WCAG contrast of dominant color vs text (exact math)",
            severity="critical" if best_ratio < wcag.AA_LARGE else "major",
            observation=(f"{demo_tag} Dominant color {dominant} reaches only {best_ratio:.2f}:1 against "
                         f"{'white' if best_text == 'white' else 'black'} text; body copy on this "
                         "surface would fail AA regardless of text tone."),
            recommended_fix=(f"Lighten or darken the dominant surface so it clears 4.5:1, "
                             f"e.g. {wcag.suggest_fix(dominant, '#FFFFFF', wcag.AA_NORMAL) or 'redesign the surface'}."),
        ))
    else:
        findings.append(Finding(
            id="DL-D4", heuristic="WCAG contrast of dominant color vs text (exact math)",
            severity="info",
            observation=(f"{demo_tag} Dominant color {dominant} supports {best_text} text at "
                         f"{best_ratio:.2f}:1 (white: {vs_white:.2f}:1, black: {vs_black:.2f}:1)."),
            recommended_fix="Pair this surface only with text tones that keep the measured ratio above 4.5:1.",
        ))

    return CritiqueReport(mode="demo", source=source_name, findings=findings)


# ---------------------------------------------------------------------------
# LIVE MODE — OpenAI-compatible vision LLM
# ---------------------------------------------------------------------------

OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
LLM_VISION_MODEL = os.environ.get("LLM_VISION_MODEL", "gpt-4o-mini")

_SYSTEM_PROMPT = (
    "You are a senior UI/UX reviewer. Critique the given UI screenshot using "
    "Nielsen's 10 usability heuristics and WCAG 2.2 accessibility guidance. "
    "Return STRICT JSON only, no markdown fences, matching this schema: "
    '{"findings": [{"id": "string", "heuristic": "string", '
    '"severity": "critical"|"major"|"minor"|"info", '
    '"observation": "string", "recommended_fix": "string"}]}. '
    "Produce between 4 and 8 concrete findings tied to what is visible."
)


class LiveModeError(RuntimeError):
    pass


def run_live_critique(image_path: str) -> CritiqueReport:
    """Send the screenshot to the configured vision LLM and validate the reply."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise LiveModeError(
            "LIVE mode needs OPENAI_API_KEY in the environment. "
            "Switch to DEMO mode for the offline deterministic checklist."
        )
    with open(image_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    ext = os.path.splitext(image_path)[1].lstrip(".").lower() or "png"
    if ext == "jpg":
        ext = "jpeg"
    data_url = f"data:image/{ext};base64,{b64}"

    payload = {
        "model": LLM_VISION_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "Critique this UI screenshot now."},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ],
        "temperature": 0.2,
        "max_tokens": 1500,
    }
    url = f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions"
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=90,
        )
    except requests.RequestException as exc:
        raise LiveModeError(f"Vision LLM request failed: {exc}") from exc
    if resp.status_code != 200:
        raise LiveModeError(f"Vision LLM returned HTTP {resp.status_code}: {resp.text[:300]}")

    try:
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise LiveModeError(f"Unexpected LLM response shape: {body}") from exc

    # Some models wrap JSON in fences or prose; find the outermost JSON object.
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise LiveModeError(f"LLM reply contained no JSON object: {content[:300]}")
    try:
        raw = json.loads(match.group(0))
        report = CritiqueReport(
            mode="live",
            source=f"vision model {LLM_VISION_MODEL}",
            findings=[Finding(**f) for f in raw.get("findings", [])],
        )
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise LiveModeError(f"LLM JSON failed pydantic validation: {exc}") from exc

    if not report.findings:
        raise LiveModeError("LLM returned zero findings; refusing to show an empty critique.")
    # Re-ID deterministically so the UI stays stable across providers.
    for i, f in enumerate(report.findings, start=1):
        f.id = f"DL-L{i:02d}"
    return report


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_demo_critique(image_path: Optional[str] = None, svg_bytes: Optional[bytes] = None) -> CritiqueReport:
    """DEMO mode critique. Pass either a raster image path or raw SVG bytes."""
    if svg_bytes is not None:
        return _svg_findings(svg_bytes)
    if image_path:
        img = Image.open(image_path)
        return _image_findings(img, os.path.basename(image_path))
    raise ValueError("run_demo_critique needs an image path or SVG bytes.")
