from datetime import datetime, timedelta
import sqlite3

from flask import current_app, g

from .security import hash_password
from .services import get_timezone, now_iso


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    whatsapp TEXT NOT NULL,
    is_blocked INTEGER NOT NULL DEFAULT 0,
    blocked_at TEXT,
    blocked_reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    username TEXT NOT NULL UNIQUE,
    email TEXT,
    password_hash TEXT NOT NULL,
    whatsapp TEXT NOT NULL,
    expertise TEXT NOT NULL,
    payout_method TEXT NOT NULL,
    payout_target TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    approval_status TEXT NOT NULL DEFAULT 'APPROVED',
    approved_at TEXT,
    approved_by TEXT,
    removed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    student_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    service_type TEXT NOT NULL,
    submission_mode TEXT NOT NULL,
    complexity TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    deadline TEXT NOT NULL,
    requirements TEXT NOT NULL,
    budget_min REAL,
    budget_max REAL,
    delivery_address TEXT,
    hostel_name TEXT,
    room_number TEXT,
    location_notes TEXT,
    preferred_channel TEXT NOT NULL,
    estimated_price REAL NOT NULL,
    final_price REAL,
    quoted_by TEXT,
    quoted_at TEXT,
    payment_method TEXT NOT NULL,
    payment_provider TEXT NOT NULL,
    payment_reference TEXT,
    payment_status TEXT NOT NULL,
    status TEXT NOT NULL,
    assigned_worker_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    approved_at TEXT,
    FOREIGN KEY (student_id) REFERENCES students(id),
    FOREIGN KEY (assigned_worker_id) REFERENCES workers(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL,
    sender_role TEXT NOT NULL,
    sender_name TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (assignment_id) REFERENCES assignments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    note TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (assignment_id) REFERENCES assignments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS payouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL UNIQUE,
    worker_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    status TEXT NOT NULL,
    external_reference TEXT,
    released_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (assignment_id) REFERENCES assignments(id) ON DELETE CASCADE,
    FOREIGN KEY (worker_id) REFERENCES workers(id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audience_type TEXT NOT NULL,
    student_id INTEGER,
    worker_id INTEGER,
    assignment_id INTEGER,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    tone TEXT NOT NULL DEFAULT 'muted',
    action_url TEXT,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    read_at TEXT,
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (worker_id) REFERENCES workers(id) ON DELETE CASCADE,
    FOREIGN KEY (assignment_id) REFERENCES assignments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id INTEGER,
    audience_type TEXT NOT NULL,
    channel TEXT NOT NULL,
    recipient TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    external_reference TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audience_type TEXT NOT NULL,
    student_id INTEGER,
    worker_id INTEGER,
    assignment_id INTEGER,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    content_encoding TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (worker_id) REFERENCES workers(id) ON DELETE CASCADE,
    FOREIGN KEY (assignment_id) REFERENCES assignments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS password_reset_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id INTEGER NOT NULL,
    delivery_channel TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (worker_id) REFERENCES workers(id) ON DELETE CASCADE
);
"""


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        db = sqlite3.connect(current_app.config["DATABASE"])
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        g.db = db
    return g.db


def close_db(_error=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    db.executescript(SCHEMA_SQL)
    db.commit()


def init_app(app) -> None:
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()
        migrate_db()
        if current_app.config["SEED_DEMO_DATA"]:
            seed_demo_data()


def migrate_db() -> None:
    db = get_db()
    student_columns = {row["name"] for row in db.execute("PRAGMA table_info(students)").fetchall()}
    worker_columns = {row["name"] for row in db.execute("PRAGMA table_info(workers)").fetchall()}
    assignment_columns = {row["name"] for row in db.execute("PRAGMA table_info(assignments)").fetchall()}

    if "is_blocked" not in student_columns:
        db.execute("ALTER TABLE students ADD COLUMN is_blocked INTEGER NOT NULL DEFAULT 0")
    if "blocked_at" not in student_columns:
        db.execute("ALTER TABLE students ADD COLUMN blocked_at TEXT")
    if "blocked_reason" not in student_columns:
        db.execute("ALTER TABLE students ADD COLUMN blocked_reason TEXT")

    if "email" not in worker_columns:
        db.execute("ALTER TABLE workers ADD COLUMN email TEXT")
    if "approval_status" not in worker_columns:
        db.execute("ALTER TABLE workers ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'APPROVED'")
    if "approved_at" not in worker_columns:
        db.execute("ALTER TABLE workers ADD COLUMN approved_at TEXT")
    if "approved_by" not in worker_columns:
        db.execute("ALTER TABLE workers ADD COLUMN approved_by TEXT")
    if "removed_at" not in worker_columns:
        db.execute("ALTER TABLE workers ADD COLUMN removed_at TEXT")

    if "budget_min" not in assignment_columns:
        db.execute("ALTER TABLE assignments ADD COLUMN budget_min REAL")
    if "budget_max" not in assignment_columns:
        db.execute("ALTER TABLE assignments ADD COLUMN budget_max REAL")
    if "quoted_by" not in assignment_columns:
        db.execute("ALTER TABLE assignments ADD COLUMN quoted_by TEXT")
    if "quoted_at" not in assignment_columns:
        db.execute("ALTER TABLE assignments ADD COLUMN quoted_at TEXT")

    db.execute(
        """
        UPDATE workers
        SET approval_status = COALESCE(NULLIF(approval_status, ''), 'APPROVED')
        """
    )
    db.execute(
        """
        UPDATE workers
        SET approved_at = COALESCE(approved_at, created_at),
            approved_by = COALESCE(approved_by, 'system')
        WHERE approval_status = 'APPROVED'
        """
    )
    db.execute(
        """
        UPDATE students
        SET is_blocked = COALESCE(is_blocked, 0)
        """
    )
    db.execute(
        """
        UPDATE assignments
        SET budget_min = COALESCE(budget_min, final_price, estimated_price),
            budget_max = COALESCE(budget_max, final_price, estimated_price)
        """
    )
    db.execute(
        """
        UPDATE assignments
        SET quoted_at = COALESCE(quoted_at, updated_at),
            quoted_by = COALESCE(quoted_by, 'system')
        WHERE final_price IS NOT NULL
        """
    )
    db.commit()


def seed_demo_data() -> None:
    db = get_db()
    timezone_name = current_app.config["APP_TIMEZONE"]

    worker_exists = db.execute("SELECT COUNT(*) AS total FROM workers").fetchone()["total"]
    if not worker_exists:
        created_at = now_iso(timezone_name)
        workers = [
            (
                "Neha Sharma",
                "neha.writer",
                "neha@example.com",
                hash_password("demo123"),
                "+919876500001",
                "Reports, Word files, business case studies",
                "UPI",
                "neha@upi",
                "APPROVED",
                created_at,
                "owner",
                created_at,
            ),
            (
                "Aman Verma",
                "aman.slides",
                "aman@example.com",
                hash_password("demo123"),
                "+919876500002",
                "Presentations, handwritten notes, design cleanup",
                "Bank Transfer",
                "aman@bank",
                "APPROVED",
                created_at,
                "owner",
                created_at,
            ),
        ]
        db.executemany(
            """
            INSERT INTO workers
                (
                    full_name, username, email, password_hash, whatsapp, expertise, payout_method,
                    payout_target, approval_status, approved_at, approved_by, created_at
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            workers,
        )

    student_exists = db.execute("SELECT COUNT(*) AS total FROM students").fetchone()["total"]
    if not student_exists:
        created_at = now_iso(timezone_name)
        db.executemany(
            "INSERT INTO students (full_name, email, whatsapp, created_at) VALUES (?, ?, ?, ?)",
            [
                ("Riya Mehta", "riya@example.com", "+919911223344", created_at),
                ("Karan Gupta", "karan@example.com", "+918888776655", created_at),
            ],
        )

    assignment_exists = db.execute("SELECT COUNT(*) AS total FROM assignments").fetchone()["total"]
    if assignment_exists:
        db.commit()
        return

    timezone = get_timezone(timezone_name)
    now = datetime.now(timezone).replace(second=0, microsecond=0)
    student_rows = db.execute("SELECT id, email FROM students ORDER BY id").fetchall()
    worker_rows = db.execute("SELECT id FROM workers ORDER BY id").fetchall()
    provider = current_app.config["PAYMENT_PROVIDER"]

    assignments = [
        (
            "AW-DEMO-101",
            student_rows[0]["id"],
            "Thermodynamics handwritten lab record",
            "HANDWRITTEN",
            "HANDWRITTEN",
            "ADVANCED",
            24,
            (now + timedelta(days=3)).isoformat(),
            "Need neat blue-ink handwriting, labelled diagrams, and references exactly as shared.",
            1500,
            1800,
            "Boys Hostel Block C, University Campus",
            "Block C",
            "217",
            "Call at north gate before delivery.",
            "WHATSAPP",
            1650,
            1650,
            "owner",
            now.isoformat(),
            "UPI",
            provider,
            "DEMO-SEED-001",
            "PAID",
            "PAID",
            None,
            now.isoformat(),
            now.isoformat(),
            None,
        ),
        (
            "AW-DEMO-102",
            student_rows[1]["id"],
            "Entrepreneurship presentation deck",
            "PRESENTATION",
            "ONLINE",
            "STANDARD",
            14,
            (now + timedelta(days=2)).isoformat(),
            "Need a crisp 12-14 slide deck with charts, speaker notes, and modern visuals.",
            1100,
            1400,
            "",
            "",
            "",
            "Share Google Drive link once completed.",
            "EMAIL",
            1250,
            1250,
            "owner",
            now.isoformat(),
            "CARD",
            provider,
            "DEMO-SEED-002",
            "PAID",
            "IN_PROGRESS",
            worker_rows[1]["id"],
            now.isoformat(),
            now.isoformat(),
            None,
        ),
    ]

    db.executemany(
        """
        INSERT INTO assignments (
            public_id, student_id, title, service_type, submission_mode, complexity, page_count, deadline,
            requirements, budget_min, budget_max, delivery_address, hostel_name, room_number, location_notes, preferred_channel,
            estimated_price, final_price, quoted_by, quoted_at, payment_method, payment_provider, payment_reference, payment_status,
            status, assigned_worker_id, created_at, updated_at, approved_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        assignments,
    )

    assignment_rows = db.execute("SELECT id, public_id FROM assignments ORDER BY id").fetchall()
    history = [
        (
            assignment_rows[0]["id"],
            "PAID",
            "Payment received. Assignment is now visible to approved workers.",
            "system",
            now.isoformat(),
        ),
        (
            assignment_rows[1]["id"],
            "PAID",
            "Payment received. Assignment is now visible to approved workers.",
            "system",
            now.isoformat(),
        ),
        (
            assignment_rows[1]["id"],
            "IN_PROGRESS",
            "Worker started building the presentation and is preparing the charts.",
            "worker",
            now.isoformat(),
        ),
    ]
    db.executemany(
        "INSERT INTO status_history (assignment_id, status, note, actor_role, created_at) VALUES (?, ?, ?, ?, ?)",
        history,
    )

    db.executemany(
        "INSERT INTO messages (assignment_id, sender_role, sender_name, body, created_at) VALUES (?, ?, ?, ?, ?)",
        [
            (
                assignment_rows[1]["id"],
                "worker",
                "Aman Verma",
                "I have outlined the slide flow and will share the completed deck before the deadline.",
                now.isoformat(),
            ),
        ],
    )
    db.commit()
