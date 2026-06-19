import sys
from unittest.mock import MagicMock, patch

from app.utils.unanswered_report import (
    NONSENSE_NLI_LABELS,
    build_knowledge_gap_insight,
    is_nonsense_heuristic,
    prepare_knowledge_gap_report,
    render_question_grid,
    translate_questions_to_dutch,
)
from app.utils.report_html import format_translation_attribution, truncate


def test_format_translation_attribution_uses_uppercase_source_lang():
    assert format_translation_attribution("fr") == " (vertaald van FR - NL)"
    assert format_translation_attribution("en") == " (vertaald van EN - NL)"


def test_translate_questions_to_dutch_appends_attribution():
    registry = MagicMock()
    registry.detect_language.side_effect = ["fr", "nl", "en"]
    registry.translate_to_dutch.side_effect = [
        ("Waar parkeren?", True),
        ("Hoe werkt presale?", True),
    ]
    fake_module = MagicMock()
    fake_module.get_model_registry.return_value = registry
    with patch.dict(sys.modules, {"app.ml.model_registry": fake_module}):
        result = translate_questions_to_dutch(
            [
                "Où se garer?",
                "Waar is de camping?",
                "How does presale work?",
            ]
        )

    assert result[0] == "Waar parkeren? (vertaald van FR - NL)"
    assert result[1] == "Waar is de camping?"
    assert result[2] == "Hoe werkt presale? (vertaald van EN - NL)"


def test_truncate_preserves_translation_attribution():
    text = "Waar parkeren? (vertaald van FR - NL)"
    assert truncate(text, 100) == text
    truncated = truncate(
        "Een hele lange vraag over parkeren en tickets en camping en presale en meer tekst "
        "(vertaald van FR - NL)",
        60,
    )
    assert truncated.endswith("(vertaald van FR - NL)")
    assert "…" in truncated


def test_is_nonsense_heuristic_flags_silly_and_off_topic():
    assert is_nonsense_heuristic("Wat zegt een duif?")
    assert is_nonsense_heuristic("Hoi")
    assert is_nonsense_heuristic("Can you explain Python dictionaries in detail?")
    assert not is_nonsense_heuristic("Waar kan ik mijn festival ticket downloaden?")


@patch("app.utils.unanswered_report.translate_questions_to_dutch")
@patch("app.utils.unanswered_report.classify_nonsense_batch")
def test_classify_nonsense_batch_respects_hf_cap(mock_classify, mock_translate):
    mock_classify.return_value = [True, False]
    mock_translate.side_effect = lambda items: items

    prepare_knowledge_gap_report(["Waar parkeren?", "Hoe werkt presale?"], hf_max=5)

    mock_classify.assert_called_once()
    assert mock_classify.call_args.kwargs.get("hf_max") == 5


@patch("app.utils.unanswered_report.translate_questions_to_dutch")
@patch("app.utils.unanswered_report.classify_nonsense_batch")
def test_prepare_knowledge_gap_report_splits_and_deduplicates(mock_classify, mock_translate):
    mock_classify.return_value = [True, False, False]
    mock_translate.side_effect = lambda items: [f"[nl] {item}" for item in items]

    report = prepare_knowledge_gap_report(
        [
            "Wat zegt een duif?",
            "Wat zegt een duif?",
            "Waar is de camping?",
            "Hoe werkt de presale?",
        ],
        total_unanswered=10,
    )

    assert report.total_unanswered == 10
    assert report.nonsense_count == 1
    assert report.substantive_count == 2
    assert report.pct_nonsense == 33.3
    assert len(report.substantive_questions) == 2
    assert report.substantive_questions[0].startswith("[nl]")


def test_build_knowledge_gap_insight_mentions_nonsense_split():
    from app.utils.unanswered_report import KnowledgeGapReport

    gap = KnowledgeGapReport(
        total_unanswered=20,
        nonsense_count=8,
        substantive_count=12,
        pct_nonsense=40.0,
        pct_substantive=60.0,
        substantive_questions=("Waar parkeren?",),
        nonsense_questions=("Wat zegt een duif?",),
    )
    insight = build_knowledge_gap_insight(
        gap,
        {"weak_answer": 5, "no_reply": 4, "not_answered": 3},
        15.0,
    )

    assert "niet-serieus of onzin" in insight
    assert "echte kennislacunes" in insight
    assert "15%" in insight


def test_render_question_grid_escapes_html():
    html = render_question_grid(['Vraag over <script> & "tickets"?'], 0, 1)
    assert "&lt;script&gt;" in html
    assert "&amp;" in html


def test_nonsense_nli_labels_include_legitimate_and_joke():
    assert len(NONSENSE_NLI_LABELS) == 3


def test_pct_substantive_of_all_questions():
    from app.utils.unanswered_report import KnowledgeGapReport

    gap = KnowledgeGapReport(
        total_unanswered=943,
        nonsense_count=76,
        substantive_count=20,
        pct_nonsense=79.0,
        pct_substantive=21.0,
        substantive_questions=(),
        nonsense_questions=(),
    )
    assert gap.pct_substantive_of_all(18860, unanswered_pct=5.0) == 1.1
    assert gap.pct_substantive_of_all(18860) == 1.0
