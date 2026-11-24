import unittest

from compiler.playground import MAX_COMMANDS, parse_command, run_commands


class ParseCommandTests(unittest.TestCase):
    def test_valid_commands(self):
        self.assertEqual(parse_command("insert x int")["kind"], "variable")
        cmd = parse_command("INSERT buf char array 16")
        self.assertEqual((cmd["kind"], cmd["size"]), ("array", 16))
        self.assertEqual(parse_command("enter")["name"], None)
        self.assertEqual(parse_command("lookup  x ")["name"], "x")

    def test_invalid_commands(self):
        for bad in ("", "insert x", "insert 1x int", "insert x banana", "insert x int blob",
                    "insert a int array", "insert a int array 0", "insert x int variable 4",
                    "insert v void variable", "lookup", "delete a b", "exit now", "jump"):
            with self.assertRaises(ValueError, msg=bad):
                parse_command(bad)


class RunCommandsTests(unittest.TestCase):
    def test_scoping_walkthrough(self):
        state = run_commands([
            "insert g int",             # 1
            "enter f",                  # 2
            "insert g float",           # 3 shadows
            "lookup g",                 # 4 finds the float
            "exit",                     # 5
            "lookup g",                 # 6 finds the int again
            "lookup nothing",           # 7 miss
            "insert g char",            # 8 duplicate
        ])
        results = state["results"]
        self.assertEqual([r["ok"] for r in results], [True, True, True, True, True, True, False, False])
        self.assertIn("float g", results[3]["message"])
        self.assertIn("int g", results[5]["message"])
        self.assertEqual(state["active_scopes"], ["global"])
        self.assertEqual([s["name"] for s in state["scopes"]], ["global", "f"])

    def test_errors_do_not_stop_replay(self):
        state = run_commands(["exit", "bogus", "insert a int", "lookup a"])
        self.assertEqual([r["ok"] for r in state["results"]], [False, False, True, True])
        self.assertIn("global", state["results"][0]["message"])

    def test_delete_and_array(self):
        state = run_commands(["insert buf char array 16", "delete buf", "lookup buf"])
        self.assertEqual([r["ok"] for r in state["results"]], [True, True, False])
        self.assertEqual(state["metrics"]["deletes"], 1)

    def test_empty_history(self):
        state = run_commands([])
        self.assertEqual(state["results"], [])
        self.assertEqual(state["active_scopes"], ["global"])

    def test_limit_constant_is_sensible(self):
        self.assertGreaterEqual(MAX_COMMANDS, 50)


if __name__ == "__main__":
    unittest.main()
