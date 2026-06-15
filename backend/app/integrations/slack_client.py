"""Slack notifications for on-request rapport runs.

DMs the user who requested a run at each milestone. All public helpers are
best-effort: if Slack is disabled, misconfigured, or the API call fails, they
log and return False instead of raising, so a Slack problem never breaks the
pipeline.
"""

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

SLACK_API_BASE = "https://slack.com/api"

# Milestones reported to the requesting user, in order.
MILESTONE_STARTED = "started"
MILESTONE_INGEST_STARTED = "ingest_started"
MILESTONE_INGEST_DONE = "ingest_done"
MILESTONE_INSIGHTS_STARTED = "insights_started"
MILESTONE_INSIGHTS_DONE = "insights_done"
MILESTONE_PDF_READY = "pdf_ready"
MILESTONE_FAILED = "failed"


class SlackNotifier:
    """Thin wrapper over the Slack Web API for looking up users and DMing them."""

    def __init__(self, token: str, timeout_seconds: float = 10.0) -> None:
        self._token = token
        self._timeout = timeout_seconds

    def _post(self, method: str, payload: dict) -> dict:
        response = httpx.post(
            f"{SLACK_API_BASE}/{method}",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json=payload,
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack API '{method}' failed: {data.get('error', 'unknown_error')}")
        return data

    def lookup_user_id(self, email: str) -> str | None:
        """Resolve a Slack user id from an email address, or None if not found."""
        response = httpx.get(
            f"{SLACK_API_BASE}/users.lookupByEmail",
            headers={"Authorization": f"Bearer {self._token}"},
            params={"email": email},
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            logger.warning("Slack user lookup failed for %s: %s", email, data.get("error"))
            return None
        return data.get("user", {}).get("id")

    def dm(self, email: str, text: str) -> bool:
        """Send a direct message to the user identified by email. Returns success."""
        user_id = self.lookup_user_id(email)
        if not user_id:
            return False
        self._post("chat.postMessage", {"channel": user_id, "text": text})
        return True


def _get_notifier() -> SlackNotifier | None:
    settings = get_settings()
    if not settings.slack_enabled:
        return None
    return SlackNotifier(settings.slack_bot_token.strip(), settings.slack_timeout_seconds)


def _format_message(
    agent_id: str,
    milestone: str,
    *,
    agent_name: str | None,
    link: str | None,
    error: str | None,
) -> str:
    label = agent_name.strip() if agent_name and agent_name.strip() else f"agent {agent_id[:8]}"
    if milestone == MILESTONE_STARTED:
        return f":hourglass_flowing_sand: Rapport run *started* for *{label}*. I'll keep you posted."
    if milestone == MILESTONE_INGEST_STARTED:
        return f":hourglass_flowing_sand: *Ingest started* for *{label}*."
    if milestone == MILESTONE_INGEST_DONE:
        return f":inbox_tray: *Ingest completed* for *{label}*."
    if milestone == MILESTONE_INSIGHTS_STARTED:
        return f":mag: *Insights started* for *{label}*."
    if milestone == MILESTONE_INSIGHTS_DONE:
        return f":bar_chart: *Insights completed* for *{label}*."
    if milestone == MILESTONE_PDF_READY:
        suffix = f" Download the PDF: {link}" if link else ""
        return f":white_check_mark: *Rapport finished* for *{label}*.{suffix}"
    if milestone == MILESTONE_FAILED:
        detail = f" Error: {error}" if error else ""
        return f":x: Rapport run *failed* for *{label}*.{detail}"
    return f"Rapport update for *{label}*: {milestone}"


def notify_milestone(
    email: str | None,
    agent_id: str,
    milestone: str,
    *,
    agent_name: str | None = None,
    link: str | None = None,
    error: str | None = None,
) -> bool:
    """Best-effort DM to `email` about a rapport milestone.

    Returns True if the message was sent. Never raises: Slack failures are logged
    and swallowed so they cannot break the pipeline run.
    """
    if not email:
        return False
    notifier = _get_notifier()
    if notifier is None:
        logger.debug("Slack disabled; skipping %s notification for %s", milestone, email)
        return False
    text = _format_message(agent_id, milestone, agent_name=agent_name, link=link, error=error)
    try:
        return notifier.dm(email, text)
    except Exception:
        logger.exception("Failed to send Slack %s notification to %s", milestone, email)
        return False
