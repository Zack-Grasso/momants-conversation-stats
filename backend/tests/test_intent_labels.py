from datetime import datetime, timezone

from app.ml.intent_labels import (
    build_intent_text,
    detect_language,
    resolve_intent,
)


class FakeMessage:
    def __init__(
        self,
        content: str,
        *,
        from_agent: bool = False,
        source_created_at: datetime | None = None,
        created_at: datetime | None = None,
    ) -> None:
        self.content = content
        self.from_agent = from_agent
        self.source_created_at = source_created_at
        self.created_at = created_at or datetime.now(timezone.utc)


def test_resolve_intent_low_confidence_returns_general():
    slug, score = resolve_intent({"product_info": 0.2, "general": 0.19}, threshold=0.35, complaint_min_score=0.45)
    assert slug == "general"
    assert score == 0.2


def test_resolve_intent_complaint_neutral_low_score_remaps_to_second():
    slug, score = resolve_intent(
        {"complaint": 0.31, "product_info": 0.29},
        threshold=0.35,
        complaint_min_score=0.45,
        sentiment_stars=3,
    )
    assert slug == "product_info"
    assert score == 0.29


def test_resolve_intent_complaint_neutral_low_score_without_close_second_returns_general():
    slug, score = resolve_intent(
        {"complaint": 0.31, "product_info": 0.15},
        threshold=0.35,
        complaint_min_score=0.45,
        sentiment_stars=3,
    )
    assert slug == "general"
    assert score == 0.31


def test_resolve_intent_complaint_negative_sentiment_kept():
    slug, score = resolve_intent(
        {"complaint": 0.31, "product_info": 0.29},
        threshold=0.35,
        complaint_min_score=0.45,
        sentiment_stars=2,
    )
    assert slug == "complaint"
    assert score == 0.31


def test_resolve_intent_complaint_high_score_kept_with_neutral_sentiment():
    slug, score = resolve_intent(
        {"complaint": 0.69, "general": 0.1},
        threshold=0.35,
        complaint_min_score=0.45,
        sentiment_stars=3,
    )
    assert slug == "complaint"
    assert score == 0.69


def test_build_intent_text_concatenates_member_messages():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    messages = [
        FakeMessage("First question", source_created_at=base),
        FakeMessage("Agent reply", from_agent=True, source_created_at=base),
        FakeMessage("Follow up question", source_created_at=base.replace(second=1)),
        FakeMessage("Another member note", source_created_at=base.replace(second=2)),
        FakeMessage("Too many messages", source_created_at=base.replace(second=3)),
    ]
    text = build_intent_text(messages)
    assert text == "First question Follow up question Another member note"


def test_detect_language_falls_back_for_unsupported_code(monkeypatch):
    monkeypatch.setattr("langdetect.detect", lambda _text: "pt")
    assert detect_language("Olá, preciso de ajuda", ["nl", "en", "de", "fr", "es"]) == "en"


def test_detect_language_returns_supported_code(monkeypatch):
    monkeypatch.setattr("langdetect.detect", lambda _text: "nl")
    assert detect_language("Zijn er toiletten?", ["nl", "en", "de", "fr", "es"]) == "nl"
