from app.utils.sentiment_report import (
    SentimentDistribution,
    build_sentiment_callout,
    build_sentiment_headline,
    build_sentiment_page_html,
    build_sentiment_paragraphs,
    distribution_from_counts,
)


def test_build_sentiment_headline_neutral_to_positive():
    dist = distribution_from_counts({"positive": 100, "neutral": 800, "negative": 50})
    assert build_sentiment_headline(dist) == "Overwegend neutraal tot positief"


def test_build_sentiment_headline_mostly_negative():
    dist = distribution_from_counts({"positive": 50, "neutral": 100, "negative": 400})
    assert build_sentiment_headline(dist) == "Overwegend negatief"


def test_build_sentiment_paragraphs_include_counts():
    dist = SentimentDistribution(positive=1422, neutral=48531, negative=7249)
    para_1, para_2 = build_sentiment_paragraphs(dist, [])
    assert "48.531" in para_1
    assert "1.422" in para_1
    assert "7.249" in para_2


def test_build_sentiment_callout_improving_trajectory():
    dist = SentimentDistribution(positive=10, neutral=80, negative=5)
    text = build_sentiment_callout(dist, {"improving_pct": 42}, dominant_mood="nieuwsgierigheid")
    assert "42%" in text
    assert "verbetert" in text


def test_build_sentiment_callout_low_negative_share():
    dist = SentimentDistribution(positive=100, neutral=800, negative=50)
    text = build_sentiment_callout(dist, {}, dominant_mood=None)
    assert "Campagne-gerelateerde frictie" in text


def test_build_sentiment_page_html_uses_slide_card_layout():
    dist = SentimentDistribution(positive=100, neutral=800, negative=50)
    html = build_sentiment_page_html(dist, [], {})
    assert "sentiment-stats" in html
    assert "sentiment-main" in html
    assert "sentiment-copy" in html
    assert "Positief" in html
    assert "Neutraal" in html
    assert "Negatief" in html
    assert "Conclusie" not in html
    assert "#6BAA4F" in html
