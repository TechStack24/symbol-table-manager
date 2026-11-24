"""Login system: register, login, logout.

* Users are stored in a small database: SQLite locally (a file), or Postgres when the
  ``DATABASE_URL`` environment variable is set (needed on Vercel, whose disk is not persistent).
* Passwords are never stored - only a salted hash (werkzeug, PBKDF2/scrypt).
* The logged-in user is remembered in Flask's signed session cookie.
* Forms carry a CSRF token; repeated wrong passwords are throttled.
"""

import re
import secrets
import sqlite3
import time
from functools import wraps
from urllib.parse import urlsplit

from flask import (
    Blueprint, current_app, g, jsonify, redirect, render_template, request, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

try:                                    # only needed when DATABASE_URL points at Postgres
    import psycopg
    from psycopg.rows import dict_row
except ImportError:                     # pragma: no cover - local SQLite use does not need it
    psycopg = None

bp = Blueprint("auth", __name__)
INTEGRITY_ERRORS = (sqlite3.IntegrityError,) + ((psycopg.IntegrityError,) if psycopg else ())

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,30}$")
MIN_PASSWORD = 8
MAX_PASSWORD = 128
MAX_FAILURES = 5            # wrong passwords allowed ...
FAILURE_WINDOW = 300        # ... within this many seconds
_failures = {}              # (ip, username) -> [timestamps]


# --------------------------------------------------------------------- database
class Db:
    """Thin wrapper so the same SQL (written with ? placeholders) runs on SQLite and Postgres."""

    def __init__(self, conn, postgres):
        self.conn = conn
        self.postgres = postgres

    def execute(self, sql, params=()):
        if self.postgres:
            sql = sql.replace("?", "%s")
        return self.conn.execute(sql, params)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


def connect():
    url = current_app.config.get("DATABASE_URL")
    if url:
        if psycopg is None:
            raise RuntimeError("DATABASE_URL is set but the 'psycopg' package is not installed")
        # prepare_threshold=None: safe behind connection poolers (Neon, Supabase, PgBouncer)
        conn = psycopg.connect(url, row_factory=dict_row, connect_timeout=10, prepare_threshold=None)
        return Db(conn, postgres=True)
    conn = sqlite3.connect(current_app.config["DATABASE"])
    conn.row_factory = sqlite3.Row
    return Db(conn, postgres=False)


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def ensure_schema(db):
    if db.postgres:
        db.execute(
            """CREATE TABLE IF NOT EXISTS users (
                   id             SERIAL PRIMARY KEY,
                   username       TEXT NOT NULL,
                   username_lower TEXT NOT NULL UNIQUE,
                   password_hash  TEXT NOT NULL,
                   created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
               )""")
        db.commit()
        return
    db.execute(
        """CREATE TABLE IF NOT EXISTS users (
               id             INTEGER PRIMARY KEY AUTOINCREMENT,
               username       TEXT NOT NULL,
               username_lower TEXT NOT NULL UNIQUE,
               password_hash  TEXT NOT NULL,
               created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )""")
    columns = [row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()]
    if "username_lower" not in columns:                 # database made by the first version
        db.execute("ALTER TABLE users ADD COLUMN username_lower TEXT")
        db.execute("UPDATE users SET username_lower = lower(username)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower ON users (username_lower)")
    db.commit()


def init_db(app):
    """Register request hooks. The table is created lazily on the first request, so a slow or
    missing database never stops the app from starting (important on serverless hosting)."""
    app.teardown_appcontext(close_db)

    @app.before_request
    def prepare_database():
        if request.path == "/api/health":
            return None
        if app.config.get("ON_VERCEL") and not app.config.get("DATABASE_URL"):
            return (
                "Setup needed: this site runs on Vercel, which has no persistent disk, so accounts "
                "cannot be stored. Add a Postgres database (for example Neon from the Vercel "
                "Marketplace) so that DATABASE_URL is set, add a SECRET_KEY environment variable, "
                "and redeploy.",
                503, {"Content-Type": "text/plain; charset=utf-8"})
        if not app.extensions.get("symtab_schema_ready"):
            db = get_db()
            try:
                ensure_schema(db)
            except Exception:                           # two cold starts racing to create the table
                db.rollback()
                ensure_schema(db)
            app.extensions["symtab_schema_ready"] = True
        return None


