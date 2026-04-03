from datetime import datetime
from pathlib import Path
import os
from zoneinfo import ZoneInfo

from flask import Flask

from .database import init_app as init_db_app
from .payments import get_payment_gateway
from .routes import register_routes
from .services import get_timezone


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
        }

    init_db_app(app)
    register_routes(app)
    return app
