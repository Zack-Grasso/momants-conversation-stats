"""Prepare unanswered-question data for the knowledge-gap report slides."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.utils.report_format import format_dutch_int, format_report_num
from app.utils.report_html import format_translation_attribution, truncate

KNOWLEDGE_GAP_PAGE1_COUNT = 8
KNOWLEDGE_GAP_PAGE2_COUNT = 6
KNOWLEDGE_GAP_NONSENSE_COUNT = 5

TRIVIAL_QUESTION_RE = re.compile(
    r"^(hallo|hoi|hey|hi|dag|dank|thanks|bedankt|oké?|oke|ja|nee|top|super|goed|mooi)\b",
    re.I,
)
SILLY_QUESTION_RE = re.compile(
    r"\b(kikker|kwak|duif|pigeon|frog|grappig|ik ben een|speel je|pretend|onzin)\b|"
    r"in die taal uitleggen",
    re.I,
)
OFF_TOPIC_TECH_RE = re.compile(
    r"\b(\.net|c#|python|javascript|dictionary|programming|code\b|chatgpt)\b",
    re.I,
)
FESTIVAL_TOPIC_RE = re.compile(
    r"\b(ticket|korting|discount|festival|reis|parkeren|camp|line.?up|programma|"
    r"entree|toegang|bus|trein|hotel|presale|voorverkoop|order)\b",
    re.I,
)
MISDIRECTED_TOPIC_RE = re.compile(r"\b(aangifte|belasting|tax return)\b", re.I)

NONSENSE_NLI_LABELS = (
    "legitimate customer question about an event service or ticket",
    "joke nonsense trolling or meaningless question",
    "off-topic unrelated question not about customer service",
)
LEGITIMATE_LABEL = NONSENSE_NLI_LABELS[0]
NONSENSE_NLI_THRESHOLD = 0.42


@dataclass(frozen=True)
class KnowledgeGapReport:
    total_unanswered: int
    nonsense_count: int
    substantive_count: int
    pct_nonsense: float | None
    pct_substantive: float | None
    substantive_questions: tuple[str, ...]
    nonsense_questions: tuple[str, ...]

    def pct_substantive_of_all(
        self,
        question_total: int,
        *,
        unanswered_pct: float | None = None,
    ) -> float | None:
        """Real knowledge gaps as % of all member questions (same basis as pct_unanswered)."""
        if unanswered_pct is not None and self.pct_substantive is not None:
            return round(unanswered_pct * self.pct_substantive / 100, 1)
        if question_total <= 0:
            return None
        classified = self.nonsense_count + self.substantive_count
        if classified <= 0:
            return 0.0
        substantive_total = self.total_unanswered * self.substantive_count / classified
        return round(100 * substantive_total / question_total, 1)


def _normalize_question_key(text: str) -> str:
    stripped = re.sub(r"[^\w\s]", "", text.lower(), flags=re.UNICODE)
    return " ".join(stripped.split())


def is_nonsense_heuristic(question: str) -> bool:
    cleaned = question.strip()
    if len(cleaned) < 12:
        return True
    if TRIVIAL_QUESTION_RE.match(cleaned):
        return True
    if SILLY_QUESTION_RE.search(cleaned):
        return True
    if OFF_TOPIC_TECH_RE.search(cleaned) and not FESTIVAL_TOPIC_RE.search(cleaned):
        return True
    return False


def classify_nonsense_batch(questions: list[str], *, hf_max: int | None = None) -> list[bool]:
    if not questions:
        return []

    results: list[bool | None] = [None] * len(questions)
    pending_texts: list[str] = []
    pending_indexes: list[int] = []

    for index, question in enumerate(questions):
        if is_nonsense_heuristic(question):
            results[index] = True
        else:
            pending_texts.append(f"Question: {question[:512]}")
            pending_indexes.append(index)

    if pending_texts:
        limit = len(pending_texts) if hf_max is None else max(0, hf_max)
        hf_texts = pending_texts[:limit]
        hf_indexes = pending_indexes[:limit]
        for index in pending_indexes[limit:]:
            results[index] = False

        if hf_texts:
            from app.ml.model_registry import get_model_registry

            registry = get_model_registry()
            try:
                batch = registry.classify_zero_shot_batch(hf_texts, list(NONSENSE_NLI_LABELS))
            except Exception:
                batch = [(LEGITIMATE_LABEL, 0.0) for _ in hf_texts]

            for index, (label, score) in zip(hf_indexes, batch, strict=True):
                is_nonsense = label != LEGITIMATE_LABEL and score >= NONSENSE_NLI_THRESHOLD
                results[index] = is_nonsense

    return [bool(item) for item in results]


def translate_questions_to_dutch(questions: list[str]) -> list[str]:
    if not questions:
        return []

    from app.ml.model_registry import get_model_registry

    registry = get_model_registry()
    translated: list[str] = []
    for question in questions:
        lang = registry.detect_language(question)
        if lang == "nl":
            translated.append(question)
            continue
        dutch, was_translated = registry.translate_to_dutch(question, lang)
        if was_translated:
            translated.append(dutch + format_translation_attribution(lang))
        else:
            translated.append(question)
    return translated


def prepare_knowledge_gap_report(
    questions: list[str],
    *,
    total_unanswered: int | None = None,
    hf_max: int | None = None,
) -> KnowledgeGapReport:
    unique: list[str] = []
    seen: set[str] = set()
    for question in questions:
        cleaned = question.strip()
        if not cleaned:
            continue
        key = _normalize_question_key(cleaned)
        if key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)

    total = total_unanswered if total_unanswered is not None else len(unique)
    if not unique:
        return KnowledgeGapReport(
            total_unanswered=total,
            nonsense_count=0,
            substantive_count=0,
            pct_nonsense=None,
            pct_substantive=None,
            substantive_questions=(),
            nonsense_questions=(),
        )

    flags = classify_nonsense_batch(unique, hf_max=hf_max)
    nonsense_raw = [q for q, flag in zip(unique, flags, strict=True) if flag]
    substantive_raw = [q for q, flag in zip(unique, flags, strict=True) if not flag]

    nonsense_display = translate_questions_to_dutch(nonsense_raw[:9])
    substantive_display = translate_questions_to_dutch(substantive_raw[:36])

    nonsense_count = len(nonsense_raw)
    substantive_count = len(substantive_raw)
    classified_total = nonsense_count + substantive_count or 1
    pct_nonsense = round(100 * nonsense_count / classified_total, 1) if classified_total else None
    pct_substantive = round(100 * substantive_count / classified_total, 1) if classified_total else None

    return KnowledgeGapReport(
        total_unanswered=total,
        nonsense_count=nonsense_count,
        substantive_count=substantive_count,
        pct_nonsense=pct_nonsense,
        pct_substantive=pct_substantive,
        substantive_questions=tuple(substantive_display),
        nonsense_questions=tuple(nonsense_display),
    )


def render_question_grid(examples: list[str], start: int, count: int, *, empty_label: str | None = None) -> str:
    from html import escape

    cells: list[str] = []
    for offset in range(count):
        idx = start + offset
        if idx >= len(examples):
            break
        text = truncate(examples[idx], 100)
        cells.append(f'<div class="q-cell"><span class="q-text">"{escape(text)}"</span></div>')

    if empty_label and not cells:
        cells.append(f'<div class="q-cell q-remainder">{escape(empty_label)}</div>')
    elif empty_label and len(examples) <= start + count and start > 0:
        cells.append(f'<div class="q-cell q-remainder">{escape(empty_label)}</div>')

    return "\n          ".join(cells)


def build_knowledge_gap_insight(
    gap: KnowledgeGapReport,
    breakdown: dict[str, int],
    unanswered_pct: float | None,
) -> str:
    if gap.total_unanswered <= 0:
        return "Geen onbeantwoorde vragen in deze periode — de agent beantwoordt alles wat mensen vroegen."

    pct_all = format_report_num(unanswered_pct, 0) if unanswered_pct is not None else "—"
    parts: list[str] = []

    if gap.nonsense_count > 0:
        nonsense_pct = format_report_num(gap.pct_nonsense, 0) if gap.pct_nonsense is not None else "—"
        parts.append(
            f"Van de {format_dutch_int(gap.total_unanswered)} onbeantwoorde vragen waren "
            f"{format_dutch_int(gap.nonsense_count)} ({nonsense_pct}%) niet-serieus of onzin "
            f"(grapjes, off-topic of spam)."
        )
        parts.append(
            f"De overige {format_dutch_int(gap.substantive_count)} vragen "
            f"({format_report_num(gap.pct_substantive, 0) if gap.pct_substantive is not None else '—'}%) "
            f"zijn echte kennislacunes — inhoudelijk niet of onvoldoende beantwoord."
        )
    else:
        parts.append(
            f"Alle {format_dutch_int(gap.substantive_count)} onbeantwoorde vragen zijn inhoudelijk relevant "
            f"— geen grapjes of off-topic gedetecteerd."
        )

    parts.append(f"In totaal is {pct_all}% van alle vragen onbeantwoord gebleven.")

    weak = breakdown.get("weak_answer", 0)
    no_reply = breakdown.get("no_reply", 0)
    semantic = breakdown.get("not_answered", 0)
    if gap.substantive_count > 0:
        categories = [
            (weak, "onvoldoende antwoord"),
            (no_reply, "geen reactie"),
            (semantic, "antwoord miste de kern"),
        ]
        dominant_count, label = max(categories, key=lambda item: item[0])
        if dominant_count > 0:
            dominant_pct = round(100 * dominant_count / max(weak + no_reply + semantic, 1))
            parts.append(
                f"Binnen de echte kennislacunes ging het meest om {label} "
                f"({format_dutch_int(dominant_count)}, {dominant_pct}%)."
            )

    if gap.substantive_questions:
        example = truncate(gap.substantive_questions[0], 90)
        parts.append(f'Voorbeeld kennislacune: "{example}".')

    return " ".join(parts)
