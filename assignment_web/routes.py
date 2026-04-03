from __future__ import annotations

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .database import get_db
from .payments import PaymentGatewayError, get_payment_gateway
from .security import hash_password, owner_required, verify_password, worker_required
from .services import (
    PAYMENT_METHODS,
    SERVICE_OPTIONS,
    STATUS_LABELS,
    SUBMISSION_MODES,
    generate_public_id,
    label_for,
    normalize_phone,
    now_iso,
    owner_commission_amount,
    status_tone,
    worker_payout_amount,
)


def register_routes(app: Flask) -> None:
    @app.route("/", methods=["GET"])
    def index():
        db = get_db()
        stats = {
            "orders": db.execute("SELECT COUNT(*) AS total FROM assignments").fetchone()["total"],
            "workers": db.execute(
                "SELECT COUNT(*) AS total FROM workers WHERE is_active = 1 AND approval_status = 'APPROVED'"
            ).fetchone()["total"],
            "pending_workers": db.execute(
                "SELECT COUNT(*) AS total FROM workers WHERE approval_status = 'PENDING'"
            ).fetchone()["total"],
        }
        return render_template(
            "index.html",
            stats=stats,
            service_options=SERVICE_OPTIONS,
            submission_modes=SUBMISSION_MODES,
            payment_methods=PAYMENT_METHODS,
        )

    @app.route("/track", methods=["POST"])
    def track_assignment():
        public_id = request.form.get("public_id", "").strip().upper()
        email = request.form.get("email", "").strip().lower()
        assignment = _get_assignment_bundle(public_id)
        if not assignment or assignment["email"].lower() != email:
            flash("We could not match that order ID with the email provided.", "error")
            return redirect(url_for("index"))
        return redirect(url_for("order_detail", public_id=public_id))

    @app.route("/assignments", methods=["POST"])
    def create_assignment():
        form = request.form
        full_name = form.get("full_name", "").strip()
        email = form.get("email", "").strip().lower()
        whatsapp = normalize_phone(form.get("whatsapp", ""))
        title = form.get("title", "").strip()
        service_type = form.get("service_type", "").strip()
        submission_mode = form.get("submission_mode", "").strip()
        payment_method = form.get("payment_method", "").strip()
        requirements = form.get("requirements", "").strip()
        deadline = form.get("deadline", "").strip()
        budget_min = _parse_price(form.get("budget_min", ""))
        budget_max = _parse_price(form.get("budget_max", ""))

        try:
            page_count = max(int(form.get("page_count", "1") or "1"), 1)
        except ValueError:
            page_count = 1

        missing = [
            label
            for label, value in [
                ("full name", full_name),
                ("email address", email),
                ("WhatsApp number", whatsapp),
                ("assignment title", title),
                ("service type", service_type),
                ("submission mode", submission_mode),
                ("deadline", deadline),
                ("payment method", payment_method),
                ("requirements", requirements),
            ]
            if not value
        ]

        if budget_min <= 0:
            missing.append("budget start")
        if budget_max <= 0:
            missing.append("budget end")
        if budget_min > 0 and budget_max > 0 and budget_max < budget_min:
            missing.append("a valid budget range")

        delivery_address = form.get("delivery_address", "").strip()
        hostel_name = form.get("hostel_name", "").strip()
        room_number = form.get("room_number", "").strip()
        location_notes = form.get("location_notes", "").strip()

        if submission_mode == "HANDWRITTEN":
            missing.extend(
                [
                    label
                    for label, value in [
                        ("delivery address", delivery_address),
                        ("hostel name", hostel_name),
                        ("room number", room_number),
                    ]
                    if not value
                ]
            )

        if missing:
            flash(f"Please complete the following before submitting: {', '.join(dict.fromkeys(missing))}.", "error")
            return redirect(url_for("index"))

        db = get_db()
        timestamp = now_iso(app.config["APP_TIMEZONE"])
        db.execute(
            """
            INSERT INTO students (full_name, email, whatsapp, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                full_name = excluded.full_name,
                whatsapp = excluded.whatsapp
            """,
            (full_name, email, whatsapp, timestamp),
        )
        student_id = db.execute("SELECT id FROM students WHERE email = ?", (email,)).fetchone()["id"]
        public_id = generate_public_id(app.config["APP_TIMEZONE"])
        payment_gateway = get_payment_gateway(app)
        preferred_channel = "WHATSAPP" if submission_mode == "HANDWRITTEN" else "EMAIL"

        db.execute(
            """
            INSERT INTO assignments (
                public_id, student_id, title, service_type, submission_mode, complexity, page_count, deadline,
                requirements, budget_min, budget_max, delivery_address, hostel_name, room_number, location_notes,
                preferred_channel, estimated_price, final_price, quoted_by, quoted_at, payment_method,
                payment_provider, payment_reference, payment_status,
                status, assigned_worker_id, created_at, updated_at, approved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                public_id,
                student_id,
                title,
                service_type,
                submission_mode,
                "STANDARD",
                page_count,
                deadline,
                requirements,
                budget_min,
                budget_max,
                delivery_address,
                hostel_name,
                room_number,
                location_notes,
                preferred_channel,
                budget_max,
                None,
                None,
                None,
                payment_method,
                payment_gateway.name,
                None,
                "WAITING_QUOTE",
                "QUOTE_PENDING",
                None,
                timestamp,
                timestamp,
                None,
            ),
        )
        assignment_id = db.execute("SELECT id FROM assignments WHERE public_id = ?", (public_id,)).fetchone()["id"]
        _append_status(
            assignment_id,
            "QUOTE_PENDING",
            f"Order created with a budget range of {_currency_value(budget_min)} to {_currency_value(budget_max)}. Final price will be set after review.",
            "student",
        )
        _append_message(
            assignment_id,
            "student",
            full_name,
            f"Initial brief: {requirements}",
        )
        db.commit()
        flash("Assignment request created. The owner will review the budget range and set the final payable amount.", "success")
        return redirect(url_for("order_detail", public_id=public_id))

    @app.route("/orders/<public_id>", methods=["GET"])
    def order_detail(public_id: str):
        assignment = _get_assignment_bundle(public_id)
        if not assignment:
            abort(404)

        db = get_db()
        messages = db.execute(
            """
            SELECT sender_role, sender_name, body, created_at
            FROM messages
            WHERE assignment_id = ?
            ORDER BY id DESC
            """,
            (assignment["id"],),
        ).fetchall()
        history = db.execute(
            """
            SELECT status, note, actor_role, created_at
            FROM status_history
            WHERE assignment_id = ?
            ORDER BY id DESC
            """,
            (assignment["id"],),
        ).fetchall()
        payout = db.execute(
            "SELECT amount, status, released_at FROM payouts WHERE assignment_id = ?",
            (assignment["id"],),
        ).fetchone()
        gateway = get_payment_gateway(app)
        return render_template(
            "order_detail.html",
            assignment=assignment,
            messages=messages,
            history=history,
            payout=payout,
            payment_provider=gateway.name,
            status_labels=STATUS_LABELS,
            status_tone=status_tone,
            service_label=label_for(SERVICE_OPTIONS, assignment["service_type"]),
            submission_label=label_for(SUBMISSION_MODES, assignment["submission_mode"]),
            payment_label=label_for(PAYMENT_METHODS, assignment["payment_method"]),
        )

    @app.route("/orders/<public_id>/messages", methods=["POST"])
    def post_student_message(public_id: str):
        assignment = _get_assignment_bundle(public_id)
        if not assignment:
            abort(404)

        email = request.form.get("email", "").strip().lower()
        body = request.form.get("body", "").strip()
        sender_name = request.form.get("sender_name", "").strip() or assignment["student_name"]

        if assignment["email"].lower() != email:
            flash("Message not sent because the email did not match this order.", "error")
            return redirect(url_for("order_detail", public_id=public_id))
        if not body:
            flash("Please write a message before sending it.", "error")
            return redirect(url_for("order_detail", public_id=public_id))

        _append_message(assignment["id"], "student", sender_name, body)
        _append_status(assignment["id"], assignment["status"], "Student shared an update or clarification.", "student")
        get_db().commit()
        flash("Message sent to the worker workspace.", "success")
        return redirect(url_for("order_detail", public_id=public_id))

    @app.route("/orders/<public_id>/approve", methods=["POST"])
    def approve_assignment(public_id: str):
        assignment = _get_assignment_bundle(public_id)
        if not assignment:
            abort(404)

        email = request.form.get("email", "").strip().lower()
        if email != assignment["email"].lower():
            flash("Approval failed because the email did not match the order.", "error")
            return redirect(url_for("order_detail", public_id=public_id))

        if assignment["status"] != "COMPLETED":
            flash("This order can be approved once the worker marks it as completed.", "error")
            return redirect(url_for("order_detail", public_id=public_id))

        db = get_db()
        timestamp = now_iso(app.config["APP_TIMEZONE"])
        db.execute(
            "UPDATE assignments SET status = 'APPROVED', approved_at = ?, updated_at = ? WHERE id = ?",
            (timestamp, timestamp, assignment["id"]),
        )
        _append_status(assignment["id"], "APPROVED", "Student approved the submitted work.", "student")
        if assignment["assigned_worker_id"]:
            _release_payout(assignment)
        db.commit()
        flash("Assignment approved. Worker payout has been queued or released.", "success")
        return redirect(url_for("order_detail", public_id=public_id))

    @app.route("/payments/<public_id>/checkout", methods=["POST"])
    def create_payment_checkout(public_id: str):
        assignment = _get_assignment_bundle(public_id)
        if not assignment:
            return jsonify({"error": "Assignment not found."}), 404
        if assignment["payment_status"] == "PAID":
            return jsonify({"message": "This assignment has already been paid for."}), 400
        if not assignment["final_price"] or float(assignment["final_price"]) <= 0:
            return jsonify({"error": "Final price has not been set yet. Please wait for the owner to confirm it."}), 400

        gateway = get_payment_gateway(app)
        try:
            payload = gateway.create_checkout(assignment)
        except PaymentGatewayError as exc:
            return jsonify({"error": str(exc)}), 400

        reference = payload.get("order_id") or payload.get("payment_reference")
        if reference and reference != assignment["payment_reference"]:
            db = get_db()
            db.execute(
                "UPDATE assignments SET payment_reference = ?, payment_provider = ?, updated_at = ? WHERE id = ?",
                (reference, gateway.name, now_iso(app.config["APP_TIMEZONE"]), assignment["id"]),
            )
            db.commit()

        return jsonify(payload)

    @app.route("/payments/<public_id>/confirm-demo", methods=["POST"])
    def confirm_demo_payment(public_id: str):
        assignment = _get_assignment_bundle(public_id)
        if not assignment:
            return jsonify({"error": "Assignment not found."}), 404
        if not assignment["final_price"] or float(assignment["final_price"]) <= 0:
            return jsonify({"error": "Final price has not been set yet."}), 400

        gateway = get_payment_gateway(app)
        if gateway.name != "demo":
            return jsonify({"error": "Demo confirmation is only available in demo mode."}), 400

        payload = request.get_json(silent=True) or {}
        payment_reference = payload.get("payment_reference") or assignment["payment_reference"] or "DEMO-MANUAL"
        _mark_assignment_paid(assignment["id"], payment_reference)
        get_db().commit()
        return jsonify({"ok": True, "redirect_url": url_for("order_detail", public_id=public_id)})

    @app.route("/payments/razorpay/verify", methods=["POST"])
    def verify_razorpay_payment():
        payload = request.get_json(silent=True) or {}
        public_id = payload.get("public_id", "")
        assignment = _get_assignment_bundle(public_id)
        if not assignment:
            return jsonify({"error": "Assignment not found."}), 404
        if not assignment["final_price"] or float(assignment["final_price"]) <= 0:
            return jsonify({"error": "Final price has not been set yet."}), 400

        gateway = get_payment_gateway(app)
        if gateway.name != "razorpay":
            return jsonify({"error": "Razorpay mode is not enabled."}), 400

        order_id = payload.get("razorpay_order_id", "")
        payment_id = payload.get("razorpay_payment_id", "")
        signature = payload.get("razorpay_signature", "")
        if not gateway.verify(order_id, payment_id, signature):
            return jsonify({"error": "Signature verification failed."}), 400

        _mark_assignment_paid(assignment["id"], payment_id)
        get_db().commit()
        return jsonify({"ok": True, "redirect_url": url_for("order_detail", public_id=public_id)})

    @app.route("/worker/login", methods=["GET", "POST"])
    def worker_login():
        if request.method == "POST":
            username = request.form.get("username", "").strip().lower()
            password = request.form.get("password", "")
            db = get_db()
            worker = db.execute(
                """
                SELECT id, full_name, username, password_hash, is_active, approval_status
                FROM workers
                WHERE username = ?
                """,
                (username,),
            ).fetchone()
            if not worker or not verify_password(worker["password_hash"], password):
                flash("Worker login failed. Please check the username and password.", "error")
                return redirect(url_for("worker_login"))
            if not worker["is_active"]:
                flash("This worker account is inactive. Please contact the owner.", "error")
                return redirect(url_for("worker_login"))
            if worker["approval_status"] == "PENDING":
                flash("Your worker account is registered but still waiting for owner approval.", "error")
                return redirect(url_for("worker_login"))
            if worker["approval_status"] == "REJECTED":
                flash("This worker registration was not approved by the owner.", "error")
                return redirect(url_for("worker_login"))

            session.clear()
            session["worker_id"] = worker["id"]
            session["worker_name"] = worker["full_name"]
            next_page = request.args.get("next")
            return redirect(next_page or url_for("worker_dashboard"))

        return render_template("worker_login.html")

    @app.route("/worker/register", methods=["POST"])
    def worker_register():
        form = request.form
        full_name = form.get("full_name", "").strip()
        username = form.get("username", "").strip().lower()
        whatsapp = normalize_phone(form.get("whatsapp", ""))
        expertise = form.get("expertise", "").strip()
        payout_method = form.get("payout_method", "").strip()
        payout_target = form.get("payout_target", "").strip()
        password = form.get("password", "")

        missing = [
            label
            for label, value in [
                ("full name", full_name),
                ("username", username),
                ("WhatsApp number", whatsapp),
                ("expertise", expertise),
                ("payout method", payout_method),
                ("payout details", payout_target),
                ("password", password),
            ]
            if not value
        ]
        if missing:
            flash(f"Please complete the following before registering: {', '.join(missing)}.", "error")
            return redirect(url_for("worker_login"))

        db = get_db()
        existing_worker = db.execute("SELECT id FROM workers WHERE username = ?", (username,)).fetchone()
        if existing_worker:
            flash("That worker username is already taken. Please choose another one.", "error")
            return redirect(url_for("worker_login"))

        timestamp = now_iso(app.config["APP_TIMEZONE"])
        db.execute(
            """
            INSERT INTO workers (
                full_name, username, password_hash, whatsapp, expertise, payout_method,
                payout_target, is_active, approval_status, approved_at, approved_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                full_name,
                username,
                hash_password(password),
                whatsapp,
                expertise,
                payout_method,
                payout_target,
                1,
                "PENDING",
                None,
                None,
                timestamp,
            ),
        )
        db.commit()
        flash("Worker registration submitted. The owner must approve it before you can log in.", "success")
        return redirect(url_for("worker_login"))

    @app.route("/worker/logout", methods=["POST"])
    def worker_logout():
        session.pop("worker_id", None)
        session.pop("worker_name", None)
        flash("Worker session closed.", "success")
        return redirect(url_for("index"))

    @app.route("/worker/dashboard", methods=["GET"])
    @worker_required
    def worker_dashboard():
        db = get_db()
        worker_id = session["worker_id"]
        available_assignments = db.execute(
            """
            SELECT a.*, s.full_name AS student_name, s.email, s.whatsapp
            FROM assignments a
            JOIN students s ON s.id = a.student_id
            WHERE a.status = 'PAID' AND a.assigned_worker_id IS NULL
            ORDER BY a.deadline ASC
            """
        ).fetchall()
        my_assignments = db.execute(
            """
            SELECT a.*, s.full_name AS student_name, s.email, s.whatsapp
            FROM assignments a
            JOIN students s ON s.id = a.student_id
            WHERE a.assigned_worker_id = ?
            ORDER BY a.updated_at DESC
            """,
            (worker_id,),
        ).fetchall()
        payouts = db.execute(
            """
            SELECT p.amount, p.status, p.released_at, a.public_id, a.title
            FROM payouts p
            JOIN assignments a ON a.id = p.assignment_id
            WHERE p.worker_id = ?
            ORDER BY p.created_at DESC
            """,
            (worker_id,),
        ).fetchall()
        stats = {
            "available": len(available_assignments),
            "mine": len(my_assignments),
            "earnings": sum(row["amount"] for row in payouts if row["status"] == "RELEASED"),
        }
        return render_template(
            "worker_dashboard.html",
            available_assignments=available_assignments,
            my_assignments=my_assignments,
            payouts=payouts,
            stats=stats,
            status_labels=STATUS_LABELS,
            status_tone=status_tone,
            service_lookup=_label_lookup(SERVICE_OPTIONS),
            submission_lookup=_label_lookup(SUBMISSION_MODES),
            worker_earning_amount=worker_payout_amount,
            worker_share=app.config["WORKER_SHARE"],
        )

    @app.route("/worker/assignments/<int:assignment_id>/claim", methods=["POST"])
    @worker_required
    def claim_assignment(assignment_id: int):
        db = get_db()
        assignment = db.execute(
            "SELECT id, title, status, assigned_worker_id FROM assignments WHERE id = ?",
            (assignment_id,),
        ).fetchone()
        if not assignment:
            abort(404)
        if assignment["assigned_worker_id"] is not None:
            flash("This assignment has already been claimed by another worker.", "error")
            return redirect(url_for("worker_dashboard"))
        if assignment["status"] != "PAID":
            flash("Only paid assignments can be claimed.", "error")
            return redirect(url_for("worker_dashboard"))

        timestamp = now_iso(app.config["APP_TIMEZONE"])
        db.execute(
            """
            UPDATE assignments
            SET assigned_worker_id = ?, status = 'ASSIGNED', updated_at = ?
            WHERE id = ?
            """,
            (session["worker_id"], timestamp, assignment_id),
        )
        _append_status(assignment_id, "ASSIGNED", f"{session['worker_name']} claimed the assignment.", "worker")
        db.commit()
        flash("Assignment claimed successfully.", "success")
        return redirect(url_for("worker_dashboard"))

    @app.route("/worker/assignments/<int:assignment_id>/status", methods=["POST"])
    @worker_required
    def update_worker_status(assignment_id: int):
        new_status = request.form.get("status", "").strip()
        note = request.form.get("note", "").strip()
        if new_status not in {"ASSIGNED", "IN_PROGRESS", "COMPLETED"}:
            flash("That status transition is not supported.", "error")
            return redirect(url_for("worker_dashboard"))

        db = get_db()
        assignment = db.execute(
            """
            SELECT id
            FROM assignments
            WHERE id = ? AND assigned_worker_id = ?
            """,
            (assignment_id, session["worker_id"]),
        ).fetchone()
        if not assignment:
            abort(404)

        timestamp = now_iso(app.config["APP_TIMEZONE"])
        db.execute(
            "UPDATE assignments SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, timestamp, assignment_id),
        )
        note_text = note or f"Worker updated the assignment status to {STATUS_LABELS.get(new_status, new_status)}."
        _append_status(assignment_id, new_status, note_text, "worker")
        db.commit()
        flash("Assignment status updated.", "success")
        return redirect(url_for("worker_dashboard"))

    @app.route("/worker/assignments/<int:assignment_id>/messages", methods=["POST"])
    @worker_required
    def post_worker_message(assignment_id: int):
        body = request.form.get("body", "").strip()
        if not body:
            flash("Please write a message before sending it.", "error")
            return redirect(url_for("worker_dashboard"))

        db = get_db()
        assignment = db.execute(
            """
            SELECT id
            FROM assignments
            WHERE id = ? AND assigned_worker_id = ?
            """,
            (assignment_id, session["worker_id"]),
        ).fetchone()
        if not assignment:
            abort(404)

        _append_message(assignment_id, "worker", session["worker_name"], body)
        _append_status(assignment_id, "IN_PROGRESS", "Worker sent a message or delivery note.", "worker")
        db.commit()
        flash("Message added to the order thread.", "success")
        return redirect(url_for("worker_dashboard"))

    @app.route("/owner/login", methods=["GET", "POST"])
    def owner_login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if username != app.config["OWNER_USERNAME"] or password != app.config["OWNER_PASSWORD"]:
                flash("Owner login failed. Please check the credentials.", "error")
                return redirect(url_for("owner_login"))

            session.clear()
            session["owner_authenticated"] = True
            session["owner_name"] = username
            next_page = request.args.get("next")
            return redirect(next_page or url_for("owner_dashboard"))

        return render_template("owner_login.html")

    @app.route("/owner/logout", methods=["POST"])
    def owner_logout():
        session.pop("owner_authenticated", None)
        session.pop("owner_name", None)
        flash("Owner session closed.", "success")
        return redirect(url_for("index"))

    @app.route("/owner/dashboard", methods=["GET"])
    @owner_required
    def owner_dashboard():
        db = get_db()
        pending_workers = db.execute(
            """
            SELECT id, full_name, username, whatsapp, expertise, payout_method, payout_target, created_at
            FROM workers
            WHERE approval_status = 'PENDING'
            ORDER BY created_at ASC
            """
        ).fetchall()
        approved_workers = db.execute(
            """
            SELECT full_name, username, whatsapp, expertise, payout_method, approval_status, approved_at
            FROM workers
            WHERE approval_status = 'APPROVED'
            ORDER BY approved_at DESC, created_at DESC
            LIMIT 12
            """
        ).fetchall()
        quote_pending_assignments = db.execute(
            """
            SELECT public_id, title, budget_min, budget_max, page_count, service_type, deadline, created_at
            FROM assignments
            WHERE status = 'QUOTE_PENDING'
            ORDER BY created_at ASC
            """
        ).fetchall()
        recent_assignments = db.execute(
            """
            SELECT public_id, title, status, budget_min, budget_max, final_price, created_at
            FROM assignments
            ORDER BY created_at DESC
            LIMIT 10
            """
        ).fetchall()
        return render_template(
            "owner_dashboard.html",
            pending_workers=pending_workers,
            approved_workers=approved_workers,
            quote_pending_assignments=quote_pending_assignments,
            recent_assignments=recent_assignments,
            status_labels=STATUS_LABELS,
            status_tone=status_tone,
            worker_earning_amount=worker_payout_amount,
            owner_commission_amount=owner_commission_amount,
            worker_share=app.config["WORKER_SHARE"],
            service_lookup=_label_lookup(SERVICE_OPTIONS),
        )

    @app.route("/owner/assignments/<public_id>/quote", methods=["POST"])
    @owner_required
    def quote_assignment(public_id: str):
        assignment = _get_assignment_bundle(public_id)
        if not assignment:
            abort(404)

        final_price = _parse_price(request.form.get("final_price", ""))
        note = request.form.get("note", "").strip()
        if final_price <= 0:
            flash("Please enter a valid final price before saving the quote.", "error")
            return redirect(url_for("owner_dashboard"))

        timestamp = now_iso(app.config["APP_TIMEZONE"])
        db = get_db()
        db.execute(
            """
            UPDATE assignments
            SET final_price = ?, estimated_price = ?, status = 'NEW', payment_status = 'PENDING',
                quoted_by = ?, quoted_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                final_price,
                final_price,
                session.get("owner_name", "owner"),
                timestamp,
                timestamp,
                assignment["id"],
            ),
        )
        quote_note = note or f"Final price confirmed at {_currency_value(final_price)} after workload review."
        _append_status(assignment["id"], "NEW", quote_note, "owner")
        db.commit()
        flash("Final price saved. The student can now complete payment.", "success")
        return redirect(url_for("owner_dashboard"))

    @app.route("/owner/workers/<int:worker_id>/approve", methods=["POST"])
    @owner_required
    def approve_worker(worker_id: int):
        db = get_db()
        worker = db.execute(
            "SELECT id, approval_status FROM workers WHERE id = ?",
            (worker_id,),
        ).fetchone()
        if not worker:
            abort(404)

        timestamp = now_iso(app.config["APP_TIMEZONE"])
        db.execute(
            """
            UPDATE workers
            SET approval_status = 'APPROVED', approved_at = ?, approved_by = ?, is_active = 1
            WHERE id = ?
            """,
            (timestamp, session.get("owner_name", "owner"), worker_id),
        )
        db.commit()
        flash("Worker approved successfully.", "success")
        return redirect(url_for("owner_dashboard"))

    @app.route("/owner/workers/<int:worker_id>/reject", methods=["POST"])
    @owner_required
    def reject_worker(worker_id: int):
        db = get_db()
        worker = db.execute(
            "SELECT id FROM workers WHERE id = ?",
            (worker_id,),
        ).fetchone()
        if not worker:
            abort(404)

        db.execute(
            """
            UPDATE workers
            SET approval_status = 'REJECTED', approved_at = NULL, approved_by = ?, is_active = 0
            WHERE id = ?
            """,
            (session.get("owner_name", "owner"), worker_id),
        )
        db.commit()
        flash("Worker registration was rejected.", "success")
        return redirect(url_for("owner_dashboard"))


def _get_assignment_bundle(public_id: str):
    db = get_db()
    return db.execute(
        """
        SELECT
            a.*,
            s.full_name AS student_name,
            s.email,
            s.whatsapp,
            w.full_name AS worker_name,
            w.whatsapp AS worker_whatsapp
        FROM assignments a
        JOIN students s ON s.id = a.student_id
        LEFT JOIN workers w ON w.id = a.assigned_worker_id
        WHERE a.public_id = ?
        """,
        (public_id.upper(),),
    ).fetchone()


def _append_status(assignment_id: int, status: str, note: str, actor_role: str) -> None:
    db = get_db()
    db.execute(
        """
        INSERT INTO status_history (assignment_id, status, note, actor_role, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (assignment_id, status, note, actor_role, now_iso(current_app.config["APP_TIMEZONE"])),
    )


def _append_message(assignment_id: int, sender_role: str, sender_name: str, body: str) -> None:
    db = get_db()
    db.execute(
        """
        INSERT INTO messages (assignment_id, sender_role, sender_name, body, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (assignment_id, sender_role, sender_name, body, now_iso(current_app.config["APP_TIMEZONE"])),
    )


def _mark_assignment_paid(assignment_id: int, payment_reference: str) -> None:
    db = get_db()
    timestamp = now_iso(current_app.config["APP_TIMEZONE"])
    db.execute(
        """
        UPDATE assignments
        SET payment_status = 'PAID', status = 'PAID', payment_reference = ?, updated_at = ?
        WHERE id = ?
        """,
        (payment_reference, timestamp, assignment_id),
    )
    _append_status(assignment_id, "PAID", "Payment verified successfully.", "system")


def _release_payout(assignment) -> None:
    db = get_db()
    payout_exists = db.execute(
        "SELECT id FROM payouts WHERE assignment_id = ?",
        (assignment["id"],),
    ).fetchone()
    if payout_exists:
        return

    timestamp = now_iso(current_app.config["APP_TIMEZONE"])
    amount = worker_payout_amount(assignment["final_price"], current_app.config["WORKER_SHARE"])
    status = "RELEASED" if current_app.config["AUTO_RELEASE_PAYOUTS"] else "QUEUED"
    released_at = timestamp if status == "RELEASED" else None
    db.execute(
        """
        INSERT INTO payouts (assignment_id, worker_id, amount, status, external_reference, released_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            assignment["id"],
            assignment["assigned_worker_id"],
            amount,
            status,
            f"PAYOUT-{assignment['public_id']}",
            released_at,
            timestamp,
        ),
    )


def _label_lookup(options) -> dict:
    return dict(options)


def _parse_price(raw_value: str) -> float:
    try:
        return round(float(raw_value), 2)
    except (TypeError, ValueError):
        return 0


def _currency_value(amount: float) -> str:
    return f"INR {amount:,.0f}"
