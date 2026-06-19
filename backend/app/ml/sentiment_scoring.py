"""Composite sentiment scoring: emotion-aware star adjustment on top of polarity."""

from __future__ import annotations

POSITIVE_EMOTIONS = frozenset(
    {
        "joy",
        "excitement",
        "amusement",
        "love",
        "gratitude",
        "optimism",
        "curiosity",
        "desire",
        "approval",
        "admiration",
        "caring",
        "pride",
        "relief",
    }
)

NEGATIVE_EMOTIONS = frozenset(
    {
        "anger",
        "disappointment",
        "disgust",
        "fear",
        "sadness",
        "annoyance",
        "remorse",
        "embarrassment",
        "grief",
        "nervousness",
    }
)

# Emotions that lift neutral polarity without requiring a strong emotion score.
LIFT_NEUTRAL_EMOTIONS = frozenset({"curiosity", "desire", "excitement", "joy", "gratitude"})

EMOTION_SCORE_MIN = 0.25


def stars_to_label(stars: int) -> str:
    if stars >= 4:
        return "POSITIVE"
    if stars <= 2:
        return "NEGATIVE"
    return "NEUTRAL"


def stars_to_polarity(stars: int) -> str:
    if stars >= 4:
        return "positive"
    if stars <= 2:
        return "negative"
    return "neutral"


def adjust_stars_with_emotions(
    stars: int,
    emotions: list[dict],
    *,
    min_stars: int = 1,
    max_stars: int = 5,
) -> int:
    """Nudge star rating using the top detected emotion when polarity is ambiguous."""
    if not emotions or stars < min_stars or stars > max_stars:
        return stars

    top = emotions[0]
    label = str(top.get("label", "")).lower()
    score = float(top.get("score", 0.0))
    if not label or score < EMOTION_SCORE_MIN:
        return stars

    adjusted = stars

    if label in NEGATIVE_EMOTIONS and adjusted >= 3:
        adjusted -= 1
    elif label in POSITIVE_EMOTIONS:
        if adjusted <= 3 and label in LIFT_NEUTRAL_EMOTIONS:
            adjusted += 1
        elif adjusted <= 2:
            adjusted += 1

    return max(min_stars, min(max_stars, adjusted))
