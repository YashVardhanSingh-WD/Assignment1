from __future__ import annotations

from base64 import b64encode
import hashlib
import hmac
import json
import urllib.error
import urllib.request
from uuid import uuid4

from flask import current_app


class PaymentGatewayError(RuntimeError):
    pass


class DemoGateway:
    name = "demo"
    label = "Demo checkout"
    is_live = False

    def create_checkout(self, assignment: dict) -> dict:
        reference = _assignment_value(assignment, "payment_reference") or f"DEMO-{uuid4().hex[:10].upper()}"
        return {
            "provider": self.name,
            "payment_reference": reference,
            "instructions": (
                "This workspace is running in demo payment mode. Click the confirmation button to "
                "simulate a successful payment and unlock worker assignment."
            ),
        }

    def verify(self, *_args, **_kwargs) -> bool:
        return True


class RazorpayGateway:
    name = "razorpay"
    label = "Razorpay checkout"
    is_live = True

    def __init__(self, key_id: str, key_secret: str) -> None:
        self.key_id = key_id
        self.key_secret = key_secret

    @property
    def is_configured(self) -> bool:
        return bool(self.key_id and self.key_secret)

    def create_checkout(self, assignment: dict) -> dict:
        if not self.is_configured:
            raise PaymentGatewayError("Razorpay credentials are missing.")

        amount_rupees = float(_assignment_value(assignment, "final_price") or 0)
        if amount_rupees <= 0:
            raise PaymentGatewayError("Final price has not been set for this order yet.")
        payload = {
            "amount": int(round(amount_rupees * 100)),
            "currency": "INR",
            "receipt": _assignment_value(assignment, "public_id"),
            "notes": {
                "assignment_id": _assignment_value(assignment, "public_id"),
                "service_type": _assignment_value(assignment, "service_type"),
                "preferred_method": _assignment_value(assignment, "payment_method"),
            },
        }

        request = urllib.request.Request(
            "https://api.razorpay.com/v1/orders",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Basic {self._basic_token()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise PaymentGatewayError(f"Razorpay order creation failed: {body}") from exc
        except urllib.error.URLError as exc:
            raise PaymentGatewayError("Could not reach Razorpay.") from exc

        return {
            "provider": self.name,
            "order_id": data["id"],
            "amount": data["amount"],
            "currency": data["currency"],
            "key_id": self.key_id,
            "name": current_app.config["APP_NAME"],
            "description": f"{_assignment_value(assignment, 'title')} ({_assignment_value(assignment, 'public_id')})",
            "prefill": {
                "name": _assignment_value(assignment, "student_name"),
                "email": _assignment_value(assignment, "email"),
                "contact": _assignment_value(assignment, "whatsapp"),
            },
            "notes": data.get("notes", {}),
        }

    def verify(self, order_id: str, payment_id: str, signature: str) -> bool:
        message = f"{order_id}|{payment_id}".encode("utf-8")
        expected = hmac.new(self.key_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def _basic_token(self) -> str:
        return b64encode(f"{self.key_id}:{self.key_secret}".encode("utf-8")).decode("utf-8")


def get_payment_gateway(app=None):
    app = app or current_app
    requested = app.config.get("PAYMENT_PROVIDER", "demo").lower()
    if requested == "razorpay":
        gateway = RazorpayGateway(
            app.config.get("RAZORPAY_KEY_ID", ""),
            app.config.get("RAZORPAY_KEY_SECRET", ""),
        )
        if gateway.is_configured:
            return gateway
    return DemoGateway()


def _assignment_value(assignment, key: str):
    if isinstance(assignment, dict):
        return assignment.get(key)
    return assignment[key]
