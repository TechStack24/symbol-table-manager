"""Flask web application for the Symbol Table Management System."""

import hashlib
import os
import secrets
import tempfile
from datetime import timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template, request

import auth
from compiler import analyze_source, run_commands
from compiler.playground import MAX_COMMANDS

BASE_DIR = Path(__file__).resolve().parent
SAMPLES_DIR = BASE_DIR / "samples"
MAX_SOURCE_CHARS = 20_000


def load_samples():
    samples = []
    for path in sorted(SAMPLES_DIR.glob("*.c")):
        # "01_scopes_and_shadowing.c" -> "Scopes And Shadowing"
        title = path.stem.split("_", 1)[-1].replace("_", " ").title()
        samples.append({"id": path.stem, "title": title, "code": path.read_text(encoding="utf-8")})
    return samples


def database_url_from_env():
    """Postgres connection string from the environment (names used by Vercel/Neon/Supabase)."""
    for name in ("DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL"):
        url = os.environ.get(name)
        if url:
            return "postgresql://" + url[len("postgres://"):] if url.startswith("postgres://") else url
    return None


def load_secret_key(instance_path, database_url):
    """SECRET_KEY from the environment; else derived from the database URL (stable across
    serverless instances and secret, because the URL holds the database password); else a random
    key saved once in instance/secret_key."""
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    if database_url:
        return hashlib.sha256(("symtab-session-key:" + database_url).encode()).hexdigest()
    key_file = Path(instance_path) / "secret_key"
    try:
        if not key_file.exists():
            key_file.write_text(secrets.token_hex(32))
        return key_file.read_text().strip()
    except OSError:                                   # read-only disk: key lasts for this process only
        return secrets.token_hex(32)


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024
    on_vercel = bool(os.environ.get("VERCEL"))
    instance_path = app.instance_path
    try:
        os.makedirs(instance_path, exist_ok=True)
    except OSError:                                   # read-only deployment directory
        instance_path = tempfile.gettempdir()
    app.config["ON_VERCEL"] = on_vercel
    app.config["DATABASE_URL"] = database_url_from_env()
    app.config["DATABASE"] = os.path.join(instance_path, "users.db")
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SECURE"] = on_vercel
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):              # Flask pre-sets it to None
        app.config["SECRET_KEY"] = load_secret_key(instance_path, app.config["DATABASE_URL"])
    auth.init_db(app)
    app.register_blueprint(auth.bp)

    @app.context_processor
    def inject_user():
        return {"current_user": auth.current_user(), "csrf": auth.csrf_token()}

    def error(message, status=400):
        return jsonify({"error": message}), status

    @app.get("/")
    @auth.login_required
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/api/samples")
    @auth.login_required
    def samples():
        return jsonify(load_samples())

    @app.post("/api/analyze")
    @auth.login_required
    def analyze():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("source"), str):
            return error("Send JSON like {\"source\": \"<program text>\"}.")
        source = data["source"]
        if len(source) > MAX_SOURCE_CHARS:
            return error(f"Source is too long (limit is {MAX_SOURCE_CHARS} characters).")
        return jsonify(analyze_source(source))

    @app.post("/api/playground")
    @auth.login_required
    def playground():
        data = request.get_json(silent=True)
        commands = data.get("commands") if isinstance(data, dict) else None
        if not isinstance(commands, list) or not all(isinstance(c, str) for c in commands):
            return error("Send JSON like {\"commands\": [\"insert x int\", \"lookup x\"]}.")
        if len(commands) > MAX_COMMANDS:
            return error(f"Too many commands (limit is {MAX_COMMANDS}).")
        if any(len(c) > 200 for c in commands):
            return error("A command is too long (limit is 200 characters).")
        return jsonify(run_commands(commands))

    @app.errorhandler(404)
    def not_found(_):
        if request.path.startswith("/api/"):
            return error("Not found.", 404)
        return "Page not found", 404

    @app.errorhandler(413)
    def too_large(_):
        return error("Request body is too large.", 413)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
