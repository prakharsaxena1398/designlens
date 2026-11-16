# DesignLens — Evidence-Based Design Critique and WCAG Auditor

DesignLens splits design review into two kinds of evidence:

1. **Exact math.** WCAG 2.2 contrast ratios are computed from actual hex
   colors using the specification's relative-luminance formula
   (`0.2126 R + 0.7152 G + 0.0722 B` over linearized sRGB channels) and
   compared against fixed thresholds: AA normal 4.5:1, AA large 3.0:1,
   AAA normal 7.0:1, AAA large 4.5:1. There is no judgment call in this
   path — the same inputs always produce the same pass/fail table.

2. **Heuristic critique.** Layout and usability observations follow
   Nielsen's heuristics plus WCAG 2.2 guidance. They are labeled as
   heuristics because they are:
   - **DEMO mode (default, fully offline):** a deterministic checklist.
     For screenshots it uses PIL — dimensions and aspect-ratio class,
     whitespace estimated by sampling border pixels, dominant color
     extraction and exact contrast of the dominant color vs white and
     black. For SVG uploads it runs on parsed metadata — canvas size and
     layout class, text element density, palette size, and exact-math
     contrast between text colors and shape colors.
   - **LIVE mode:** a vision LLM (OpenAI-compatible chat completions,
     model from `LLM_VISION_MODEL`, default `gpt-4o-mini`) receives the
     screenshot as a base64 data URL and returns severity-tagged findings
     validated with pydantic.

Every DEMO finding is tagged `[DEMO MODE]` so it can never be mistaken
for AI output. No emojis are used anywhere in this project.

## Files

```
app.py          Gradio Blocks UI (three tabs)
wcag.py         Exact WCAG 2.2 math: luminance, contrast, pass/fail, fixes
svgparse.py     SVG color/text/canvas extraction (xml.etree, stdlib)
critique.py     Demo checklist engine + live vision-LLM client
demo_data/      dashboard_card.svg (900x560), mobile_screen.svg (360x640)
tests/test_core.py  15 unittest cases
requirements.txt
```

## Quickstart

```bash
pip install -r requirements.txt
python app.py
# open http://127.0.0.1:7865
```

Tabs:

- **Contrast Auditor** — enter two hex colors (3- or 6-digit, `#` optional,
  ~20 common CSS names accepted) for an exact ratio and AA/AAA pass/fail
  table, or upload an SVG to extract its full palette, see the worst
  pairwise combinations ranked by ratio, and get per-pair findings with
  suggested fixes.
- **Screenshot Critique** — upload a PNG/JPG (or a sample SVG) and run
  DEMO or LIVE critique. Output: severity scorecard, findings table
  (ID, heuristic, severity, observation, recommended fix) and a
  prioritized fix list.
- **Design System Check** — paste a palette; every pair is checked against
  AA, and failing pairs get a lightened or darkened alternate that reaches
  4.5:1 (binary-blend search toward white/black, smallest adjustment wins).

## Sample artifacts

Both sample SVGs are hand-written, valid XML, and contain exactly one
planted contrast failure each, so the demo is self-evident:

- `demo_data/dashboard_card.svg` (900x560): KPI value "12,940" is
  `#B0B0B0` on white — contrast **2.17:1**, below the 3.0 AA large-text
  minimum.
- `demo_data/mobile_screen.svg` (360x640): task meta text is `#A0A0A0` on
  light surfaces — contrast **2.61:1** on white.

## LIVE mode configuration

```bash
export OPENAI_API_KEY=...                # required for LIVE mode
export OPENAI_BASE_URL=https://api.openai.com/v1   # default
export LLM_VISION_MODEL=gpt-4o-mini                # default
```

The request goes to `POST {OPENAI_BASE_URL}/chat/completions` with the
image as `image_url` data URL. Malformed replies, HTTP errors and missing
keys raise a readable `LIVE mode error:` message instead of crashing the UI.

## Tests

```bash
python -m unittest discover -s tests -v
```

15 tests cover: luminance bounds (black 0, white 1), the 21.0:1 black/white
ratio, the 4.3-4.7 band for `#777777` on white, threshold logic (3.0 passes
AA large but fails AA normal), 3-digit hex expansion, named colors, SVG
palette extraction of the planted bad color, text element counting, canvas
geometry, darken/lighten-until-pass reaching 4.5:1, and demo critique of
the sample SVG producing at least 3 findings including a contrast finding.

## Deploying to Hugging Face Spaces

1. Create a Space with SDK "Gradio".
2. Push `app.py`, `wcag.py`, `svgparse.py`, `critique.py`,
   `requirements.txt` and `demo_data/` (tests are optional on the Space).
3. Set Space secrets `OPENAI_API_KEY` (and optionally `OPENAI_BASE_URL`,
   `LLM_VISION_MODEL`) only if you want LIVE mode; DEMO mode needs nothing.
4. Gradio picks up `app.py` automatically; the Space serves on port 7865.

## Honest limitations

- **SVG palette extraction is attribute-level.** It reads `fill`, `stroke`,
  `stop-color` and `color` presentation attributes plus inline `style`, and
  resolves only solid hex values and a small named-color dictionary.
  Gradients (`url(#id)`), CSS stylesheets, `currentColor` inheritance and
  `<style>` blocks are ignored, so extracted palettes can be incomplete.
- **Text/surface pairing in SVG critique is heuristic.** The ratio between
  any two colors is exact, but which color is "text on which surface" is
  inferred from element type and luminance direction; real occlusion
  geometry is not computed, so some flagged pairs may not co-occur on
  screen and not every real failure is flagged.
- **PIL checks are coarse.** Whitespace is estimated from a border ring of
  sampled pixels; the dominant color comes from a 120x120 quantized copy.
  They are signals, not measurements.
- **LIVE mode inherits LLM limitations.** Findings are unverified opinions;
  the UI shows them next to exact math precisely so humans can check claims
  against the deterministic numbers. Model JSON is pydantic-validated, but
  no model can measure a pixel.
- **Large-text detection is not automatic.** Threshold tables include both
  normal and large rows because font size is not reliably extractable from
  screenshots or generic SVGs; the audit presents both thresholds instead
  of guessing.
- WCAG contrast math does not capture all accessibility (focus order, ARIA,
  keyboard paths, motion); DesignLens is a first-pass auditor, not a
  conformance certification.
