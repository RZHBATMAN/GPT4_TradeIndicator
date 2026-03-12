"""Shared utility for saving analysis reports as styled HTML files.

Supports two modes:
  1. Structured mode: pass a list of section dicts → renders proper HTML tables, KPI cards
  2. Legacy mode: pass a plain text string → renders in a <pre> block (backward compat)

Both validate_outcomes.py and analyze_signals.py use this to auto-save
reports to the reports/ folder with timestamps.
"""
import os
import html
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

import pytz

ET_TZ = pytz.timezone('US/Eastern')
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')


def _ensure_reports_dir():
    """Create reports/ directory if it doesn't exist."""
    os.makedirs(REPORTS_DIR, exist_ok=True)


# ── Tier tag colors ──
TIER_COLORS = {
    'TRADE_AGGRESSIVE': '#f85149',      # red
    'TRADE_NORMAL': '#d29922',          # yellow
    'TRADE_CONSERVATIVE': '#3fb950',    # green
    'SKIP': '#8b949e',                  # muted
}

TIER_SHORT = {
    'TRADE_AGGRESSIVE': 'AGGRESSIVE',
    'TRADE_NORMAL': 'NORMAL',
    'TRADE_CONSERVATIVE': 'CONSERVATIVE',
    'SKIP': 'SKIP',
}


# ── CSS ──
_CSS = """
:root {
    --bg: #0d1117;
    --fg: #e6edf3;
    --accent: #58a6ff;
    --green: #3fb950;
    --yellow: #d29922;
    --red: #f85149;
    --muted: #8b949e;
    --border: #30363d;
    --surface: #161b22;
    --surface2: #1c2128;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    font-size: 14px;
    line-height: 1.6;
    background: var(--bg);
    color: var(--fg);
    padding: 24px 32px;
}
.container { max-width: 1100px; margin: 0 auto; }

/* ── Header ── */
.report-header {
    text-align: center;
    padding: 24px 0 16px;
    margin-bottom: 24px;
    border-bottom: 2px solid var(--accent);
}
.report-header h1 {
    font-size: 22px;
    font-weight: 700;
    color: var(--accent);
    margin-bottom: 4px;
}
.report-header .subtitle {
    color: var(--muted);
    font-size: 13px;
}

/* ── Table of Contents ── */
.toc {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 20px;
    margin-bottom: 24px;
}
.toc h3 {
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--muted);
    margin-bottom: 8px;
}
.toc a {
    color: var(--accent);
    text-decoration: none;
    display: block;
    padding: 3px 0;
    font-size: 14px;
}
.toc a:hover { text-decoration: underline; }

/* ── Sections ── */
.report-section {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px 24px;
    margin-bottom: 20px;
}
.section-title {
    font-size: 17px;
    font-weight: 700;
    color: var(--accent);
    margin-bottom: 16px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
}

/* ── KPI Grid ── */
.kpi-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 20px;
}
.kpi {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 14px 18px;
    min-width: 160px;
    flex: 1;
}
.kpi .value {
    font-size: 26px;
    font-weight: 700;
    line-height: 1.2;
}
.kpi .label {
    font-size: 12px;
    color: var(--muted);
    margin-top: 2px;
}
.kpi.positive .value { color: var(--green); }
.kpi.negative .value { color: var(--red); }
.kpi.warning .value { color: var(--yellow); }
.kpi.neutral .value { color: var(--fg); }

/* ── Tables ── */
.table-wrapper { overflow-x: auto; margin-bottom: 16px; }
.table-caption {
    font-size: 14px;
    font-weight: 600;
    color: var(--fg);
    margin-bottom: 8px;
}
.data-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
}
.data-table th {
    text-align: left;
    color: var(--accent);
    font-weight: 600;
    padding: 8px 12px;
    border-bottom: 2px solid var(--border);
    white-space: nowrap;
}
.data-table td {
    padding: 7px 12px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
}
.data-table tbody tr:hover { background: var(--surface2); }
.data-table .num { text-align: right; }
td.positive { color: var(--green); }
td.negative { color: var(--red); }
td.warning { color: var(--yellow); }
td.muted { color: var(--muted); }

/* ── Detail Items ── */
.detail-list { margin-bottom: 16px; }
.detail-item {
    padding: 8px 12px;
    border-left: 3px solid var(--border);
    margin-bottom: 6px;
    font-family: 'SF Mono', 'Cascadia Code', monospace;
    font-size: 13px;
    background: var(--surface2);
    border-radius: 0 4px 4px 0;
}
.detail-item.negative { border-left-color: var(--red); color: var(--red); }
.detail-item.positive { border-left-color: var(--green); color: var(--green); }
.detail-item.warning { border-left-color: var(--yellow); color: var(--yellow); }

/* ── Callouts ── */
.callout {
    padding: 12px 16px;
    border-left: 4px solid var(--accent);
    background: var(--surface2);
    border-radius: 0 6px 6px 0;
    margin-bottom: 12px;
    font-size: 13px;
}
.callout.positive { border-left-color: var(--green); }
.callout.negative { border-left-color: var(--red); }
.callout.warning { border-left-color: var(--yellow); }
.callout.info { border-left-color: var(--accent); }

/* ── Sub-sections ── */
.subsection {
    margin-top: 20px;
    padding-top: 16px;
    border-top: 1px solid var(--border);
}
.subsection-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--fg);
    margin-bottom: 10px;
}

/* ── Tags ── */
.tag {
    display: inline-block;
    padding: 1px 8px;
    border-radius: 3px;
    font-size: 11px;
    font-weight: 600;
    font-family: 'SF Mono', 'Cascadia Code', monospace;
}

/* ── Text blocks ── */
.text-block {
    font-size: 13px;
    color: var(--muted);
    margin-bottom: 12px;
    line-height: 1.7;
}

/* ── Footer ── */
.report-footer {
    text-align: center;
    padding-top: 16px;
    margin-top: 8px;
    color: var(--muted);
    font-size: 11px;
    border-top: 1px solid var(--border);
}

/* ── Print ── */
@media print {
    body { background: white; color: #1a1a1a; font-size: 12px; padding: 12px; }
    .report-section { background: #f9f9f9; border-color: #ddd; }
    .kpi { background: #f0f0f0; border-color: #ddd; }
    .section-title, .toc a, .report-header h1, .data-table th { color: #1a5fb4; }
    .kpi.positive .value { color: #2a7e19; }
    .kpi.negative .value { color: #c01c28; }
    .kpi.warning .value { color: #a67c00; }
    td.positive { color: #2a7e19; }
    td.negative { color: #c01c28; }
}

/* ── Legacy <pre> mode ── */
pre.legacy {
    white-space: pre-wrap;
    word-wrap: break-word;
    padding: 16px;
    background: var(--surface);
    border-radius: 8px;
    border: 1px solid var(--border);
    font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
    font-size: 13px;
    line-height: 1.6;
}
"""


