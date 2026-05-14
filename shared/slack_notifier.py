"""
Slack Notifier — Per-tenant Slack integration for incident lifecycle.

Sends rich Block Kit messages to the correct Slack workspace based on
each tenant's stored credentials (bot_token, channels).

Uses httpx (already a project dependency) to call Slack Web API directly,
avoiding the need for the slack_sdk package.

Message types:
  - incident_triaged:  New incident triaged → incidents_channel
  - diagnosis_complete: Root cause found → incidents_channel
  - approval_request:  Human approval needed → approvals_channel
  - incident_resolved: Incident resolved → incidents_channel
  - incident_escalated: Escalated to humans → escalation_channel
"""
from __future__ import annotations

import httpx

from shared.credential_store import get_credentials
from shared.logger import get_logger

log = get_logger("slack-notifier")

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"


async def _get_slack_config(tenant_id: str):
    """Load tenant's Slack credentials. Returns None if not configured or disabled."""
    creds = await get_credentials(tenant_id)
    if not creds or not creds.slack:
        log.debug("slack_not_configured", tenant_id=tenant_id)
        return None
    if not creds.slack.enabled:
        log.debug("slack_disabled", tenant_id=tenant_id)
        return None
    return creds.slack


async def _post_message(bot_token: str, channel: str, text: str, blocks: list | None = None) -> dict | None:
    """Send a message to Slack using the Web API."""
    # Slack API expects channel name without '#' prefix
    channel = channel.lstrip("#")

    headers = {
        "Authorization": f"Bearer {bot_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(SLACK_POST_MESSAGE_URL, headers=headers, json=payload)
            data = resp.json()
            if not data.get("ok"):
                log.error("slack_api_error", error=data.get("error"), channel=channel)
                return None
            log.info("slack_message_sent", channel=channel, ts=data.get("ts"))
            return data
    except Exception as e:
        log.error("slack_send_failed", error=str(e), channel=channel)
        return None


# ── Severity → emoji / color mapping ──────────────────────────────

SEVERITY_EMOJI = {"P1": "🔴", "P2": "🟠", "P3": "🔵", "P4": "⚪"}
SEVERITY_COLOR = {"P1": "#ef4444", "P2": "#f59e0b", "P3": "#3b82f6", "P4": "#6b7280"}
STATUS_EMOJI = {
    "triaging": "🔍", "diagnosing": "🧪", "remediating": "🔧",
    "awaiting_approval": "⏳", "resolved": "✅", "escalated": "🚨",
}


# ── Public notification functions ─────────────────────────────────

async def notify_incident_triaged(
    tenant_id: str,
    incident_id: str,
    alert_name: str,
    service: str,
    severity: str,
    triage_summary: str,
    confidence: float,
    matched_runbook: str | None = None,
) -> None:
    """Send a notification when a new incident has been triaged."""
    slack = await _get_slack_config(tenant_id)
    if not slack:
        return

    sev_emoji = SEVERITY_EMOJI.get(severity, "⚪")
    color = SEVERITY_COLOR.get(severity, "#6b7280")

    # Use escalation_channel for P1 if configured
    channel = slack.incidents_channel
    if severity == "P1" and slack.escalation_channel:
        channel = slack.escalation_channel

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{sev_emoji} {severity} Incident — {alert_name}", "emoji": True}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Service:*\n`{service}`"},
                {"type": "mrkdwn", "text": f"*Severity:*\n{sev_emoji} {severity}"},
                {"type": "mrkdwn", "text": f"*Confidence:*\n{confidence:.0%}"},
                {"type": "mrkdwn", "text": f"*Runbook:*\n{matched_runbook or 'None matched'}"},
            ]
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Triage Summary:*\n{triage_summary[:500]}"}
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"🆔 `{incident_id}` • Tenant: `{tenant_id}` • Status: 🔍 Triaged → Diagnosing"}
            ]
        },
        {"type": "divider"},
    ]

    fallback_text = f"{sev_emoji} {severity} Incident: {alert_name} on {service} — {triage_summary[:100]}"
    await _post_message(slack.bot_token, channel, fallback_text, blocks)


