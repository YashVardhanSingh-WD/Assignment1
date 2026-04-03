from __future__ import annotations

from email.message import EmailMessage
import base64
import json
import smtplib
import ssl
import urllib.error
import urllib.parse
import urllib.request

from flask import current_app

from .database import get_db
from .services import now_iso

try:
    from pywebpush import WebPushException, webpush
except ImportError:  # pragma: no cover - optional dependency in some environments
    WebPushException = Exception
    webpush = None


def create_notification_record(
    *,
    audience_type: str,
    title: str,
    body: str,
    tone: str = "muted",
    student_id: int | None = None,
    worker_id: int | None = None,
    assignment_id: int | None = None,
    action_url: str | None = None,
) -> int:
    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO notifications (
            audience_type, student_id, worker_id, assignment_id, title, body, tone, action_url, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audience_type,
            student_id,
            worker_id,
            assignment_id,
            title,
            body,
            tone,
            action_url,
            now_iso(current_app.config["APP_TIMEZONE"]),
        ),
    )
    return int(cursor.lastrowid)


def log_delivery(
    *,
    notification_id: int | None,
    audience_type: str,
    channel: str,
    recipient: str,
    provider: str,
    status: str,
    external_reference: str | None = None,
    error_message: str | None = None,
) -> None:
    get_db().execute(
        """
        INSERT INTO notification_deliveries (
            notification_id, audience_type, channel, recipient, provider, status, external_reference, error_message, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            notification_id,
            audience_type,
            channel,
            recipient,
            provider,
            status,
            external_reference,
            error_message,
            now_iso(current_app.config["APP_TIMEZONE"]),
        ),
    )


def dispatch_notification(
    *,
    notification_id: int,
    audience_type: str,
    title: str,
    body: str,
    action_url: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    whatsapp: str | None = None,
    student_id: int | None = None,
    worker_id: int | None = None,
    assignment_id: int | None = None,
) -> None:
    message_text = body if not action_url else f"{body}\n\nOpen: {action_url}"
    if email:
        _send_email(
            notification_id=notification_id,
            audience_type=audience_type,
            recipient=email,
            subject=title,
            body=message_text,
        )
    if phone:
        _send_twilio_message(
            notification_id=notification_id,
            audience_type=audience_type,
            recipient=phone,
            body=message_text,
            channel="sms",
        )
    if whatsapp:
        _send_twilio_message(
            notification_id=notification_id,
            audience_type=audience_type,
            recipient=whatsapp,
            body=message_text,
            channel="whatsapp",
        )

    _send_push_notifications(
        notification_id=notification_id,
        audience_type=audience_type,
        title=title,
        body=body,
        action_url=action_url,
        student_id=student_id,
        worker_id=worker_id,
        assignment_id=assignment_id,
    )


def send_worker_reset_code(
    *,
    worker_id: int,
    title: str,
    body: str,
    channel: str,
    email: str | None = None,
    phone: str | None = None,
) -> bool:
    recipient = email if channel == "email" else phone
    if not recipient:
        return False

    if channel == "email":
        status = _send_email(
            notification_id=None,
            audience_type="WORKER_RESET",
            recipient=recipient,
            subject=title,
            body=body,
        )
        return status == "SENT"

    status = _send_twilio_message(
        notification_id=None,
        audience_type="WORKER_RESET",
        recipient=recipient,
        body=body,
        channel=channel,
    )
    return status == "SENT"


def has_push_config() -> bool:
    return bool(
        current_app.config.get("VAPID_PUBLIC_KEY")
        and current_app.config.get("VAPID_PRIVATE_KEY")
        and current_app.config.get("VAPID_CLAIMS_EMAIL")
    )


def channel_configured(channel: str) -> bool:
    channel = channel.lower()
    if channel == "email":
        return bool(current_app.config.get("SMTP_HOST") and (current_app.config.get("SMTP_FROM_EMAIL") or current_app.config.get("SMTP_USERNAME")))
    if channel == "sms":
        return bool(
            current_app.config.get("TWILIO_ACCOUNT_SID")
            and current_app.config.get("TWILIO_AUTH_TOKEN")
            and current_app.config.get("TWILIO_SMS_FROM")
        )
    if channel == "whatsapp":
        return bool(
            current_app.config.get("TWILIO_ACCOUNT_SID")
            and current_app.config.get("TWILIO_AUTH_TOKEN")
            and current_app.config.get("TWILIO_WHATSAPP_FROM")
        )
    if channel == "push":
        return has_push_config() and webpush is not None
    return False


def upsert_push_subscription(
    *,
    audience_type: str,
    endpoint: str,
    p256dh: str,
    auth: str,
    content_encoding: str | None = None,
    student_id: int | None = None,
    worker_id: int | None = None,
    assignment_id: int | None = None,
) -> None:
    db = get_db()
    timestamp = now_iso(current_app.config["APP_TIMEZONE"])
    db.execute(
        """
        INSERT INTO push_subscriptions (
            audience_type, student_id, worker_id, assignment_id, endpoint, p256dh, auth, content_encoding, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET
            audience_type = excluded.audience_type,
            student_id = excluded.student_id,
            worker_id = excluded.worker_id,
            assignment_id = excluded.assignment_id,
            p256dh = excluded.p256dh,
            auth = excluded.auth,
            content_encoding = excluded.content_encoding,
            updated_at = excluded.updated_at
        """,
        (
            audience_type,
            student_id,
            worker_id,
            assignment_id,
            endpoint,
            p256dh,
            auth,
            content_encoding,
            timestamp,
            timestamp,
        ),
    )


def remove_push_subscription(endpoint: str) -> None:
    get_db().execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))


def _send_email(*, notification_id: int | None, audience_type: str, recipient: str, subject: str, body: str) -> str:
    config = current_app.config
    host = config.get("SMTP_HOST")
    port = int(config.get("SMTP_PORT", 587))
    username = config.get("SMTP_USERNAME")
    password = config.get("SMTP_PASSWORD")
    from_email = config.get("SMTP_FROM_EMAIL") or username

    if not (host and from_email):
        log_delivery(
            notification_id=notification_id,
            audience_type=audience_type,
            channel="email",
            recipient=recipient,
            provider="smtp",
            status="SKIPPED",
            error_message="SMTP is not configured.",
        )
        return "SKIPPED"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_email
    message["To"] = recipient
    message.set_content(body)

    try:
        if port == 465 and not config.get("SMTP_USE_TLS", True):
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20) as client:
                if username and password:
                    client.login(username, password)
                client.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=20) as client:
                client.ehlo()
                if config.get("SMTP_USE_TLS", True):
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                if username and password:
                    client.login(username, password)
                client.send_message(message)
        log_delivery(
            notification_id=notification_id,
            audience_type=audience_type,
            channel="email",
            recipient=recipient,
            provider="smtp",
            status="SENT",
        )
        return "SENT"
    except Exception as exc:  # pragma: no cover - depends on external SMTP server
        log_delivery(
            notification_id=notification_id,
            audience_type=audience_type,
            channel="email",
            recipient=recipient,
            provider="smtp",
            status="FAILED",
            error_message=str(exc),
        )
        return "FAILED"


def _send_twilio_message(
    *,
    notification_id: int | None,
    audience_type: str,
    recipient: str,
    body: str,
    channel: str,
) -> str:
    config = current_app.config
    account_sid = config.get("TWILIO_ACCOUNT_SID")
    auth_token = config.get("TWILIO_AUTH_TOKEN")
    sender = config.get("TWILIO_WHATSAPP_FROM") if channel == "whatsapp" else config.get("TWILIO_SMS_FROM")

    if not (account_sid and auth_token and sender):
        log_delivery(
            notification_id=notification_id,
            audience_type=audience_type,
            channel=channel,
            recipient=recipient,
            provider="twilio",
            status="SKIPPED",
            error_message="Twilio is not configured for this channel.",
        )
        return "SKIPPED"

    to_value = recipient
    from_value = sender
    if channel == "whatsapp":
        to_value = recipient if recipient.startswith("whatsapp:") else f"whatsapp:{recipient}"
        from_value = sender if sender.startswith("whatsapp:") else f"whatsapp:{sender}"

    payload = urllib.parse.urlencode({"To": to_value, "From": from_value, "Body": body}).encode("utf-8")
    request_obj = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
        data=payload,
        method="POST",
    )
    auth_value = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
    request_obj.add_header("Authorization", f"Basic {auth_value}")
    request_obj.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(request_obj, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        log_delivery(
            notification_id=notification_id,
            audience_type=audience_type,
            channel=channel,
            recipient=recipient,
            provider="twilio",
            status="SENT",
            external_reference=payload.get("sid"),
        )
        return "SENT"
    except urllib.error.HTTPError as exc:  # pragma: no cover - depends on external Twilio API
        error_body = exc.read().decode("utf-8", "replace")
        log_delivery(
            notification_id=notification_id,
            audience_type=audience_type,
            channel=channel,
            recipient=recipient,
            provider="twilio",
            status="FAILED",
            error_message=error_body or str(exc),
        )
        return "FAILED"
    except Exception as exc:  # pragma: no cover - depends on external Twilio API
        log_delivery(
            notification_id=notification_id,
            audience_type=audience_type,
            channel=channel,
            recipient=recipient,
            provider="twilio",
            status="FAILED",
            error_message=str(exc),
        )
        return "FAILED"


def _send_push_notifications(
    *,
    notification_id: int,
    audience_type: str,
    title: str,
    body: str,
    action_url: str | None,
    student_id: int | None,
    worker_id: int | None,
    assignment_id: int | None,
) -> None:
    if not has_push_config() or webpush is None:
        return

    db = get_db()
    clauses = ["audience_type = ?"]
    params: list[object] = [audience_type]
    if student_id is not None:
        clauses.append("student_id = ?")
        params.append(student_id)
    if worker_id is not None:
        clauses.append("worker_id = ?")
        params.append(worker_id)
    if assignment_id is not None and audience_type == "STUDENT":
        clauses.append("(assignment_id = ? OR assignment_id IS NULL)")
        params.append(assignment_id)

    subscriptions = db.execute(
        f"""
        SELECT id, endpoint, p256dh, auth, content_encoding
        FROM push_subscriptions
        WHERE {' AND '.join(clauses)}
        """,
        tuple(params),
    ).fetchall()
    if not subscriptions:
        return

    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "url": action_url or "/",
        }
    )

    for subscription in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription["endpoint"],
                    "keys": {
                        "p256dh": subscription["p256dh"],
                        "auth": subscription["auth"],
                    },
                },
                data=payload,
                vapid_private_key=current_app.config["VAPID_PRIVATE_KEY"],
                vapid_claims={"sub": f"mailto:{current_app.config['VAPID_CLAIMS_EMAIL']}"},
            )
            log_delivery(
                notification_id=notification_id,
                audience_type=audience_type,
                channel="push",
                recipient=subscription["endpoint"],
                provider="webpush",
                status="SENT",
            )
        except WebPushException as exc:  # pragma: no cover - depends on browser push service
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            if status_code in {404, 410}:
                db.execute("DELETE FROM push_subscriptions WHERE id = ?", (subscription["id"],))
            log_delivery(
                notification_id=notification_id,
                audience_type=audience_type,
                channel="push",
                recipient=subscription["endpoint"],
                provider="webpush",
                status="FAILED",
                error_message=str(exc),
            )
        except Exception as exc:  # pragma: no cover - depends on browser push service
            log_delivery(
                notification_id=notification_id,
                audience_type=audience_type,
                channel="push",
                recipient=subscription["endpoint"],
                provider="webpush",
                status="FAILED",
                error_message=str(exc),
            )
