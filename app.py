"""DesignLens — Evidence-Based Design Critique and WCAG Auditor.

Gradio Blocks app with three tabs:

  1. Contrast Auditor   — exact WCAG 2.2 contrast math on hex pairs and on a
                          full palette extracted from an uploaded SVG.
  2. Screenshot Critique — heuristic UI critique: DEMO mode runs a
                          deterministic checklist (PIL for images, SVG
                          metadata for SVGs); LIVE mode sends the screenshot
                          to an OpenAI-compatible vision LLM.
  3. Design System Check — pairwise palette audit with lighten/darken
                          suggestions that reach AA normal text (4.5:1).

Run:  python app.py   (serves on http://127.0.0.1:7865)
"""

from __future__ import annotations

import os

import gradio as gr
import pandas as pd

import critique
import svgparse
import wcag

DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_data")


# ---------------------------------------------------------------------------
# Tab 1 — Contrast Auditor
# ---------------------------------------------------------------------------

def audit_pair(fg: str, bg: str) -> tuple[float, pd.DataFrame, str]:
    """Exact contrast math for one foreground/background pair."""
    try:
        fg_n = wcag.normalize_hex(fg)
        bg_n = wcag.normalize_hex(bg)
    except ValueError as exc:
        return float("nan"), pd.DataFrame(), f"Invalid color input: {exc}"
    ratio = wcag.contrast_ratio(fg_n, bg_n)
    results = wcag.evaluate(fg_n, bg_n)
    df = pd.DataFrame([r.as_row() for r in results])
    fails = [r for r in results if not r.passed]
    if not fails:
        verdict = f"{fg_n} on {bg_n}: {ratio}:1 — passes ALL WCAG AA and AAA thresholds."
    else:
        levels = ", ".join(f"{r.level} {r.text_size.lower()}" for r in fails)
        verdict = f"{fg_n} on {bg_n}: {ratio}:1 — FAILS: {levels}."
    return ratio, df, verdict