async def notify_diagnosis_complete(
    tenant_id: str,
    incident_id: str,
    alert_name: str,
    service: str,
    severity: str,
    root_cause: str,
    diagnosis_summary: str,
    evidence_count: int,
) -> None:
    """Send a notification when root cause analysis is complete."""
    slack = await _get_slack_config(tenant_id)
    if not slack:
        return

    sev_emoji = SEVERITY_EMOJI.get(severity, "⚪")

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🧪 Diagnosis Complete — {alert_name}", "emoji": True}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Service:*\n`{service}`"},
                {"type": "mrkdwn", "text": f"*Severity:*\n{sev_emoji} {severity}"},
                {"type": "mrkdwn", "text": f"*Evidence Collected:*\n{evidence_count} pieces"},
            ]
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Root Cause:*\n{root_cause[:500]}"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Diagnosis Summary:*\n{diagnosis_summary[:500]}"}
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"🆔 `{incident_id}` • Status: 🔧 Moving to Remediation"}
            ]
        },
        {"type": "divider"},
    ]

    fallback_text = f"🧪 Diagnosis: {alert_name} — Root cause: {root_cause[:100]}"
    await _post_message(slack.bot_token, slack.incidents_channel, fallback_text, blocks)


async def notify_approval_request(
    tenant_id: str,
    incident_id: str,
    request_id: str,
    action_summary: str,
    risk_level: str,
    alert_name: str = "",
    service: str = "",
) -> None:
    """Post an approval request to the approvals channel."""
    slack = await _get_slack_config(tenant_id)
    if not slack:
        return

    risk_emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(risk_level, "⚪")
    dashboard_url = f"http://localhost:5173"  # TODO: make configurable

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"⏳ Approval Required — {risk_emoji} {risk_level.upper()} Risk", "emoji": True}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Proposed Action:*\n```{action_summary[:400]}```"}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Incident:*\n`{incident_id[:12]}...`"},
                {"type": "mrkdwn", "text": f"*Risk Level:*\n{risk_emoji} {risk_level.upper()}"},
                {"type": "mrkdwn", "text": f"*Service:*\n`{service or 'N/A'}`"},
                {"type": "mrkdwn", "text": f"*Alert:*\n{alert_name or 'N/A'}"},
            ]
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Approve via API:*\n"
                    f"```POST http://localhost:8000/approvals/{request_id}/approve?approved_by=slack```\n\n"
                    f"*Or use the Dashboard:* <{dashboard_url}|Open Approvals →>"
                )
            }
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"Request ID: `{request_id}` • Tenant: `{tenant_id}`"}
            ]
        },
        {"type": "divider"},
    ]

    fallback_text = f"⏳ Approval needed ({risk_level}): {action_summary[:100]}"
    await _post_message(slack.bot_token, slack.approvals_channel, fallback_text, blocks)


async def notify_incident_resolved(
    tenant_id: str,
    incident_id: str,
    alert_name: str,
    service: str,
    severity: str,
    resolution_summary: str,
    mttr_seconds: int | None = None,
) -> None:
    """Send a notification when an incident is resolved."""
    slack = await _get_slack_config(tenant_id)
    if not slack:
        return

    mttr_text = "N/A"
    if mttr_seconds is not None:
        minutes = mttr_seconds // 60
        seconds = mttr_seconds % 60
        mttr_text = f"{minutes}m {seconds}s"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"✅ Incident Resolved — {alert_name}", "emoji": True}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Service:*\n`{service}`"},
                {"type": "mrkdwn", "text": f"*Severity:*\n{SEVERITY_EMOJI.get(severity, '⚪')} {severity}"},
                {"type": "mrkdwn", "text": f"*MTTR:*\n⏱️ {mttr_text}"},
                {"type": "mrkdwn", "text": f"*Status:*\n✅ Resolved"},
            ]
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Resolution:*\n{resolution_summary[:500]}"}
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"🆔 `{incident_id}` • Tenant: `{tenant_id}`"}
            ]
        },
        {"type": "divider"},
    ]

    fallback_text = f"✅ Resolved: {alert_name} on {service} (MTTR: {mttr_text})"
    await _post_message(slack.bot_token, slack.incidents_channel, fallback_text, blocks)


async def notify_incident_escalated(
    tenant_id: str,
    incident_id: str,
    alert_name: str,
    service: str,
    severity: str,
    reason: str,
) -> None:
    """Send a notification when an incident is escalated to humans."""
    slack = await _get_slack_config(tenant_id)
    if not slack:
        return

    channel = slack.escalation_channel or slack.incidents_channel

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🚨 Incident Escalated — {alert_name}", "emoji": True}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Service:*\n`{service}`"},
                {"type": "mrkdwn", "text": f"*Severity:*\n{SEVERITY_EMOJI.get(severity, '⚪')} {severity}"},
            ]
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Reason:*\n{reason[:500]}"}
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"🆔 `{incident_id}` • ⚠️ Human intervention required"}
            ]
        },
        {"type": "divider"},
    ]

    fallback_text = f"🚨 Escalated: {alert_name} on {service} — {reason[:100]}"
    await _post_message(slack.bot_token, channel, fallback_text, blocks)
