import re

QUESTION_STARTERS = re.compile(
    r"^\s*(hoe|wat|waar|wanneer|waarom|kan|kunnen|krijg|mag|is|zijn|heb|heeft|"
    r"how|what|where|when|why|can|could|do|does|is|are|will|would)\b",
    re.IGNORECASE,
)

# Genuine customer questions are short. Forwarded emails, pasted marketing blasts and
# spam blobs are long and carry tell-tale markers — reject them so they never reach the
# FAQ clusters, unanswered analysis or question metrics.
MAX_QUESTION_LENGTH = 300

_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Email-forward / newsletter / marketing boilerplate markers.
_JUNK_MARKERS = re.compile(
    r"(?im)^\s*(title|from|to|subject|sent|date|cc|bcc)\s*:"
    r"|--+\s*forwarded"
    r"|\bunsubscribe\b"
    r"|view (this|in) (your )?browser",
)
_QUOTE_START = re.compile(r"(?im)^\s*(>|on .+wrote:|from\s*:|sent\s*:|-{2,}\s*forwarded)")


def strip_quoted_tail(text: str) -> str:
    """Drop everything from the first quoted/forwarded line onward (email reply chains)."""
    kept: list[str] = []
    for line in text.splitlines():
        if _QUOTE_START.match(line):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def is_question(text: str, min_length: int = 8) -> bool:
    if not text:
        return False

    cleaned = strip_quoted_tail(text).strip()
    if not (min_length <= len(cleaned) <= MAX_QUESTION_LENGTH):
        return False

    # Forwarded emails, links and marketing blobs are not customer questions.
    if _URL_RE.search(cleaned) or _EMAIL_RE.search(cleaned) or _JUNK_MARKERS.search(cleaned):
        return False

    if "?" in cleaned:
        return True
    return bool(QUESTION_STARTERS.match(cleaned))
