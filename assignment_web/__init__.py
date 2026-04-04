from datetime import datetime
from pathlib import Path
import os
import shutil
from zoneinfo import ZoneInfo

from flask import Flask

from .database import init_app as init_db_app
from .notifications import has_push_config
from .payments import get_payment_gateway
from .routes import register_routes
from .services import get_timezone


def _migrate_legacy_database_path(app: Flask) -> None:
    target = Path(app.config["DATABASE"])
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        if target.exists() and target.stat().st_size > 0:
            return
    except OSError:
        return

    for source in (Path("/tmp/assignment_hub.db"), Path(app.root_path).parent / "assignment_hub.db"):
        if source == target:
            continue
        try:
            if source.exists() and source.is_file() and source.stat().st_size > 0:
                shutil.copy2(source, target)
                app.logger.info("Migrated database from %s to %s", source, target)
                return
        except OSError as exc:
            app.logger.warning("Could not migrate database from %s to %s: %s", source, target, exc)


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "change-this-before-production"),
        DATABASE=os.getenv("DATABASE_PATH", str(Path(app.root_path).parent / "assignment_hub.db")),
        APP_NAME="Ink & Insight",
        APP_TIMEZONE=os.getenv("APP_TIMEZONE", "Asia/Calcutta"),
        PAYMENT_PROVIDER=os.getenv("PAYMENT_PROVIDER", "demo").lower(),
        RAZORPAY_KEY_ID=os.getenv("RAZORPAY_KEY_ID", ""),
        RAZORPAY_KEY_SECRET=os.getenv("RAZORPAY_KEY_SECRET", ""),
        AUTO_RELEASE_PAYOUTS=os.getenv("AUTO_RELEASE_PAYOUTS", "true").lower() == "true",
        WORKER_SHARE=float(os.getenv("WORKER_SHARE", "0.65")),
        OWNER_USERNAME=os.getenv("OWNER_USERNAME", "owner"),
        OWNER_PASSWORD=os.getenv("OWNER_PASSWORD", "owner123"),
        SHOW_DEMO_CREDENTIALS=os.getenv("SHOW_DEMO_CREDENTIALS", "true").lower() == "true",
        SEED_DEMO_DATA=os.getenv("SEED_DEMO_DATA", "true").lower() == "true",
        SMTP_HOST=os.getenv("SMTP_HOST", ""),
        SMTP_PORT=int(os.getenv("SMTP_PORT", "587")),
        SMTP_USERNAME=os.getenv("SMTP_USERNAME", ""),
        SMTP_PASSWORD=os.getenv("SMTP_PASSWORD", ""),
        SMTP_FROM_EMAIL=os.getenv("SMTP_FROM_EMAIL", ""),
        SMTP_USE_TLS=os.getenv("SMTP_USE_TLS", "true").lower() == "true",
        TWILIO_ACCOUNT_SID=os.getenv("TWILIO_ACCOUNT_SID", ""),
        TWILIO_AUTH_TOKEN=os.getenv("TWILIO_AUTH_TOKEN", ""),
        TWILIO_SMS_FROM=os.getenv("TWILIO_SMS_FROM", ""),
        TWILIO_WHATSAPP_FROM=os.getenv("TWILIO_WHATSAPP_FROM", ""),
        OWNER_ALERT_EMAIL=os.getenv("OWNER_ALERT_EMAIL", ""),
        OWNER_ALERT_PHONE=os.getenv("OWNER_ALERT_PHONE", ""),
        OWNER_ALERT_WHATSAPP=os.getenv("OWNER_ALERT_WHATSAPP", ""),
        VAPID_PUBLIC_KEY=os.getenv("VAPID_PUBLIC_KEY", ""),
        VAPID_PRIVATE_KEY=os.getenv("VAPID_PRIVATE_KEY", ""),
        VAPID_CLAIMS_EMAIL=os.getenv("VAPID_CLAIMS_EMAIL", ""),
        PASSWORD_RESET_CODE_MINUTES=int(os.getenv("PASSWORD_RESET_CODE_MINUTES", "15")),
    )

    @app.template_filter("currency_inr")
    def currency_inr(value: float | int | None) -> str:
        amount = float(value or 0)
        return f"INR {amount:,.0f}"

    @app.template_filter("pretty_datetime")
    def pretty_datetime(value: str | None) -> str:
        if not value:
            return "-"
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return value
        timezone = get_timezone(app.config["APP_TIMEZONE"])
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone)
        return moment.astimezone(timezone).strftime("%d %b %Y, %I:%M %p")

    @app.context_processor
    def inject_globals() -> dict[str, object]:
        gateway = get_payment_gateway(app)
        return {
            "app_name": app.config["APP_NAME"],
            "app_timezone": app.config["APP_TIMEZONE"],
            "payment_provider_name": gateway.label,
            "worker_demo_username": "neha.writer",
            "worker_demo_password": "demo123",
            "owner_demo_username": app.config["OWNER_USERNAME"],
            "owner_demo_password": app.config["OWNER_PASSWORD"],
            "show_demo_credentials": app.config["SHOW_DEMO_CREDENTIALS"],
            "push_enabled": has_push_config(),
            "vapid_public_key": app.config["VAPID_PUBLIC_KEY"],
        }

    _migrate_legacy_database_path(app)
    init_db_app(app)
    register_routes(app)
    return app