def audit_svg(svg_path: str) -> tuple[str, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Palette extraction + worst-pair matrix + findings for an uploaded SVG."""
    if not svg_path or not os.path.exists(svg_path):
        return "Upload an SVG file.", pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    try:
        with open(svg_path, "rb") as fh:
            info = svgparse.parse_svg(fh.read())
    except Exception as exc:  # malformed XML or unreadable file
        return f"Could not parse SVG: {exc}", pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    canvas = info["canvas"]
    palette = info["palette"]
    summary = (
        f"Canvas: {canvas['width']}x{canvas['height']} px "
        f"(viewBox: {canvas['viewbox'] or 'none'}) — "
        f"{len(palette)} unique solid colors, {info['text_count']} text elements."
    )

    if not palette:
        empty = pd.DataFrame()
        return summary, empty, empty, empty

    # Palette table with counts
    palette_rows = [
        {"Color": hexv, "Uses": count, "Luminance": round(wcag.luminance_hex(hexv), 4)}
        for hexv, count in svgparse.palette_list(palette)
    ]
    palette_df = pd.DataFrame(palette_rows)

    # Pairwise worst-combination matrix. Text-size heuristic: every pair is
    # evaluated as if one color were text on the other; smaller ratios are
    # flagged against AA large text first, then AA normal.
    pairs = []
    colors = list(palette.keys())
    for i in range(len(colors)):
        for j in range(i + 1, len(colors)):
            c1, c2 = colors[i], colors[j]
            ratio = wcag.contrast_ratio(c1, c2)
            pairs.append({
                "Foreground": c1,
                "Background": c2,
                "Ratio": ratio,
                "AA normal (4.5)": "PASS" if ratio >= wcag.AA_NORMAL else "FAIL",
                "AA large (3.0)": "PASS" if ratio >= wcag.AA_LARGE else "FAIL",
            })
    pairs_df = pd.DataFrame(pairs).sort_values("Ratio").reset_index(drop=True)
    worst_df = pairs_df.head(8)

    findings_rows = []
    for _, row in pairs_df.iterrows():
        if row["AA large (3.0)"] == "FAIL":
            sev = "Critical" if row["Ratio"] < 2.0 else "Major"
            fix = wcag.suggest_fix(row["Foreground"], row["Background"], wcag.AA_NORMAL)
            suggested = f"suggest {fix[0]} at {fix[1]}:1 ({fix[2]}% blend)" if fix else "no single-color fix; change surface"
            findings_rows.append({
                "Severity": sev,
                "Pair": f"{row['Foreground']} on {row['Background']}",
                "Ratio": row["Ratio"],
                "Finding": f"Below the 3.0 AA large-text minimum.",
                "Suggested fix": suggested,
            })
        elif row["AA normal (4.5)"] == "FAIL":
            fix = wcag.suggest_fix(row["Foreground"], row["Background"], wcag.AA_NORMAL)
            suggested = f"suggest {fix[0]} at {fix[1]}:1 ({fix[2]}% blend)" if fix else "no single-color fix; change surface"
            findings_rows.append({
                "Severity": "Minor",
                "Pair": f"{row['Foreground']} on {row['Background']}",
                "Ratio": row["Ratio"],
                "Finding": "Passes AA large text only; body-size text on this pair fails AA.",
                "Suggested fix": suggested,
            })
    findings_df = pd.DataFrame(findings_rows) if findings_rows else pd.DataFrame(
        [{"Severity": "None", "Pair": "-", "Ratio": None,
          "Finding": "All palette pairs meet AA normal text.", "Suggested fix": "-"}]
    )
    return summary, palette_df, worst_df, findings_df


# ---------------------------------------------------------------------------
# Tab 2 — Screenshot Critique
# ---------------------------------------------------------------------------

def run_critique(file_path: str, mode: str) -> tuple[str, pd.DataFrame, str]:
    if not file_path or not os.path.exists(file_path):
        return "Upload a PNG/JPG screenshot (or an SVG in demo mode) first.", pd.DataFrame(), ""
    ext = os.path.splitext(file_path)[1].lower()

    if mode.startswith("LIVE"):
        if ext == ".svg":
            return "LIVE mode requires a raster screenshot; SVGs run only in DEMO mode.", pd.DataFrame(), ""
        try:
            report = critique.run_live_critique(file_path)
        except critique.LiveModeError as exc:
            return f"LIVE mode error: {exc}", pd.DataFrame(), ""
    else:
        try:
            if ext == ".svg":
                with open(file_path, "rb") as fh:
                    report = critique.run_demo_critique(svg_bytes=fh.read())
            else:
                report = critique.run_demo_critique(image_path=file_path)
        except Exception as exc:
            return f"Could not process file: {exc}", pd.DataFrame(), ""

    counts = report.scorecard()
    header = (
        f"### Scorecard — mode: `{report.mode}` ({report.source})\n\n"
        f"| Severity | Count |\n|---|---|\n"
        f"| Critical | {counts['critical']} |\n"
        f"| Major | {counts['major']} |\n"
        f"| Minor | {counts['minor']} |\n"
        f"| Info | {counts['info']} |\n"
    )
    findings_df = pd.DataFrame(report.table_rows())
    fixes = "\n".join(report.prioritized_fixes()) or "No findings."
    return header, findings_df, fixes


# ---------------------------------------------------------------------------
# Tab 3 — Design System Check
# ---------------------------------------------------------------------------

def check_palette(palette_text: str) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    raw = [tok.strip() for chunk in palette_text.replace("\n", ",").split(",") for tok in [chunk.strip()] if tok.strip()]
    try:
        colors = [wcag.normalize_hex(tok) for tok in raw]
    except ValueError as exc:
        return pd.DataFrame(), pd.DataFrame(), f"Invalid palette input: {exc}"
    colors = list(dict.fromkeys(colors))
    if len(colors) < 2:
        return pd.DataFrame(), pd.DataFrame(), "Enter at least two distinct hex colors."

    flags = []
    fixes = []
    for i in range(len(colors)):
        for j in range(i + 1, len(colors)):
            c1, c2 = colors[i], colors[j]
            ratio = wcag.contrast_ratio(c1, c2)
            if ratio >= wcag.AA_NORMAL:
                flags.append({"Pair": f"{c1} / {c2}", "Ratio": ratio,
                              "AA (4.5)": "PASS", "AAA (7.0)": "PASS" if ratio >= wcag.AAA_NORMAL else "FAIL"})
                continue
            flags.append({"Pair": f"{c1} / {c2}", "Ratio": ratio,
                          "AA (4.5)": "FAIL", "AAA (7.0)": "FAIL"})
            # Suggest the smallest adjustment across both colors of the pair.
            options = []
            for candidate, surface, role in ((c1, c2, "first"), (c2, c1, "second")):
                fix = wcag.suggest_fix(candidate, surface, wcag.AA_NORMAL)
                if fix:
                    options.append((fix[2], fix[0], fix[1], role, candidate, surface))
            if options:
                blend, suggested, new_ratio, role, candidate, surface = min(options)
                fixes.append({
                    "Failing pair": f"{c1} / {c2}",
                    "Current ratio": ratio,
                    "Adjusted color": suggested,
                    "Role": f"{role} color ({candidate})",
                    "Blend direction": f"{blend}% toward white/black",
                    "New ratio": new_ratio,
                    "Result": f"{suggested} on {surface} = {new_ratio}:1 (AA normal PASS)",
                })
            else:
                fixes.append({
                    "Failing pair": f"{c1} / {c2}",
                    "Current ratio": ratio,
                    "Adjusted color": "-",
                    "Role": "-",
                    "Blend direction": "-",
                    "New ratio": None,
                    "Result": "No single-color blend reaches 4.5:1; redesign the pair.",
                })

    flags_df = pd.DataFrame(flags).sort_values("Ratio").reset_index(drop=True)
    fixes_df = pd.DataFrame(fixes)
    n_fail = sum(1 for f in flags if f["AA (4.5)"] == "FAIL")
    summary = (f"{len(colors)} colors, {len(colors) * (len(colors) - 1) // 2} pairs checked — "
               f"{n_fail} pair(s) below AA normal text (4.5:1).")
    return flags_df, fixes_df, summary


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def build_ui() -> gr.Blocks:
    with gr.Blocks(title="DesignLens") as demo:
        gr.Markdown(
            "# DesignLens — Evidence-Based Design Critique and WCAG Auditor\n"
            "Contrast is **exact math** (WCAG 2.2 relative luminance). Layout critique is "
            "**heuristic**: deterministic checklists in DEMO mode, a vision LLM in LIVE mode."
        )

        with gr.Tab("Contrast Auditor"):
            gr.Markdown("### Single pair — exact WCAG 2.2 math")
            with gr.Row():
                fg_in = gr.Textbox(label="Foreground hex", value="#777777", placeholder="#RRGGBB or #RGB")
                bg_in = gr.Textbox(label="Background hex", value="#FFFFFF", placeholder="#RRGGBB or #RGB")
                pair_btn = gr.Button("Audit pair", variant="primary")
            pair_ratio = gr.Number(label="Contrast ratio (2 decimals)", precision=2, interactive=False)
            pair_table = gr.DataFrame(label="WCAG conformance", interactive=False)
            pair_verdict = gr.Markdown()

            gr.Markdown("### SVG palette audit")
            gr.Markdown(
                "Upload an SVG to extract its full solid-color palette, see the worst "
                "pairwise combinations and per-pair findings. Try `demo_data/dashboard_card.svg`."
            )
            svg_in = gr.File(label="SVG file", file_types=[".svg"], type="filepath")
            svg_btn = gr.Button("Audit SVG palette", variant="primary")
            svg_summary = gr.Markdown()
            svg_palette = gr.DataFrame(label="Extracted palette", interactive=False)
            svg_worst = gr.DataFrame(label="Worst pairwise combinations", interactive=False)
            svg_findings = gr.DataFrame(label="Findings and suggested fixes", interactive=False)

            pair_btn.click(audit_pair, [fg_in, bg_in], [pair_ratio, pair_table, pair_verdict])
            svg_btn.click(audit_svg, [svg_in], [svg_summary, svg_palette, svg_worst, svg_findings])

        with gr.Tab("Screenshot Critique"):
            gr.Markdown(
                "Upload a UI screenshot (PNG/JPG) or one of the sample SVGs. "
                "**DEMO mode** (default, offline) runs deterministic checks: PIL sampling for "
                "images, SVG metadata for SVGs. **LIVE mode** sends the image to an "
                "OpenAI-compatible vision model (`OPENAI_API_KEY`, `LLM_VISION_MODEL`."
            )
            with gr.Row():
                shot_in = gr.File(label="Screenshot or SVG", file_types=[".png", ".jpg", ".jpeg", ".webp", ".svg"], type="filepath")
                mode_in = gr.Radio(["DEMO (offline, deterministic)", "LIVE (vision LLM)"],
                                   value="DEMO (offline, deterministic)", label="Mode")
            crit_btn = gr.Button("Run critique", variant="primary")
            crit_score = gr.Markdown()
            crit_table = gr.DataFrame(label="Findings", interactive=False)
            crit_fixes = gr.Markdown(label="Prioritized fix list")
            crit_btn.click(run_critique, [shot_in, mode_in], [crit_score, crit_table, crit_fixes])

        with gr.Tab("Design System Check"):
            gr.Markdown(
                "Paste a palette as hex colors separated by commas or new lines "
                "(e.g. `#1F2937, #6B7280, #B0B0B0, #FFFFFF`). Every pair is checked against "
                "AA (4.5:1); failing pairs get a lightened/darkened alternate that passes."
            )
            pal_in = gr.Textbox(
                label="Palette",
                value="#1F2937, #6B7280, #B0B0B0, #EEF1F6, #1F3A5F, #2F6FED, #FFFFFF",
                lines=3,
            )
            pal_btn = gr.Button("Check palette", variant="primary")
            pal_summary = gr.Markdown()
            pal_flags = gr.DataFrame(label="Pair audit", interactive=False)
            pal_fixes = gr.DataFrame(label="Suggested fixes", interactive=False)
            pal_btn.click(check_palette, [pal_in], [pal_flags, pal_fixes, pal_summary])

        gr.Markdown(
            "---\n"
            "Sample artifacts: `demo_data/dashboard_card.svg` (900x560, planted #B0B0B0 text "
            "on white) and `demo_data/mobile_screen.svg` (360x640, planted #A0A0A0 meta text). "
            "DEMO findings are always tagged `[DEMO MODE]`; LIVE findings come from the model "
            "and should be verified against the exact-math results before shipping."
        )
    return demo


def main():
    app = build_ui()
    app.launch(server_name="127.0.0.1", server_port=int(os.environ.get("DESIGNLENS_PORT", "7865")))


if __name__ == "__main__":
    main()
