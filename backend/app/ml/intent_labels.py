from __future__ import annotations

from datetime import datetime
from typing import Protocol

INTENT_SLUGS: list[str] = [
    "refund",
    "shipping",
    "order_status",
    "account",
    "pricing",
    "product_info",
    "complaint",
    "technical_support",
    "general",
]

FESTIVAL_INTENT_SLUGS: list[str] = [
    "tickets",
    "travel",
    "lineup",
    "rules",
    "refund",
    "complaint",
    "product_info",
    "general",
]

INTENT_PROFILES: dict[str, list[str]] = {
    "ecommerce": INTENT_SLUGS,
    "festival": FESTIVAL_INTENT_SLUGS,
}

INTENT_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "nl": {
        "refund": "terugbetaling of restitutie",
        "shipping": "levering of verzending",
        "order_status": "bestel- of ticketstatus",
        "account": "account of profiel",
        "pricing": "prijs of kosten",
        "product_info": "informatie over het product of evenement",
        "complaint": "een klacht of ontevredenheid",
        "technical_support": "technische ondersteuning",
        "general": "een algemene vraag",
        "tickets": "tickets of toegangsbewijzen",
        "travel": "reizen naar het evenement of vervoer",
        "lineup": "artiesten, line-up of programma",
        "rules": "festivalregels of wat wel of niet mag",
    },
    "en": {
        "refund": "a refund or reimbursement",
        "shipping": "shipping or delivery",
        "order_status": "order or ticket status",
        "account": "an account or profile",
        "pricing": "pricing or cost",
        "product_info": "product or event information",
        "complaint": "a complaint or dissatisfaction",
        "technical_support": "technical support",
        "general": "a general question",
        "tickets": "tickets or entry passes",
        "travel": "travel to the event or transport",
        "lineup": "artists, lineup or schedule",
        "rules": "festival rules or what is allowed",
    },
    "de": {
        "refund": "eine Rückerstattung oder Erstattung",
        "shipping": "Versand oder Lieferung",
        "order_status": "Bestell- oder Ticketstatus",
        "account": "ein Konto oder Profil",
        "pricing": "Preis oder Kosten",
        "product_info": "Produkt- oder Veranstaltungsinformationen",
        "complaint": "eine Beschwerde oder Unzufriedenheit",
        "technical_support": "technischer Support",
        "general": "eine allgemeine Frage",
        "tickets": "Tickets oder Eintrittskarten",
        "travel": "Anreise zum Event oder Transport",
        "lineup": "Künstler, Line-up oder Programm",
        "rules": "Festivalregeln oder was erlaubt ist",
    },
    "fr": {
        "refund": "un remboursement",
        "shipping": "la livraison ou l'expédition",
        "order_status": "le statut de commande ou de billet",
        "account": "un compte ou profil",
        "pricing": "le prix ou le coût",
        "product_info": "des informations sur le produit ou l'événement",
        "complaint": "une plainte ou insatisfaction",
        "technical_support": "le support technique",
        "general": "une question générale",
        "tickets": "des billets ou des entrées",
        "travel": "se rendre à l'événement ou le transport",
        "lineup": "artistes, line-up ou programme",
        "rules": "règles du festival ou ce qui est autorisé",
    },
    "es": {
        "refund": "un reembolso o devolución",
        "shipping": "envío o entrega",
        "order_status": "estado del pedido o entrada",
        "account": "una cuenta o perfil",
        "pricing": "precio o coste",
        "product_info": "información del producto o evento",
        "complaint": "una queja o insatisfacción",
        "technical_support": "soporte técnico",
        "general": "una pregunta general",
        "tickets": "entradas o tickets",
        "travel": "viajar al evento o transporte",
        "lineup": "artistas, cartel o programación",
        "rules": "normas del festival o qué está permitido",
    },
}


def resolve_intent_slugs(profile: str, default_slugs: list[str]) -> list[str]:
    """Resolve the global intent taxonomy from INTENT_PROFILE or INTENT_LABELS."""
    if profile and profile in INTENT_PROFILES:
        return INTENT_PROFILES[profile]
    return default_slugs


HYPOTHESIS_TEMPLATES: dict[str, str] = {
    "nl": "De klantvraag gaat over {}.",
    "en": "The customer message is about {}.",
    "de": "Die Kundennachricht handelt von {}.",
    "fr": "Le message du client concerne {}.",
    "es": "El mensaje del cliente trata sobre {}.",
}

COMPLAINT_SECOND_LABEL_MARGIN = 0.08
INTENT_TEXT_MAX_CHARS = 512
INTENT_EXTRA_MEMBER_MESSAGES = 2


class IntentMessage(Protocol):
    from_agent: bool
    content: str
    source_created_at: datetime | None
    created_at: datetime


def detect_language(text: str, supported: list[str] | None = None) -> str:
    supported_langs = supported or list(HYPOTHESIS_TEMPLATES.keys())
    fallback = "en" if "en" in supported_langs else supported_langs[0]
    cleaned = text.strip()
    if not cleaned:
        return fallback

    try:
        from langdetect import DetectorFactory, detect

        DetectorFactory.seed = 0
        detected = detect(cleaned).lower()
    except Exception:
        return fallback

    if detected in supported_langs:
        return detected
    return fallback


def build_intent_text(messages: list[IntentMessage]) -> str:
    member_messages = sorted(
        (m for m in messages if not m.from_agent and m.content.strip()),
        key=lambda m: (m.source_created_at or m.created_at, id(m)),
    )
    if not member_messages:
        return ""

    selected = member_messages[: 1 + INTENT_EXTRA_MEMBER_MESSAGES]
    combined = " ".join(message.content.strip() for message in selected).strip()
    return combined[:INTENT_TEXT_MAX_CHARS]


def descriptions_for_language(language: str, slugs: list[str] | None = None) -> dict[str, str]:
    slug_list = slugs or INTENT_SLUGS
    lang = language if language in INTENT_DESCRIPTIONS else "en"
    descriptions = INTENT_DESCRIPTIONS[lang]
    return {slug: descriptions.get(slug, descriptions["general"]) for slug in slug_list}


def reverse_description_map(language: str, slugs: list[str] | None = None) -> dict[str, str]:
    return {description: slug for slug, description in descriptions_for_language(language, slugs).items()}


def hypothesis_template_for_language(language: str, override: str = "") -> str:
    if override.strip():
        return override.strip()
    return HYPOTHESIS_TEMPLATES.get(language, HYPOTHESIS_TEMPLATES["en"])


def resolve_intent(
    scores_by_slug: dict[str, float],
    threshold: float,
    complaint_min_score: float,
    sentiment_stars: int | None = None,
) -> tuple[str, float]:
    if not scores_by_slug:
        return "general", 0.0

    ranked = sorted(scores_by_slug.items(), key=lambda item: item[1], reverse=True)
    top_slug, top_score = ranked[0]

    if top_slug == "complaint" and sentiment_stars is not None and sentiment_stars <= 2:
        return top_slug, top_score

    if top_slug == "complaint" and (sentiment_stars is None or sentiment_stars >= 3):
        if top_score < complaint_min_score:
            if len(ranked) > 1:
                second_slug, second_score = ranked[1]
                if top_score - second_score <= COMPLAINT_SECOND_LABEL_MARGIN:
                    return second_slug, second_score
            return "general", top_score

    if top_score < threshold:
        return "general", top_score

    return top_slug, top_score
