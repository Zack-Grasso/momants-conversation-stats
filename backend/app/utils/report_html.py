from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from app.utils.report_format import format_report_num


def escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


TRANSLATION_ATTRIBUTION_RE = re.compile(r" \(vertaald van [A-Z]{2} - NL\)$")


def format_translation_attribution(source_lang: str) -> str:
    code = (source_lang or "??").strip().upper() or "??"
    return f" (vertaald van {code} - NL)"


def truncate(value: str, max_len: int) -> str:
    suffix = ""
    match = TRANSLATION_ATTRIBUTION_RE.search(value)
    if match:
        suffix = match.group(0)
        value = value[: match.start()]
    cleaned = " ".join(value.split())
    if len(cleaned) + len(suffix) <= max_len:
        return cleaned + suffix
    budget = max_len - len(suffix) - 1
    if budget < 1:
        return cleaned[: max_len - 1].rstrip() + "…"
    return cleaned[:budget].rstrip() + "…" + suffix


def render_top_questions_grid(clusters: list[tuple[str, int]], limit: int = 18) -> str:
    cells: list[str] = []
    for index in range(limit):
        if index < len(clusters):
            text, count = clusters[index]
            top_class = " top" if index == 0 else ""
            max_text = 70
            cells.append(
                f'<div class="cluster compact tiny{top_class}">'
                f'<div class="chead">'
                f'<span class="clbl"><i data-lucide="message-circle-question" class="ic"></i> '
                f"Vraag #{index + 1}</span>"
                f'<span class="badge">{count}×</span>'
                f"</div>"
                f'<div class="ctext">"{escape_html(truncate(text, max_text))}"</div>'
                f"</div>"
            )
        else:
            cells.append(
                '<div class="cluster compact tiny">'
                '<div class="chead"><span class="clbl">—</span></div>'
                '<div class="ctext">Geen voorbeeld beschikbaar</div>'
                "</div>"
            )
    if not clusters:
        return '<div class="cluster compact"><div class="ctext">Geen terugkerende vragen deze week.</div></div>'
    return "\n        ".join(cells)


def build_top_questions_insight(clusters: list[tuple[str, int]]) -> str:
    if not clusters:
        return "Geen terugkerende vragen deze week."
    top_text, top_count = clusters[0]
    total = sum(c for _, c in clusters)
    return (
        f'De meest gestelde vraag deze week ("{truncate(top_text, 80)}") kwam {top_count}× voor. '
        f"In totaal {len(clusters)} terugkerende vragen ({total}×)."
    )


STATUS_LABELS = {"no_reply": "Geen reactie", "weak_answer": "Zwak antwoord", "not_answered": "Niet beantwoord"}

UNANSWERED_EXAMPLES_PAGE1 = 18
UNANSWERED_EXAMPLES_PAGE2 = 15
REMAINDER_TEXT = "Alle overige vragen zijn beantwoord"


def _render_unanswered_cell(text: str | None = None, *, remainder: bool = False) -> str:
    if remainder:
        return (
            f'<div class="q-cell q-remainder">'
            f'<span class="q-text">{escape_html(REMAINDER_TEXT)}</span>'
            f"</div>"
        )
    return (
        f'<div class="q-cell">'
        f'<span class="q-text">"{escape_html(truncate(text or "", 100))}"</span>'
        f"</div>"
    )


def render_unanswered_examples(questions: list[str], start: int = 0, count: int = UNANSWERED_EXAMPLES_PAGE1) -> str:
    if not questions and start == 0:
        return (
            '<div class="q-cell"><span class="q-text">'
            "Geen onbeantwoorde of zwakke antwoorden deze week."
            "</span></div>"
        )
    cells: list[str] = []
    for offset in range(count):
        index = start + offset
        if index < len(questions):
            cells.append(_render_unanswered_cell(questions[index]))
        else:
            cells.append(_render_unanswered_cell(remainder=True))
    return "\n          ".join(cells)


def render_unanswered_examples_page2(
    questions: list[str],
    start: int = UNANSWERED_EXAMPLES_PAGE1,
    count: int = UNANSWERED_EXAMPLES_PAGE2,
) -> str:
    return render_unanswered_examples(questions, start, count)


def render_examples_page2_slide(
    *,
    page2_html: str,
    insight: str,
    event_name: str,
    date_range: str,
    slide_num: str,
    slide_count: str,
) -> str:
    if not page2_html.strip():
        return ""
    logo = momants_logo_html()
    return f"""
<section class="slide">
  <div class="topbar">
    <div class="logo">{logo}</div>
    <div class="doc">{escape_html(event_name)} · Weekly Report</div>
  </div>
  <div class="eyebrow">Onbeantwoorde vragen</div>
  <h1>Wat kon de agent niet beantwoorden? <span style="font-size:22px;font-weight:600;color:var(--muted)">(2/2)</span></h1>
  <div class="body body-with-insight" style="padding-top:10px">
    <div class="fill">
      <div class="card card-fill" style="padding:12px 16px">
        <div class="lbl"><i data-lucide="clock" class="ic"></i> Recente onbeantwoorde vragen (vervolg)</div>
        <div class="q-grid questions-grid">
          {page2_html}
        </div>
      </div>
    </div>
    <div class="insight">
      <strong>Key Insight</strong> · {escape_html(insight)}
    </div>
  </div>
  <div class="footer"><span>{escape_html(event_name)} · Weekly Report {escape_html(date_range)} · Momants</span><span>{slide_num} / {slide_count}</span></div>
</section>"""


def render_breakdown_pills(breakdown: dict[str, int]) -> str:
    labels = [
        ("no_reply", "Geen reactie"),
        ("weak_answer", "Zwak antwoord"),
        ("not_answered", "Niet beantwoord"),
    ]
    return "\n      ".join(
        f'<span class="pill-stat">{label}: {breakdown.get(key, 0)}</span>' for key, label in labels
    )


@lru_cache
def momants_logo_html() -> str:
    template = Path(__file__).resolve().parents[2] / "templates" / "conversation-analysis-template-v2.html"
    html = template.read_text(encoding="utf-8")
    start = html.find('<svg class="mom-logo"')
    if start < 0:
        return ""
    end = html.find("</svg>", start) + len("</svg>")
    return html[start:end]


def build_unanswered_insight(
    breakdown: dict[str, int],
    total: int,
    pct: float | None,
    member_questions: int = 0,
) -> str:
    if total <= 0:
        return "Geen onbeantwoorde of zwakke antwoorden deze week."
    weak = breakdown.get("weak_answer", 0)
    no_reply = breakdown.get("no_reply", 0)
    semantic = breakdown.get("not_answered", 0)
    dominant_count, label = max(
        [(weak, "zwakke antwoorden"), (no_reply, "genegeerde vragen"), (semantic, "antwoorden die de kern misten")],
        key=lambda x: x[0],
    )
    if pct is not None and member_questions > 0:
        pct_label = format_report_num(pct, 1)
        return (
            f"{total} flagged vragen ({dominant_count} {label}) — "
            f"{pct_label}% van {member_questions} vragen deze week."
        )
    return f"{total} flagged vragen ({dominant_count} {label})."
