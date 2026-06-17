"""Slack slash commands and interactive modals for starting report pipelines."""

from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response

from app.auth.google_oauth import is_allowed_email
from app.config import get_settings
from app.integrations.momants_client import get_momants_client
from app.integrations.slack_client import SlackNotifier, get_slack_notifier
from app.integrations.slack_verification import verify_slack_signature
from app.services.pipeline_launcher import (
    PipelineBusyError,
    PipelineConfigError,
    launch_reanalyze,
    launch_run,
)

logger = logging.getLogger(__name__)

router = APIRouter()

SLACK_COMMAND_REPORT = "/report"
SLACK_COMMAND_REANALYZE = "/report-reanalyze"
CALLBACK_REPORT_RUN = "report_run"
CALLBACK_REPORT_REANALYZE = "report_reanalyze"
MAX_AGENT_OPTIONS = 100


def _require_slack_commands_enabled() -> None:
    if not get_settings().slack_commands_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Slack commands are not configured",
        )


async def _verify_slack_request(request: Request, body: bytes) -> None:
    settings = get_settings()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    if not verify_slack_signature(
        settings.slack_signing_secret,
        timestamp=timestamp,
        body=body,
        signature=signature,
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Slack signature")


def _parse_form_body(body: bytes) -> dict[str, str]:
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: values[0] if values else "" for key, values in parsed.items()}


def _load_agent_options() -> list[dict]:
    client = get_momants_client()
    try:
        agents = client.list_agents()
    finally:
        client.close()

    options = [
        {
            "text": {"type": "plain_text", "text": (agent.get("name") or f"Agent {agent['id'][:8]}")[:75]},
            "value": agent["id"],
        }
        for agent in agents
    ]
    options.sort(key=lambda item: item["text"]["text"].lower())
    return options[:MAX_AGENT_OPTIONS]


def _build_agent_modal(*, callback_id: str, title: str, submit_label: str, agents: list[dict]) -> dict:
    if not agents:
        return {
            "type": "modal",
            "callback_id": callback_id,
            "title": {"type": "plain_text", "text": title[:24]},
            "close": {"type": "plain_text", "text": "Close"},
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ":warning: No agents found. Check the Momants API configuration.",
                    },
                }
            ],
        }

    return {
        "type": "modal",
        "callback_id": callback_id,
        "title": {"type": "plain_text", "text": title[:24]},
        "submit": {"type": "plain_text", "text": submit_label[:24]},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "agent_block",
                "label": {"type": "plain_text", "text": "Agent"},
                "element": {
                    "type": "static_select",
                    "action_id": "agent_select",
                    "placeholder": {"type": "plain_text", "text": "Select an agent"},
                    "options": agents,
                },
            }
        ],
    }


def _modal_config_for_command(command: str) -> tuple[str, str, str]:
    if command == SLACK_COMMAND_REPORT:
        return CALLBACK_REPORT_RUN, "Start report", "Start"
    if command == SLACK_COMMAND_REANALYZE:
        return CALLBACK_REPORT_REANALYZE, "Reanalyze report", "Reanalyze"
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported command: {command}")


def _extract_agent_id(view: dict) -> str:
    values = view.get("state", {}).get("values", {})
    agent_block = values.get("agent_block", {})
    agent_select = agent_block.get("agent_select", {})
    selected = agent_select.get("selected_option")
    if not selected or not selected.get("value"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Agent selection is required")
    return str(selected["value"])


def _agent_label(view: dict) -> str:
    values = view.get("state", {}).get("values", {})
    selected = values.get("agent_block", {}).get("agent_select", {}).get("selected_option", {})
    text = selected.get("text", {}).get("text")
    return text or "selected agent"


def _authorize_slack_user(notifier: SlackNotifier, user_id: str) -> str:
    email = notifier.lookup_user_email(user_id)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not resolve your Slack email address",
        )
    if not is_allowed_email(email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your email domain is not authorized to start reports",
        )
    return email


@router.post("/commands")
async def slack_commands(request: Request) -> Response:
    _require_slack_commands_enabled()
    body = await request.body()
    await _verify_slack_request(request, body)

    form = _parse_form_body(body)
    command = form.get("command", "").strip()
    trigger_id = form.get("trigger_id", "").strip()
    if not trigger_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing trigger_id")

    callback_id, title, submit_label = _modal_config_for_command(command)
    notifier = get_slack_notifier()
    if notifier is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Slack bot token is not configured")

    try:
        agents = _load_agent_options()
    except Exception as exc:
        logger.exception("Failed to load agents for Slack modal")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not load agents: {exc}",
        ) from exc

    view = _build_agent_modal(
        callback_id=callback_id,
        title=title,
        submit_label=submit_label,
        agents=agents,
    )
    try:
        notifier.open_modal(trigger_id, view)
    except Exception as exc:
        logger.exception("Failed to open Slack modal for command %s", command)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to open Slack modal: {exc}",
        ) from exc

    return Response(status_code=status.HTTP_200_OK)


@router.post("/interactions")
async def slack_interactions(request: Request) -> JSONResponse:
    _require_slack_commands_enabled()
    body = await request.body()
    await _verify_slack_request(request, body)

    form = _parse_form_body(body)
    payload_raw = form.get("payload", "")
    if not payload_raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing interaction payload")

    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid interaction payload") from exc

    if payload.get("type") != "view_submission":
        return JSONResponse(content={})

    callback_id = payload.get("view", {}).get("callback_id", "")
    user_id = payload.get("user", {}).get("id", "")
    channel_id = payload.get("container", {}).get("channel_id") or payload.get("channel", {}).get("id", "")

    notifier = get_slack_notifier()
    if notifier is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Slack bot token is not configured")

    email = _authorize_slack_user(notifier, user_id)
    agent_id = _extract_agent_id(payload.get("view", {}))
    agent_name = _agent_label(payload.get("view", {}))

    try:
        if callback_id == CALLBACK_REPORT_RUN:
            launch_run(agent_id, email)
            confirmation = (
                f":hourglass_flowing_sand: Started full analysis for *{agent_name}*. "
                "I'll DM you updates as each stage completes."
            )
        elif callback_id == CALLBACK_REPORT_REANALYZE:
            launch_reanalyze(agent_id, email)
            confirmation = (
                f":hourglass_flowing_sand: Started reanalyze for *{agent_name}* "
                "(sentiment + insights). I'll DM you updates as each stage completes."
            )
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported callback: {callback_id}")
    except PipelineBusyError:
        if channel_id and user_id:
            notifier.post_ephemeral(
                channel_id,
                user_id,
                f":warning: A report run is already in progress for *{agent_name}*. Try again when it finishes.",
            )
        return JSONResponse(content={"response_action": "clear"})
    except PipelineConfigError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    if channel_id and user_id:
        try:
            notifier.post_ephemeral(channel_id, user_id, confirmation)
        except Exception:
            logger.exception("Failed to post Slack confirmation for agent %s", agent_id)

    return JSONResponse(content={"response_action": "clear"})
