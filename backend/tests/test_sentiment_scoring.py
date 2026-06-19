from app.config import Settings
from app.ml.intent_labels import FESTIVAL_INTENT_SLUGS, resolve_intent_slugs
from app.ml.sentiment_scoring import adjust_stars_with_emotions, stars_to_label, stars_to_polarity


def test_adjust_stars_lifts_neutral_curiosity():
    stars = adjust_stars_with_emotions(3, [{"label": "curiosity", "score": 0.6}])
    assert stars == 4


def test_adjust_stars_lifts_neutral_desire():
    stars = adjust_stars_with_emotions(3, [{"label": "desire", "score": 0.5}])
    assert stars == 4


def test_adjust_stars_lowers_neutral_anger():
    stars = adjust_stars_with_emotions(3, [{"label": "anger", "score": 0.7}])
    assert stars == 2


def test_adjust_stars_keeps_positive_when_emotion_weak():
    stars = adjust_stars_with_emotions(4, [{"label": "curiosity", "score": 0.1}])
    assert stars == 4


def test_adjust_stars_clamps_to_bounds():
    stars = adjust_stars_with_emotions(1, [{"label": "anger", "score": 0.9}])
    assert stars == 1


def test_stars_to_label_and_polarity():
    assert stars_to_label(5) == "POSITIVE"
    assert stars_to_label(3) == "NEUTRAL"
    assert stars_to_label(1) == "NEGATIVE"
    assert stars_to_polarity(4) == "positive"
    assert stars_to_polarity(2) == "negative"


def test_settings_intent_slug_list_uses_global_profile():
    settings = Settings(intent_profile="festival")
    assert settings.intent_slug_list == FESTIVAL_INTENT_SLUGS


def test_settings_intent_slug_list_falls_back_to_intent_labels():
    settings = Settings(intent_profile="", intent_labels="refund,general")
    assert settings.intent_slug_list == ["refund", "general"]


def test_resolve_intent_slugs():
    assert resolve_intent_slugs("festival", ["general"]) == FESTIVAL_INTENT_SLUGS
    assert resolve_intent_slugs("", ["refund", "general"]) == ["refund", "general"]
