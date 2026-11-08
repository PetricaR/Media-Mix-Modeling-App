"""Unit tests for the post-processing of Meridian's standalone HTML reports."""

from __future__ import annotations

from mmm_studio.report.meridian_html import POLISH_MARKER, is_polished, polish_meridian_report

# A trimmed Meridian 2.1 summary/optimisation report: the chip, a stat, insight text and an embedded chart spec
_REPORT = """<!DOCTYPE html>
<html lang="en">
  <head>
    <script src="https://www.gstatic.com/external_hosted/vega_embed/vega-embed.js"></script>
    <title>Marketing Mix Modeling Report</title>
  </head>
  <body>
<chips>
  <chip>
    Time period: Jan 1, 2023 - Jan 2, 2025
  </chip>
</chips>
<stats>
  <stats-title>Non-optimized ROI</stats-title>
  <stat>3.3000000000000003</stat>
</stats>
<p class="insights-text">For every $1 you spent on Sea, you saw $8.40 in revenue.</p>
<script type="text/javascript">
  (() => {
    const spec = JSON.parse("{\\"$schema\\": \\"x\\", \\"labelExpr\\": \\"\\u0027$\\u0027 + format(datum.value)\\"}");
    vegaEmbed('#chart', spec).catch(console.error);
  })();
</script>
  </body>
</html>
"""


def test_polish_fixes_text_and_injects_theme() -> None:
    out = polish_meridian_report(_REPORT, currency="€", interval_days=1)

    assert is_polished(out) and not is_polished(_REPORT)
    assert out.index(POLISH_MARKER) < out.index("</head>")
    assert "Time period: Jan 1, 2023 – Jan 1, 2025" in out
    assert "<stat>3.3</stat>" in out
    assert "For every €1 you spent on Sea, you saw €8.40 in revenue." in out
    # The axis label currency is swapped inside the chart spec; the spec's own "$schema" key is left alone
    assert "\\u0027\\u20ac\\u0027 + format" in out
    assert '\\"$schema\\"' in out
    assert "window.vegaEmbed = Object.assign(polished" in out


def test_polish_is_idempotent_and_keeps_dollar_reports_unchanged_in_text() -> None:
    once = polish_meridian_report(_REPORT)
    assert polish_meridian_report(once) == once
    assert once.count("Jan 1, 2025") == 1

    dollars = polish_meridian_report(_REPORT, currency="$")
    assert "For every $1 you spent" in dollars


def test_polish_respects_the_model_interval() -> None:
    weekly = _REPORT.replace("Jan 2, 2025", "Jan 7, 2025")
    assert "Jan 1, 2023 – Dec 31, 2024" in polish_meridian_report(weekly, interval_days=7)


def test_reports_polished_with_an_older_theme_get_the_current_head_only() -> None:
    current = polish_meridian_report(_REPORT)
    # A version-1 report: the old marker and an old head block (Figtree, gold tints) before </head>
    head_start = current.index("    " + POLISH_MARKER)
    head_end = current.index("</head>")
    old_block = '    <meta name="mmm-studio-report-polish" content="1" />\n    <style>body { font-family: Figtree; }</style>\n'
    older = current[:head_start] + old_block + current[head_end:]

    upgraded = polish_meridian_report(older)

    assert is_polished(upgraded) and not is_polished(older)
    assert upgraded == current
    assert "Figtree" not in upgraded and "IBM Plex Sans" in upgraded
    # The text fixes were not applied twice: the period end did not move back another day
    assert "Time period: Jan 1, 2023 – Jan 1, 2025" in upgraded
    assert upgraded.count("mmm-studio-report-polish") == 1
