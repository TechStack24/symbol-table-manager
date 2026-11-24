import os
import re
import tempfile
import unittest

from app import MAX_SOURCE_CHARS, create_app
from compiler.playground import MAX_COMMANDS


def make_client(tmpdir, database_url=None):
    """A test client with its own empty user database (SQLite file, or Postgres if a URL is given)."""
    app = create_app({"TESTING": True, "SECRET_KEY": "test-key",
                      "DATABASE": os.path.join(tmpdir, "users.db"),
                      "DATABASE_URL": database_url})
    return app.test_client()


def csrf_from(client, path):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def register(client, username="alice", password="secret123", confirm=None):
    return client.post("/register", data={
        "csrf": csrf_from(client, "/register"), "username": username,
        "password": password, "confirm": password if confirm is None else confirm,
    })


class AppTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.client = make_client(self._tmp.name)
        register(self.client)                      # registering also logs the user in

    def test_index_page_and_static_assets(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Symbol Table Management System", res.data)
        for path in ("/static/js/app.js", "/static/css/style.css"):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200, path)
            res.close()

    def test_health(self):
        self.assertEqual(self.client.get("/api/health").get_json(), {"status": "ok"})

    def test_samples(self):
        data = self.client.get("/api/samples").get_json()
        self.assertEqual(len(data), 5)
        self.assertEqual(data[0]["id"], "01_scopes_and_shadowing")
        self.assertEqual(data[0]["title"], "Scopes And Shadowing")
        self.assertIn("int main", data[0]["code"])

    def test_analyze_success(self):
        res = self.client.post("/api/analyze", json={"source": "int main() { int a = 1; return a; }"})
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["summary"]["symbols"], 2)
        self.assertEqual([s["name"] for s in body["scopes"]], ["global", "main"])

    def test_analyze_reports_errors_with_200(self):
        body = self.client.post("/api/analyze", json={"source": "int main() { return x; }"}).get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["summary"]["errors"], 1)

    def test_analyze_validation(self):
        for payload in ({}, {"source": 5}, {"source": None}, []):
            res = self.client.post("/api/analyze", json=payload)
            self.assertEqual(res.status_code, 400, payload)
            self.assertIn("error", res.get_json())
        res = self.client.post("/api/analyze", data="not json", content_type="text/plain")
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/api/analyze", json={"source": "x" * (MAX_SOURCE_CHARS + 1)})
        self.assertEqual(res.status_code, 400)

    def test_request_body_limit(self):
        res = self.client.post("/api/analyze", json={"source": "x" * (300 * 1024)})
        self.assertEqual(res.status_code, 413)
        self.assertIn("error", res.get_json())

    def test_playground(self):
        res = self.client.post("/api/playground", json={"commands": ["insert x int", "lookup x", "lookup y"]})
        self.assertEqual(res.status_code, 200)
        self.assertEqual([r["ok"] for r in res.get_json()["results"]], [True, True, False])

    def test_playground_validation(self):
        for payload in ({}, {"commands": "insert x int"}, {"commands": [1, 2]}):
            self.assertEqual(self.client.post("/api/playground", json=payload).status_code, 400)
        too_many = {"commands": ["lookup x"] * (MAX_COMMANDS + 1)}
        self.assertEqual(self.client.post("/api/playground", json=too_many).status_code, 400)
        too_long = {"commands": ["insert " + "a" * 300 + " int"]}
        self.assertEqual(self.client.post("/api/playground", json=too_long).status_code, 400)

    def test_unknown_api_route_returns_json_404(self):
        res = self.client.get("/api/nope")
        self.assertEqual(res.status_code, 404)
        self.assertIn("error", res.get_json())


if __name__ == "__main__":
    unittest.main()
