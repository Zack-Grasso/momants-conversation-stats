from __future__ import annotations

import httpx

from app.config import get_settings

# 1440×830 slide deck — landscape 16:9 pages for Gotenberg/Chromium.
GOTENBERG_HTML_PDF_OPTIONS = {
    "printBackground": "true",
    "preferCssPageSize": "true",
    "waitDelay": "3s",
    "paperWidth": "15",
    "paperHeight": "8.645833",
    "marginTop": "0",
    "marginBottom": "0",
    "marginLeft": "0",
    "marginRight": "0",
}


def html_to_pdf(html: str) -> bytes:
    settings = get_settings()
    url = f"{settings.gotenberg_url.rstrip('/')}/forms/chromium/convert/html"
    files = {"files": ("index.html", html.encode("utf-8"), "text/html")}
    with httpx.Client(timeout=settings.gotenberg_timeout_seconds) as client:
        response = client.post(url, files=files, data=GOTENBERG_HTML_PDF_OPTIONS)
        response.raise_for_status()
        return response.content