# ------------------------------------------------------------------- helpers
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def csrf_ok():
    sent = request.form.get("csrf", "")
    return bool(sent) and secrets.compare_digest(sent, session.get("csrf", ""))


def safe_next(target):
    """Only allow redirects back into this site (no open redirect)."""
    if not target:
        return url_for("index")
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return url_for("index")
    return target


def throttled(username):
    key = (request.remote_addr, username.lower())
    now = time.time()
    recent = [t for t in _failures.get(key, []) if now - t < FAILURE_WINDOW]
    _failures[key] = recent
    return len(recent) >= MAX_FAILURES


def record_failure(username):
    _failures.setdefault((request.remote_addr, username.lower()), []).append(time.time())


def clear_failures(username):
    _failures.pop((request.remote_addr, username.lower()), None)


def current_user():
    uid = session.get("user_id")
    if uid is None:
        return None
    row = get_db().execute("SELECT id, username FROM users WHERE id = ?", (uid,)).fetchone()
    if row is None:
        session.clear()
    return row


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Please log in first."}), 401
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        g.user = user
        return view(*args, **kwargs)
    return wrapped


def start_session(user_id, username):
    session.clear()                      # new session id after login (prevents fixation)
    session["user_id"] = user_id
    session["username"] = username
    session.permanent = True
    csrf_token()


# -------------------------------------------------------------------- routes
@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user() is not None:
        return redirect(url_for("index"))
    error, username = None, ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if not csrf_ok():
            error = "Your session expired. Please try again."
        elif not USERNAME_RE.match(username):
            error = "Username must be 3-30 characters: letters, digits or underscore."
        elif len(password) < MIN_PASSWORD:
            error = f"Password must be at least {MIN_PASSWORD} characters."
        elif len(password) > MAX_PASSWORD:
            error = f"Password must be at most {MAX_PASSWORD} characters."
        elif password != confirm:
            error = "The two passwords do not match."
        else:
            db = get_db()
            try:
                cur = db.execute(
                    "INSERT INTO users (username, username_lower, password_hash) "
                    "VALUES (?, ?, ?) RETURNING id",
                    (username, username.lower(), generate_password_hash(password)),
                )
                new_id = cur.fetchone()["id"]
                db.commit()
            except INTEGRITY_ERRORS:
                db.rollback()
                error = "That username is already taken."
            else:
                start_session(new_id, username)
                return redirect(url_for("index"))
    return render_template("auth.html", mode="register", error=error, username=username,
                           csrf=csrf_token(), next=request.args.get("next", ""))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user() is not None:
        return redirect(url_for("index"))
    error, username = None, ""
    target = request.values.get("next", "")
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not csrf_ok():
            error = "Your session expired. Please try again."
        elif throttled(username):
            error = "Too many failed attempts. Please wait a few minutes and try again."
        else:
            row = get_db().execute(
                "SELECT id, username, password_hash FROM users WHERE username_lower = ?", (username.lower(),)
            ).fetchone()
            if row is not None and check_password_hash(row["password_hash"], password):
                clear_failures(username)
                start_session(row["id"], row["username"])
                return redirect(safe_next(target))
            record_failure(username)
            error = "Wrong username or password."       # same message for both cases
    return render_template("auth.html", mode="login", error=error, username=username,
                           csrf=csrf_token(), next=target)


@bp.post("/logout")
def logout():
    if csrf_ok():
        session.clear()
    return redirect(url_for("auth.login"))
