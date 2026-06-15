import json
from typing import Any


def normalize_message_content(content: Any) -> str:
    if content is None:
        return ""

    if isinstance(content, str):
        text = content.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                parsed = json.loads(text)
                return normalize_message_content(parsed)
            except json.JSONDecodeError:
                return text
        return text

    if isinstance(content, list):
        parts = [normalize_message_content(item) for item in content]
        return " ".join(part for part in parts if part)

    if isinstance(content, dict):
        for key in ("text", "body", "content", "message", "caption", "title"):
            if key in content:
                normalized = normalize_message_content(content[key])
                if normalized:
                    return normalized
        parts = []
        for value in content.values():
            normalized = normalize_message_content(value)
            if normalized:
                parts.append(normalized)
        return " ".join(parts)

    return str(content).strip()
