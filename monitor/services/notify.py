"""Email and Slack notifications for new results."""

import logging
import socket

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives, send_mail
from django.template.loader import render_to_string
from django.urls import reverse

logger = logging.getLogger("monitor.notify")

MAX_RESULTS_IN_EMAIL = 50
MAX_RESULTS_IN_SLACK = 15


class NotifyError(Exception):
    pass


def _app_settings():
    from monitor.models import AppSettings

    return AppSettings.load()


def _detail_url(search):
    return settings.APP_BASE_URL + reverse("search-detail", args=[search.pk]) + "?view=new"


# ================================================================ email
def recipient_for(search=None):
    own = search.notify_address if search else ""
    return own or _app_settings().notify_address or settings.NOTIFY_EMAIL_TO


def email_status(check_connection=True):
    """Is email deliverable? Returns {"ok", "configured", "message", "default_recipient"}."""
    default_recipient = recipient_for(None)
    backend = settings.EMAIL_BACKEND
    base = {"default_recipient": default_recipient}
    if not backend.endswith("smtp.EmailBackend"):
        return {**base, "ok": True, "configured": True, "message": f"Using {backend}"}
    host, port = settings.EMAIL_HOST, settings.EMAIL_PORT
    if not host:
        return {**base, "ok": False, "configured": False,
                "message": "No email server is configured (EMAIL_HOST is empty in .env)."}
    if check_connection:
        key = f"monitor:smtp-reachable:{host}:{port}"
        error = cache.get(key)
        if error is None:
            try:
                socket.create_connection((host, port), timeout=3).close()
                error = ""
            except OSError as exc:
                error = str(exc) or exc.__class__.__name__
            cache.set(key, error, 60)
        if error:
            return {**base, "ok": False, "configured": True,
                    "message": f"The email server {host}:{port} is not reachable ({error})."}
    return {**base, "ok": True, "configured": True, "message": f"SMTP server {host}:{port}"}


def send_new_results_email(run):
    search = run.search
    to = recipient_for(search)
    if not to:
        logger.warning(
            "Email notification skipped for '%s': no recipient configured", search.name,
            extra={"search_id": search.id, "run_id": run.id},
        )
        return False

    results = list(run.found_results.order_by("id")[:MAX_RESULTS_IN_EMAIL])
    ctx = {
        "search": search,
        "run": run,
        "results": results,
        "total": run.new_results,
        "truncated": run.new_results > len(results),
        "detail_url": _detail_url(search),
    }
    subject = f"[SearXNG Monitor] {run.new_results} new result(s) for '{search.name}'"
    msg = EmailMultiAlternatives(
        subject=subject,
        body=render_to_string("monitor/email/new_results.txt", ctx),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to],
    )
    msg.attach_alternative(render_to_string("monitor/email/new_results.html", ctx), "text/html")
    msg.send()
    logger.info(
        "Emailed %s new result(s) for '%s' to %s", run.new_results, search.name, to,
        extra={"search_id": search.id, "run_id": run.id},
    )
    return True


def send_test_email(to=None):
    to = to or recipient_for(None)
    if not to:
        raise NotifyError("No recipient: set a default notification address first.")
    send_mail(
        "[SearXNG Monitor] Test email",
        "This is a test message from SearXNG Monitor. Email notifications are working.",
        settings.DEFAULT_FROM_EMAIL,
        [to],
    )
    logger.info("Test email sent to %s", to)
    return to


# ================================================================ Slack
def slack_webhook_for(search=None):
    own = search.slack_webhook_url if search else ""
    return own or _app_settings().slack_webhook_url or settings.SLACK_WEBHOOK_URL


def _slack_escape(text):
    """Escape the three characters Slack's mrkdwn treats as control characters."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _slack_link(url, label):
    url = (url or "").replace("|", "%7C").replace(">", "%3E").replace("<", "%3C")
    return f"<{url}|{_slack_escape(label).replace('|', '¦')}>"


def build_slack_payload(search, results, total, detail_url):
    title = f"{total} new result{'s' if total != 1 else ''} for “{search.name}”"
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": title[:150]}},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"Query: `{_slack_escape(search.query)[:300]}`"},
        ]},
        {"type": "divider"},
    ]
    for r in results[:MAX_RESULTS_IN_SLACK]:
        snippet = _slack_escape((r.content or "")[:280])
        text = f"*{_slack_link(r.url, (r.title or r.url)[:200])}*"
        if snippet:
            text += f"\n{snippet}"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}})
    more = total - min(len(results), MAX_RESULTS_IN_SLACK)
    footer = _slack_link(detail_url, "Open in SearXNG Monitor")
    if more > 0:
        footer = f"…and {more} more. {footer}"
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]})
    # "text" is the fallback shown in notifications and by clients that can't render blocks.
    return {"text": title, "blocks": blocks, "unfurl_links": False, "unfurl_media": False}


def post_to_slack(webhook_url, payload):
    if not webhook_url:
        raise NotifyError("No Slack webhook URL configured.")
    allowed = ("https://", "http://") if settings.SLACK_ALLOW_HTTP else ("https://",)
    if not webhook_url.lower().startswith(allowed):
        raise NotifyError("Slack webhook URLs must start with https://")
    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
    except requests.RequestException as exc:
        raise NotifyError(f"Could not reach Slack: {exc}") from exc
    if resp.status_code == 429:
        raise NotifyError(f"Slack rate limited the webhook (retry after {resp.headers.get('Retry-After', '?')}s)")
    if resp.status_code != 200:
        # Slack explains failures in the body, e.g. "invalid_token", "no_service", "channel_is_archived".
        raise NotifyError(f"Slack returned HTTP {resp.status_code}: {resp.text[:200]}")


def send_new_results_slack(run):
    search = run.search
    webhook = slack_webhook_for(search)
    if not webhook:
        logger.warning(
            "Slack notification skipped for '%s': no webhook configured", search.name,
            extra={"search_id": search.id, "run_id": run.id},
        )
        return False
    results = list(run.found_results.filter(deleted_at__isnull=True).order_by("id")[:MAX_RESULTS_IN_SLACK])
    post_to_slack(webhook, build_slack_payload(search, results, run.new_results, _detail_url(search)))
    logger.info(
        "Posted %s new result(s) for '%s' to Slack", run.new_results, search.name,
        extra={"search_id": search.id, "run_id": run.id},
    )
    return True


def send_test_slack(webhook_url=None):
    webhook = webhook_url or slack_webhook_for(None)
    post_to_slack(webhook, {
        "text": "SearXNG Monitor test message",
        "blocks": [{"type": "section", "text": {
            "type": "mrkdwn",
            "text": ":white_check_mark: *SearXNG Monitor* can post to this channel. "
                    "New matches will appear here.",
        }}],
    })
    logger.info("Test Slack message sent")
