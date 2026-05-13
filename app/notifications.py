import asyncio
import httpx
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from app.config import settings
from app.models import RiskScore, RiskLevel

# Emoji and colour per risk level
_LEVEL_META = {
    RiskLevel.CRITICAL: {"emoji": "🚨", "color": "#ef4444", "label": "CRITICAL"},
    RiskLevel.HIGH:     {"emoji": "⚠️",  "color": "#f97316", "label": "HIGH"},
    RiskLevel.MEDIUM:   {"emoji": "🟡", "color": "#eab308", "label": "MEDIUM"},
    RiskLevel.LOW:      {"emoji": "✅", "color": "#22c55e", "label": "LOW"},
}


def _build_slack_payload(
    repo: str,
    commit_sha: str,
    author: str,
    branch: str,
    risk: RiskScore,
) -> dict:
    """Build a Slack Block Kit message payload."""
    meta = _LEVEL_META.get(risk.risk_level, _LEVEL_META[RiskLevel.MEDIUM])
    short_sha = commit_sha[:7]
    score_bar = _score_bar(risk.score)
    recs = "\n".join(f"• {r}" for r in risk.fix_recommendations[:3])

    return {
        "attachments": [
            {
                "color": meta["color"],
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"{meta['emoji']} DeployGuard — {meta['label']} Risk Deploy",
                        },
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Repo*\n`{repo}`"},
                            {"type": "mrkdwn", "text": f"*Branch*\n`{branch}`"},
                            {"type": "mrkdwn", "text": f"*Commit*\n`{short_sha}`"},
                            {"type": "mrkdwn", "text": f"*Author*\n{author}"},
                            {"type": "mrkdwn", "text": f"*Risk Score*\n`{risk.score:.3f}` {score_bar}"},
                            {"type": "mrkdwn", "text": f"*Blast Radius*\n{risk.blast_radius}"},
                        ],
                    },
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Claude's Reasoning*\n{risk.reasoning}",
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Fix Recommendations*\n{recs}",
                        },
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"<https://github.com/{repo}/commit/{commit_sha}|View commit on GitHub>",
                            }
                        ],
                    },
                ],
            }
        ]
    }


def _score_bar(score: float) -> str:
    """Tiny ASCII progress bar for the score, e.g. ████░░░░░░ 0.72"""
    filled = round(score * 10)
    return "█" * filled + "░" * (10 - filled)


async def notify_slack(
    repo: str,
    commit_sha: str,
    author: str,
    branch: str,
    risk: RiskScore,
) -> bool:
    """
    Send a Slack notification for HIGH or CRITICAL deploys.
    Returns True if sent successfully, False if skipped or failed.
    Completely non-fatal — never raises.
    """
    # Only notify on HIGH or CRITICAL
    if risk.risk_level not in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        return False

    webhook_url = getattr(settings, "slack_webhook_url", None)
    if not webhook_url:
        print("[notifications] SLACK_WEBHOOK_URL not set — skipping Slack notification")
        return False

    payload = _build_slack_payload(repo, commit_sha, author, branch, risk)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload)
        if resp.status_code == 200:
            print(f"[notifications] Slack notified — {risk.risk_level.value.upper()} deploy in {repo}")
            return True
        else:
            print(f"[notifications] Slack returned {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        print(f"[notifications] Slack notification failed (non-fatal): {e}")
        return False


def _build_email_html(
    repo: str,
    commit_sha: str,
    author: str,
    branch: str,
    risk: RiskScore,
) -> tuple[str, str]:
    """Return (subject, html_body) for the alert email."""
    meta = _LEVEL_META.get(risk.risk_level, _LEVEL_META[RiskLevel.MEDIUM])
    short_sha = commit_sha[:7]
    level_upper = meta["label"]
    color = meta["color"]
    score_bar = _score_bar(risk.score)
    recs_html = "".join(f"<li>{r}</li>" for r in risk.fix_recommendations[:3])

    subject = f"[DeployGuard] {meta['emoji']} {level_upper} risk deploy — {repo}@{short_sha}"

    html = f"""
    <div style="font-family:monospace;max-width:600px;margin:0 auto;background:#0a0b0d;color:#c8cdd8;padding:24px;border-radius:8px">
      <h2 style="color:{color};margin-bottom:16px">{meta['emoji']} DeployGuard — {level_upper} Risk Deploy</h2>
      <table style="width:100%;border-collapse:collapse;margin-bottom:16px">
        <tr><td style="padding:6px 12px 6px 0;color:#4a5168">Repo</td><td><code>{repo}</code></td></tr>
        <tr><td style="padding:6px 12px 6px 0;color:#4a5168">Branch</td><td><code>{branch}</code></td></tr>
        <tr><td style="padding:6px 12px 6px 0;color:#4a5168">Commit</td><td><code>{short_sha}</code></td></tr>
        <tr><td style="padding:6px 12px 6px 0;color:#4a5168">Author</td><td>{author}</td></tr>
        <tr><td style="padding:6px 12px 6px 0;color:#4a5168">Risk score</td><td><span style="color:{color}">{risk.score:.3f}</span> {score_bar}</td></tr>
        <tr><td style="padding:6px 12px 6px 0;color:#4a5168">Blast radius</td><td>{risk.blast_radius}</td></tr>
      </table>
      <div style="margin-bottom:12px">
        <strong style="color:#fff">Claude's reasoning</strong>
        <p style="margin-top:6px">{risk.reasoning}</p>
      </div>
      <div>
        <strong style="color:#fff">Fix recommendations</strong>
        <ul style="margin-top:6px;padding-left:20px">{recs_html}</ul>
      </div>
      <p style="margin-top:20px">
        <a href="https://github.com/{repo}/commit/{commit_sha}" style="color:#4f9cf9">View commit on GitHub →</a>
      </p>
    </div>
    """
    return subject, html


def _send_email_sync(subject: str, html_body: str) -> None:
    """
    Blocking SMTP send — must run in a thread pool, never directly in the event loop.
    Called exclusively via asyncio.get_event_loop().run_in_executor().
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user
    msg["To"] = settings.alert_email_to
    msg.attach(MIMEText(html_body, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.starttls(context=context)
        server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.smtp_user, settings.alert_email_to, msg.as_string())


async def notify_email(
    repo: str,
    commit_sha: str,
    author: str,
    branch: str,
    risk: RiskScore,
) -> bool:
    """
    Send an email alert for HIGH or CRITICAL deploys via SMTP.
    Requires SMTP_HOST, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO in .env.
    Returns True if sent, False if skipped or failed. Never raises.
    The blocking smtplib call is offloaded to a thread pool so the event loop
    is never blocked.
    """
    if risk.risk_level not in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        return False

    required = [settings.smtp_host, settings.smtp_user, settings.smtp_password, settings.alert_email_to]
    if not all(required):
        print("[notifications] Email settings incomplete — skipping email notification")
        return False

    subject, html_body = _build_email_html(repo, commit_sha, author, branch, risk)

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _send_email_sync, subject, html_body)
        print(f"[notifications] Email sent to {settings.alert_email_to} — {risk.risk_level.value.upper()} deploy in {repo}")
        return True
    except Exception as e:
        print(f"[notifications] Email notification failed (non-fatal): {e}")
        return False