# ============================================================================
# HTML BUILDERS
# ============================================================================

def _html_escape(text: str) -> str:
    """Escape text for safe HTML insertion."""
    return html.escape(str(text)) if text else ''


def _render_kpi(kpi: Dict[str, str]) -> str:
    """Render a single KPI card."""
    sentiment = kpi.get('sentiment', 'neutral')
    value = _html_escape(str(kpi.get('value', '')))
    label = _html_escape(str(kpi.get('label', '')))
    return f'<div class="kpi {sentiment}"><div class="value">{value}</div><div class="label">{label}</div></div>'


def _render_kpi_grid(kpis: List[Dict[str, str]]) -> str:
    """Render a grid of KPI cards."""
    if not kpis:
        return ''
    cards = ''.join(_render_kpi(k) for k in kpis)
    return f'<div class="kpi-grid">{cards}</div>'


def _render_table(table: Dict[str, Any]) -> str:
    """Render a data table with optional row classes and column alignment."""
    caption = table.get('caption', '')
    headers = table.get('headers', [])
    rows = table.get('rows', [])
    row_classes = table.get('row_classes', [])
    col_classes = table.get('col_classes', [])

    if not rows:
        return ''

    parts = ['<div class="table-wrapper">']
    if caption:
        parts.append(f'<div class="table-caption">{_html_escape(caption)}</div>')
    parts.append('<table class="data-table"><thead><tr>')
    for h in headers:
        parts.append(f'<th>{_html_escape(h)}</th>')
    parts.append('</tr></thead><tbody>')

    for i, row in enumerate(rows):
        row_cls = row_classes[i] if i < len(row_classes) else None
        tr_cls = f' class="{row_cls}"' if row_cls else ''
        parts.append(f'<tr{tr_cls}>')
        for j, cell in enumerate(row):
            cell_cls = col_classes[j] if j < len(col_classes) else ''
            td_cls = f' class="{cell_cls}"' if cell_cls else ''
            parts.append(f'<td{td_cls}>{_html_escape(str(cell))}</td>')
        parts.append('</tr>')

    parts.append('</tbody></table></div>')
    return ''.join(parts)


def _render_details(details: List[Dict[str, str]]) -> str:
    """Render a list of detail items."""
    if not details:
        return ''
    parts = ['<div class="detail-list">']
    for d in details:
        sentiment = d.get('sentiment', '')
        text = _html_escape(d.get('text', ''))
        parts.append(f'<div class="detail-item {sentiment}">{text}</div>')
    parts.append('</div>')
    return ''.join(parts)


def _render_callouts(callouts: List[Dict[str, str]]) -> str:
    """Render callout boxes."""
    if not callouts:
        return ''
    parts = []
    for c in callouts:
        ctype = c.get('type', 'info')
        text = _html_escape(c.get('text', ''))
        parts.append(f'<div class="callout {ctype}">{text}</div>')
    return ''.join(parts)


