import unittest

from compiler.symbol_table import HashTable, Symbol, SymbolTable


def var(name, type_="int", **kw):
    return Symbol(name, kw.pop("kind", "variable"), type_, **kw)


class HashTableTests(unittest.TestCase):
    def test_djb2_hash_is_deterministic(self):
        self.assertEqual(HashTable.hash_name("a"), 5381 * 33 + ord("a"))
        self.assertEqual(HashTable.hash_name("abc"), HashTable.hash_name("abc"))

    def test_insert_lookup_delete(self):
        table = HashTable()
        self.assertIsNotNone(table.insert(var("x")))
        self.assertEqual(table.lookup("x").name, "x")
        self.assertIsNone(table.lookup("y"))
        self.assertTrue(table.delete("x"))
        self.assertFalse(table.delete("x"))
        self.assertEqual(table.count, 0)

    def test_duplicate_is_rejected(self):
        table = HashTable()
        table.insert(var("x"))
        self.assertIsNone(table.insert(var("x")))
        self.assertEqual(table.count, 1)

    def test_resize_keeps_every_entry(self):
        table = HashTable(capacity=4)
        for i in range(50):
            table.insert(var(f"v{i}"))
        self.assertGreater(table.capacity, 4)
        self.assertGreater(table.resizes, 0)
        self.assertEqual(table.count, 50)
        self.assertTrue(all(table.lookup(f"v{i}") for i in range(50)))
        self.assertLessEqual(table.count / table.capacity, table.max_load)

    def test_collisions_are_chained(self):
        table = HashTable(capacity=1, max_load=100)
        for name in ("a", "b", "c"):
            table.insert(var(name))
        stats = table.stats()
        self.assertEqual(stats["longest_chain"], 3)
        self.assertEqual(stats["collisions"], 2)
        _, _, probes = table.find("c")
        self.assertEqual(probes, 3)


class SymbolTableTests(unittest.TestCase):
    def setUp(self):
        self.st = SymbolTable(with_builtins=False)

    def test_inner_scope_shadows_outer(self):
        self.st.insert(var("x", "int"))
        self.st.enter_scope("inner")
        self.st.insert(var("x", "float"))
        self.assertEqual(self.st.lookup("x").type, "float")
        self.st.exit_scope()
        self.assertEqual(self.st.lookup("x").type, "int")

    def test_name_disappears_after_scope_exit(self):
        self.st.enter_scope("block")
        self.st.insert(var("tmp"))
        self.assertIsNotNone(self.st.lookup("tmp"))
        self.st.exit_scope()
        self.assertIsNone(self.st.lookup("tmp"))

    def test_same_scope_redeclaration_rejected(self):
        ok, _ = self.st.insert(var("x", line=1))
        self.assertTrue(ok)
        ok, existing = self.st.insert(var("x", line=2))
        self.assertFalse(ok)
        self.assertEqual(existing.line, 1)
        self.assertEqual(self.st.metrics["insert_rejected"], 1)

    def test_cannot_exit_global_scope(self):
        with self.assertRaises(RuntimeError):
            self.st.exit_scope()

    def test_delete_only_affects_current_scope(self):
        self.st.insert(var("g"))
        self.st.enter_scope("inner")
        self.assertFalse(self.st.delete("g"))
        self.st.exit_scope()
        self.assertTrue(self.st.delete("g"))

    def test_storage_offsets(self):
        self.st.insert(var("a", "int"))
        self.st.insert(var("c", "char"))
        self.st.insert(var("arr", "int", kind="array", array_size=3))
        self.st.insert(var("f", "float"))
        symbols = {s.name: s for s in self.st.current.table.items()}
        self.assertEqual((symbols["a"].offset, symbols["a"].size), (0, 4))
        self.assertEqual((symbols["c"].offset, symbols["c"].size), (4, 1))
        self.assertEqual((symbols["arr"].offset, symbols["arr"].size), (5, 12))
        self.assertEqual(symbols["f"].offset, 17)

    def test_metrics_and_log(self):
        self.st.insert(var("x"))
        self.st.lookup("x")
        self.st.lookup("nope")
        m = self.st.metrics
        self.assertEqual((m["inserts"], m["lookups"], m["lookup_hits"], m["lookup_misses"]), (1, 2, 1, 1))
        self.assertEqual([e["op"] for e in self.st.log][-3:], ["insert", "lookup", "lookup"])
        self.assertFalse(self.st.log[-1]["ok"])

    def test_builtins_are_preinstalled_but_not_counted(self):
        st = SymbolTable()
        self.assertIsNotNone(st.lookup("printf"))
        self.assertTrue(st.lookup("printf").variadic)
        self.assertEqual(st.symbol_count(), 0)
        self.assertEqual(st.log[0]["op"], "enter_scope")

    def test_to_dict_lists_closed_scopes(self):
        self.st.enter_scope("f", "function")
        self.st.insert(var("p", kind="parameter"))
        self.st.exit_scope()
        data = self.st.to_dict()
        self.assertEqual([s["name"] for s in data["scopes"]], ["global", "f"])
        self.assertTrue(data["scopes"][1]["closed"])
        self.assertEqual(data["active_scopes"], ["global"])


if __name__ == "__main__":
    unittest.main()
