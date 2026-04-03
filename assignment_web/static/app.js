document.addEventListener("DOMContentLoaded", () => {
    initializeThemeToggle();
    initializeAssignmentForm();
    initializePaymentFlow();
    initializePushNotifications();
});

function initializeThemeToggle() {
    const root = document.documentElement;
    const button = document.getElementById("theme-toggle");
    const label = document.getElementById("theme-toggle-label");
    if (!button || !label) return;

    const stored = window.localStorage.getItem("theme");
    const preferred = stored || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    applyTheme(preferred);

    button.addEventListener("click", () => {
        const nextTheme = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
        applyTheme(nextTheme);
        window.localStorage.setItem("theme", nextTheme);
    });

    function applyTheme(theme) {
        root.setAttribute("data-theme", theme);
        label.textContent = theme === "dark" ? "Dark mode" : "Light mode";
    }
}

function initializeAssignmentForm() {
    const form = document.getElementById("assignment-form");
    if (!form) return;

    const modeSelect = form.querySelector("[name='submission_mode']");
    const handwrittenFields = document.getElementById("handwritten-fields");
    const minInput = form.querySelector("[name='budget_min']");
    const maxInput = form.querySelector("[name='budget_max']");
    const chosenPriceLabel = document.getElementById("chosen-price-label");
    const handwrittenInputs = handwrittenFields ? handwrittenFields.querySelectorAll("input") : [];

    const syncMode = () => {
        const isHandwritten = modeSelect?.value === "HANDWRITTEN";
        handwrittenFields?.classList.toggle("active", isHandwritten);
        handwrittenInputs.forEach((input) => {
            input.required = isHandwritten && input.name !== "location_notes";
        });
    };

    const syncPrice = () => {
        if (!chosenPriceLabel) return;
        const minAmount = Number(minInput?.value || 0);
        const maxAmount = Number(maxInput?.value || 0);
        if (minAmount <= 0 && maxAmount <= 0) {
            chosenPriceLabel.textContent = "INR 0 - INR 0";
            return;
        }
        const formatter = new Intl.NumberFormat("en-IN", {
            style: "currency",
            currency: "INR",
            maximumFractionDigits: 0,
        });
        chosenPriceLabel.textContent = `${formatter.format(minAmount || 0)} - ${formatter.format(maxAmount || 0)}`;
    };

    modeSelect?.addEventListener("change", syncMode);
    minInput?.addEventListener("input", syncPrice);
    maxInput?.addEventListener("input", syncPrice);
    syncMode();
    syncPrice();
}

function initializePaymentFlow() {
    const payButton = document.getElementById("pay-now-button");
    if (!payButton) return;

    const wrapper = payButton.closest("[data-order-id]");
    const feedback = document.getElementById("payment-feedback");
    const confirmDemoButton = document.getElementById("confirm-demo-button");
    const publicId = wrapper?.dataset.orderId;
    const provider = wrapper?.dataset.provider;
    let demoReference = null;

    payButton.addEventListener("click", async () => {
        feedback.textContent = "Preparing checkout...";
        try {
            const response = await fetch(`/payments/${publicId}/checkout`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || data.message || "Payment start failed");

            if (provider === "razorpay" && window.Razorpay) {
                feedback.textContent = "Opening Razorpay checkout...";
                openRazorpayCheckout(data, publicId);
                return;
            }

            demoReference = data.payment_reference;
            feedback.textContent = data.instructions || "Demo checkout prepared.";
            confirmDemoButton?.classList.remove("hidden");
        } catch (error) {
            feedback.textContent = error.message;
        }
    });

    confirmDemoButton?.addEventListener("click", async () => {
        feedback.textContent = "Confirming demo payment...";
        try {
            const response = await fetch(`/payments/${publicId}/confirm-demo`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ payment_reference: demoReference }),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Demo confirmation failed");
            window.location.href = data.redirect_url;
        } catch (error) {
            feedback.textContent = error.message;
        }
    });
}

function openRazorpayCheckout(payload, publicId) {
    const options = {
        key: payload.key_id,
        amount: payload.amount,
        currency: payload.currency,
        name: payload.name,
        description: payload.description,
        order_id: payload.order_id,
        prefill: payload.prefill,
        notes: payload.notes,
        theme: { color: "#d85b32" },
        handler: async function (response) {
            const verification = await fetch("/payments/razorpay/verify", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    public_id: publicId,
                    razorpay_order_id: response.razorpay_order_id,
                    razorpay_payment_id: response.razorpay_payment_id,
                    razorpay_signature: response.razorpay_signature,
                }),
            });
            const result = await verification.json();
            if (!verification.ok) {
                throw new Error(result.error || "Razorpay verification failed");
            }
            window.location.href = result.redirect_url;
        },
    };

    const razorpay = new window.Razorpay(options);
    razorpay.on("payment.failed", (event) => {
        const message = event?.error?.description || "Payment was not completed.";
        const feedback = document.getElementById("payment-feedback");
        if (feedback) feedback.textContent = message;
    });
    razorpay.open();
}

async function initializePushNotifications() {
    const buttons = Array.from(document.querySelectorAll(".push-enable"));
    if (!buttons.length || !("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
        return;
    }

    for (const button of buttons) {
        button.addEventListener("click", async () => {
            const vapidKey = button.dataset.vapidKey;
            if (!vapidKey) {
                button.textContent = "Push not configured";
                button.disabled = true;
                return;
            }

            try {
                const permission = await Notification.requestPermission();
                if (permission !== "granted") {
                    button.textContent = "Browser alerts blocked";
                    return;
                }

                const registration = await navigator.serviceWorker.register("/service-worker.js");
                let subscription = await registration.pushManager.getSubscription();
                if (!subscription) {
                    subscription = await registration.pushManager.subscribe({
                        userVisibleOnly: true,
                        applicationServerKey: urlBase64ToUint8Array(vapidKey),
                    });
                }

                const response = await fetch("/push/subscribe", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        audience_type: button.dataset.audience,
                        public_id: button.dataset.publicId,
                        email: button.dataset.email,
                        subscription: subscription.toJSON(),
                    }),
                });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || "Could not enable browser alerts.");
                }

                button.textContent = "Browser alerts enabled";
                button.disabled = true;
            } catch (error) {
                button.textContent = error.message;
            }
        });
    }
}

function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);

    for (let i = 0; i < rawData.length; ++i) {
        outputArray[i] = rawData.charCodeAt(i);
    }

    return outputArray;
}