def _render_text_blocks(blocks: List[str]) -> str:
    """Render free-text paragraphs."""
    if not blocks:
        return ''
    parts = []
    for b in blocks:
        parts.append(f'<div class="text-block">{_html_escape(b)}</div>')
    return ''.join(parts)


def _render_subsections(subsections: List[Dict[str, Any]]) -> str:
    """Render sub-sections within a section."""
    if not subsections:
        return ''
    parts = []
    for sub in subsections:
        parts.append('<div class="subsection">')
        title = sub.get('title', '')
        if title:
            parts.append(f'<div class="subsection-title">{_html_escape(title)}</div>')
        if sub.get('kpis'):
            parts.append(_render_kpi_grid(sub['kpis']))
        if sub.get('text_blocks'):
            parts.append(_render_text_blocks(sub['text_blocks']))
        if sub.get('tables'):
            for t in sub['tables']:
                parts.append(_render_table(t))
        if sub.get('details'):
            parts.append(_render_details(sub['details']))
        if sub.get('callouts'):
            parts.append(_render_callouts(sub['callouts']))
        parts.append('</div>')
    return ''.join(parts)


def _render_section(section: Dict[str, Any]) -> str:
    """Render a full report section."""
    sid = section.get('id', '')
    title = section.get('title', '')

    parts = [f'<section class="report-section" id="{_html_escape(sid)}">']
    parts.append(f'<h2 class="section-title">{_html_escape(title)}</h2>')

    if section.get('kpis'):
        parts.append(_render_kpi_grid(section['kpis']))
    if section.get('text_blocks'):
        parts.append(_render_text_blocks(section['text_blocks']))
    if section.get('tables'):
        for t in section['tables']:
            parts.append(_render_table(t))
    if section.get('details'):
        parts.append(_render_details(section['details']))
    if section.get('callouts'):
        parts.append(_render_callouts(section['callouts']))
    if section.get('subsections'):
        parts.append(_render_subsections(section['subsections']))

    parts.append('</section>')
    return ''.join(parts)


def _render_toc(sections: List[Dict[str, Any]]) -> str:
    """Render table of contents."""
    parts = ['<nav class="toc"><h3>Contents</h3>']
    for s in sections:
        sid = s.get('id', '')
        title = s.get('title', '')
        parts.append(f'<a href="#{_html_escape(sid)}">{_html_escape(title)}</a>')
    parts.append('</nav>')
    return ''.join(parts)


# ============================================================================
# PUBLIC API
# ============================================================================

def save_html_report(content: Union[str, List[Dict[str, Any]]], prefix: str = 'report') -> str:
    """Save a report as a styled HTML file.

    Args:
        content: Either a plain text string (legacy mode) or a list of
                 section dicts (structured mode).
        prefix: Filename prefix (e.g. 'validate', 'analysis').

    Returns:
        The absolute path to the saved HTML file.
    """
    _ensure_reports_dir()

    now = datetime.now(ET_TZ)
    timestamp = now.strftime('%Y-%m-%d_%H-%M')
    filename = f"{prefix}_{timestamp}.html"
    filepath = os.path.join(REPORTS_DIR, filename)

    if isinstance(content, str):
        html_content = _build_legacy_html(content, now, filename)
    else:
        html_content = _build_structured_html(content, now, filename)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html_content)

    return filepath


def _build_legacy_html(plain_text: str, now: datetime, filename: str) -> str:
    """Build HTML from plain text (backward compat for validate_outcomes.py)."""
    escaped = _html_escape(plain_text)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SPX Signal Report - {now.strftime('%Y-%m-%d %I:%M %p ET')}</title>
    <style>{_CSS}</style>
</head>
<body>
    <div class="container">
        <div class="report-header">
            <h1>SPX Signal Report</h1>
            <div class="subtitle">{now.strftime('%Y-%m-%d %I:%M:%S %p ET')} | {filename}</div>
        </div>
        <pre class="legacy">{escaped}</pre>
        <div class="report-footer">
            Ren's SPX Overnight Vol Premium Signal Engine
        </div>
    </div>
</body>
</html>"""


def _build_structured_html(sections: List[Dict[str, Any]], now: datetime, filename: str) -> str:
    """Build HTML from structured section dicts."""
    toc = _render_toc(sections)
    body_parts = []
    for s in sections:
        body_parts.append(_render_section(s))
    body = ''.join(body_parts)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SPX Performance Report - {now.strftime('%Y-%m-%d %I:%M %p ET')}</title>
    <style>{_CSS}</style>
</head>
<body>
    <div class="container">
        <div class="report-header">
            <h1>SPX Overnight Vol Premium - Performance Report</h1>
            <div class="subtitle">{now.strftime('%Y-%m-%d %I:%M:%S %p ET')} | {filename}</div>
        </div>
        {toc}
        {body}
        <div class="report-footer">
            Ren's SPX Overnight Vol Premium Signal Engine
        </div>
    </div>
</body>
</html>"""
