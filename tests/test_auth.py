import os
import tempfile
import unittest

import auth
from tests.test_app import csrf_from, make_client, register


class AuthTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.client = self.new_client()
        auth._failures.clear()

    def new_client(self):
        """A fresh browser (no cookies) talking to the same user database."""
        return make_client(self._tmp.name)

    def login(self, client, username="alice", password="secret123", **extra):
        return client.post("/login", data={
            "csrf": csrf_from(client, "/login"), "username": username, "password": password, **extra})

    # ------------------------------------------------------------ protection
    def test_home_redirects_to_login_when_logged_out(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.headers["Location"])

    def test_api_returns_401_when_logged_out(self):
        self.assertEqual(self.client.post("/api/analyze", json={"source": "int x;"}).status_code, 401)
        self.assertEqual(self.client.post("/api/playground", json={"commands": []}).status_code, 401)
        self.assertEqual(self.client.get("/api/samples").status_code, 401)

    def test_health_stays_public(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)

    # ------------------------------------------------------------ register
    def test_register_logs_in_and_stores_only_a_hash(self):
        res = register(self.client)
        self.assertEqual(res.status_code, 302)
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"alice", page.data)
        self.assertEqual(self.client.post("/api/analyze", json={"source": "int main(){return 0;}"}).status_code, 200)
        with self.client.application.app_context():
            row = auth.get_db().execute("SELECT password_hash FROM users").fetchone()
            auth.close_db()
        self.assertNotIn("secret123", row["password_hash"])

    def test_register_validation(self):
        cases = [
            (dict(username="ab"), "Username must be"),
            (dict(username="bad name!"), "Username must be"),
            (dict(password="short"), "at least 8"),
            (dict(confirm="different1"), "do not match"),
        ]
        for kwargs, expected in cases:
            args = dict(username="bob", password="longenough1")
            args.update(kwargs)
            res = register(self.client, **args)
            self.assertEqual(res.status_code, 200, kwargs)
            self.assertIn(expected, res.get_data(as_text=True), kwargs)

    def test_duplicate_username_is_rejected_case_insensitively(self):
        register(self.client, "carol")
        other = self.new_client()
        res = register(other, "CAROL")
        self.assertIn("already taken", res.get_data(as_text=True))

    def test_register_needs_csrf_token(self):
        res = self.client.post("/register", data={"username": "dave", "password": "longenough1", "confirm": "longenough1"})
        self.assertIn("session expired", res.get_data(as_text=True))

    # --------------------------------------------------------------- login
    def test_login_and_logout_round_trip(self):
        register(self.client, "erin", "password99")
        self.client.post("/logout", data={"csrf": csrf_from(self.client, "/")})
        self.assertEqual(self.client.get("/").status_code, 302)
        res = self.login(self.client, "erin", "password99")
        self.assertEqual(res.status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_logout_requires_post_and_csrf(self):
        register(self.client, "frank", "password99")
        self.assertEqual(self.client.get("/logout").status_code, 405)
        self.client.post("/logout", data={"csrf": "wrong"})
        self.assertEqual(self.client.get("/").status_code, 200)        # still logged in

    def test_wrong_password_and_unknown_user_give_same_message(self):
        register(self.client, "gina", "password99")
        fresh = self.new_client()
        bad_pw = self.login(fresh, "gina", "nope-nope").get_data(as_text=True)
        no_user = self.login(fresh, "nobody", "nope-nope").get_data(as_text=True)
        self.assertIn("Wrong username or password", bad_pw)
        self.assertIn("Wrong username or password", no_user)

    def test_repeated_failures_are_throttled(self):
        register(self.client, "hank", "password99")
        fresh = self.new_client()
        for _ in range(auth.MAX_FAILURES):
            self.login(fresh, "hank", "wrong-pass")
        res = self.login(fresh, "hank", "password99")            # even the right password is blocked
        self.assertIn("Too many failed attempts", res.get_data(as_text=True))

    def test_login_redirect_only_stays_on_this_site(self):
        register(self.client, "ivy", "password99")
        fresh = self.new_client()
        res = self.login(fresh, "ivy", "password99", next="https://evil.example/")
        self.assertEqual(res.headers["Location"], "/")
        fresh2 = self.new_client()
        res = self.login(fresh2, "ivy", "password99", next="//evil.example")
        self.assertEqual(res.headers["Location"], "/")

    def test_login_returns_to_requested_page(self):
        register(self.client, "jack", "password99")
        fresh = self.new_client()
        res = self.login(fresh, "jack", "password99", next="/?tab=playground")
        self.assertEqual(res.headers["Location"], "/?tab=playground")


if __name__ == "__main__":
    unittest.main()


POSTGRES_URL = os.environ.get("TEST_DATABASE_URL")


@unittest.skipUnless(POSTGRES_URL, "set TEST_DATABASE_URL to run the tests against Postgres")
class PostgresAuthTests(AuthTests):
    """The very same tests, against a real Postgres database (what Vercel uses)."""

    def new_client(self):
        return make_client(self._tmp.name, POSTGRES_URL)

    def setUp(self):
        import psycopg
        with psycopg.connect(POSTGRES_URL, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS users")
        super().setUp()


class SchemaMigrationTests(unittest.TestCase):
    def test_database_from_first_version_is_upgraded(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "users.db")
            old = sqlite3.connect(path)
            old.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL "
                        "UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL "
                        "DEFAULT CURRENT_TIMESTAMP)")
            from werkzeug.security import generate_password_hash
            old.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                        ("OldUser", generate_password_hash("password99")))
            old.commit()
            old.close()
            from app import create_app
            client = create_app({"TESTING": True, "SECRET_KEY": "k", "DATABASE": path,
                                 "DATABASE_URL": None}).test_client()
            res = client.post("/login", data={"csrf": csrf_from(client, "/login"),
                                              "username": "olduser", "password": "password99"})
            self.assertEqual(res.status_code, 302)
            self.assertEqual(client.get("/").status_code, 200)


class VercelSetupTests(unittest.TestCase):
    def test_clear_message_when_database_url_is_missing_on_vercel(self):
        from app import create_app
        app = create_app({"TESTING": True, "SECRET_KEY": "k", "ON_VERCEL": True, "DATABASE_URL": None})
        res = app.test_client().get("/login")
        self.assertEqual(res.status_code, 503)
        self.assertIn("DATABASE_URL", res.get_data(as_text=True))
        self.assertEqual(app.test_client().get("/api/health").status_code, 200)

    def test_secret_key_is_stable_across_instances_when_database_url_is_set(self):
        from app import load_secret_key
        os.environ.pop("SECRET_KEY", None)
        a = load_secret_key("/nonexistent", "postgresql://u:p@h/db")
        b = load_secret_key("/nonexistent", "postgresql://u:p@h/db")
        self.assertEqual(a, b)
        self.assertNotEqual(a, load_secret_key("/nonexistent", "postgresql://u:other@h/db"))


class DefaultConfigTests(unittest.TestCase):
    def test_app_generates_a_secret_key_when_none_is_given(self):
        import os
        from app import create_app
        os.environ.pop("SECRET_KEY", None)
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app({"TESTING": True, "DATABASE": os.path.join(tmp, "u.db")})
            self.assertTrue(app.config["SECRET_KEY"])
            client = app.test_client()
            res = client.get("/register")                    # needs a working session
            self.assertEqual(res.status_code, 200)
