import json
import unittest
from pathlib import Path

from compiler import analyze_source

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def messages(report, severity=None):
    return [d["message"] for d in report["diagnostics"] if severity in (None, d["severity"])]


def symbols_by_name(report):
    found = {}
    for scope in report["scopes"]:
        for sym in scope["symbols"]:
            if not sym["builtin"]:
                found.setdefault(sym["name"], []).append(sym)
    return found


def wrap(body):
    return "int main() {\n" + body + "\n    return 0;\n}\n"


class SampleProgramTests(unittest.TestCase):
    def read(self, name):
        return (SAMPLES / name).read_text(encoding="utf-8")

    def test_scopes_sample_is_clean_with_two_shadow_notes(self):
        report = analyze_source(self.read("01_scopes_and_shadowing.c"))
        self.assertTrue(report["ok"])
        self.assertEqual(report["summary"]["errors"], 0)
        self.assertEqual(report["summary"]["warnings"], 0)
        self.assertEqual(sum("shadows" in m for m in messages(report, "info")), 2)
        names = [s["name"] for s in report["scopes"]]
        self.assertEqual(names[:2], ["global", "main"])
        self.assertTrue(any(n.startswith("for@") for n in names))

    def test_functions_sample_is_clean(self):
        report = analyze_source(self.read("02_functions_and_recursion.c"))
        self.assertEqual(report["diagnostics"], [])
        funcs = {n for n, s in symbols_by_name(report).items() if s[0]["kind"] == "function"}
        self.assertEqual(funcs, {"factorial", "fib", "average", "main"})
        self.assertTrue(all(s[0]["defined"] for n, s in symbols_by_name(report).items() if n in funcs))

    def test_error_sample_reports_each_planted_problem(self):
        report = analyze_source(self.read("03_semantic_errors.c"))
        text = " | ".join(messages(report))
        for expected in (
            "redeclaration of 'counter'", "'unused_local' is declared but never used",
            "float to int", "'total' is used before it is initialized",
            "undeclared identifier 'b'", "expects 1 argument(s) but 2 given",
            "undeclared function 'ghost'", "redeclaration of 'a'",
            "'break' statement is not inside a loop", "void value used in return",
        ):
            self.assertIn(expected, text)
        self.assertFalse(report["ok"])
        self.assertEqual(report["summary"]["errors"], 7)

    def test_arrays_sample_only_warns_about_bounds(self):
        report = analyze_source(self.read("04_arrays_and_loops.c"))
        self.assertTrue(report["ok"])
        self.assertEqual(messages(report, "warning"),
                         ["index 5 is out of bounds for array 'scores' of size 5"])

    def test_every_report_is_json_serialisable(self):
        for path in SAMPLES.glob("*.c"):
            json.dumps(analyze_source(path.read_text(encoding="utf-8")))


class SymbolTableContentTests(unittest.TestCase):
    def test_attributes_stored_for_each_kind(self):
        report = analyze_source("int g = 1;\nfloat f(int a) { return a * 1.5; }\nint arr[4] = {1,2,3,4};\n")
        syms = symbols_by_name(report)
        self.assertEqual(syms["g"][0]["level"], 0)
        self.assertEqual(syms["f"][0]["signature"], "float f(int a)")
        self.assertEqual(syms["a"][0]["kind"], "parameter")
        self.assertEqual(syms["a"][0]["scope_name"], "f")
        self.assertEqual((syms["arr"][0]["size"], syms["arr"][0]["array_size"]), (16, 4))

    def test_references_are_recorded(self):
        report = analyze_source(wrap("    int n = 1;\n    n = n + 1;\n    printf(\"%d\", n);"))
        self.assertEqual(symbols_by_name(report)["n"][0]["refs"], [3, 4])

    def test_local_offsets_continue_inside_nested_block(self):
        report = analyze_source(wrap("    int a = 1;\n    { int b = 2; printf(\"%d\", b); }\n    printf(\"%d\", a);"))
        syms = symbols_by_name(report)
        self.assertEqual(syms["a"][0]["offset"], 0)
        self.assertEqual(syms["b"][0]["offset"], 4)

    def test_operation_log_is_recorded(self):
        report = analyze_source(wrap("    int a = 1;\n    printf(\"%d\", a);"))
        ops = {e["op"] for e in report["log"]}
        self.assertTrue({"insert", "lookup", "enter_scope", "exit_scope"} <= ops)
        self.assertGreater(report["metrics"]["lookup_hits"], 0)


class SemanticCheckTests(unittest.TestCase):
    def check(self, source, fragment, severity="error"):
        report = analyze_source(source)
        found = [m for m in messages(report, severity) if fragment in m]
        self.assertTrue(found, f"expected {severity} containing {fragment!r}, got {messages(report)}")
        return report

    def test_undeclared_identifier_and_no_cascade(self):
        report = self.check(wrap("    int a = missing + 1;\n    printf(\"%d\", a);"), "undeclared identifier 'missing'")
        self.assertEqual(report["summary"]["errors"], 1)

    def test_redeclaration_in_same_scope_but_not_in_nested_scope(self):
        self.check(wrap("    int a = 1;\n    int a = 2;\n    printf(\"%d\", a);"), "redeclaration of 'a'")
        ok = analyze_source(wrap("    int a = 1;\n    { int a = 2; printf(\"%d\", a); }\n    printf(\"%d\", a);"))
        self.assertTrue(ok["ok"])

    def test_variable_scope_ends_with_block(self):
        self.check(wrap("    { int inner = 1; printf(\"%d\", inner); }\n    inner = 2;"),
                   "undeclared identifier 'inner'")

    def test_for_loop_variable_is_scoped_to_loop(self):
        self.check(wrap("    for (int i = 0; i < 2; i++) { printf(\"%d\", i); }\n    i = 5;"),
                   "undeclared identifier 'i'")

    def test_float_to_int_narrowing_warning(self):
        self.check(wrap("    int a = 2.5;\n    printf(\"%d\", a);"), "float to int", "warning")

    def test_call_checks(self):
        self.check("int f(int a) { return a; }\n" + wrap("    f(1, 2);"), "expects 1 argument(s) but 2 given")
        self.check(wrap("    nothing(1);"), "undeclared function 'nothing'")
        self.check(wrap("    int v = 1;\n    v(2);"), "'v' is not a function")
        self.check("void p() { return; }\n" + wrap("    int a = p();\n    printf(\"%d\", a);"), "void value used")

    def test_return_checks(self):
        self.check("void f() { return 1; }", "should not return a value")
        self.check("int f() { int a = 1; printf(\"%d\", a); }", "has no return statement", "warning")
        self.check("int f() { return; }", "without a value", "warning")

    def test_prototype_then_definition_is_accepted_but_conflict_is_not(self):
        good = analyze_source("int f(int a);\nint f(int a) { return a; }\nint main() { return f(1); }")
        self.assertEqual(good["diagnostics"], [])
        self.check("int f(int a);\nfloat f(int a) { return 1.0; }", "conflicting types for 'f'")
        self.check("int f() { return 1; }\nint f() { return 2; }", "redefinition of function 'f'")

    def test_break_and_continue_need_a_loop(self):
        self.check(wrap("    break;"), "not inside a loop")
        ok = analyze_source(wrap("    while (1) { break; }\n    do { continue; } while (0);"))
        self.assertTrue(ok["ok"])

    def test_uninitialized_and_unused_warnings(self):
        self.check(wrap("    int a;\n    int b = a + 1;\n    printf(\"%d\", b);"), "'a' is used before it is initialized", "warning")
        self.check(wrap("    int never_used = 1;"), "'never_used' is declared but never used", "warning")
        report = analyze_source(wrap("    int a;\n    a = 3;\n    printf(\"%d\", a);"))
        self.assertEqual(report["diagnostics"], [])

    def test_array_checks(self):
        self.check(wrap("    int a[3] = {1,2,3};\n    printf(\"%d\", a[3]);"), "out of bounds", "warning")
        self.check(wrap("    int a[2] = {1,2,3};\n    printf(\"%d\", a[0]);"), "too many initializers")
        self.check(wrap("    int a[2] = {1,2};\n    int n = a;\n    printf(\"%d\", n);"), "used without a subscript")
        self.check(wrap("    int n = 1;\n    printf(\"%d\", n[0]);"), "is not an array")
        self.check(wrap("    int a[2] = {1,2};\n    printf(\"%d\", a[1.5]);"), "array index must be an integer")

    def test_division_by_zero_and_modulo_on_float(self):
        self.check(wrap("    int a = 4 / 0;\n    printf(\"%d\", a);"), "division by zero", "warning")
        self.check(wrap("    float f = 1.5;\n    int a = f % 2;\n    printf(\"%d\", a);"), "requires integer operands")

    def test_void_variable_is_rejected(self):
        self.check("void v;", "cannot be declared with type 'void'")

    def test_missing_main_is_only_a_note(self):
        report = analyze_source("int x;")
        self.assertTrue(report["ok"])
        self.assertIn("no 'main' function is defined", messages(report, "info"))

    def test_empty_source(self):
        report = analyze_source("")
        self.assertTrue(report["ok"])
        self.assertEqual(report["summary"]["symbols"], 0)
        self.assertEqual(report["diagnostics"], [])


class SyntaxRecoveryTests(unittest.TestCase):
    def test_missing_semicolon_is_reported_on_previous_line_and_parsing_continues(self):
        source = "int main() {\n    int a = 1\n    int b = 2;\n    return b;\n}\n"
        report = analyze_source(source)
        syntax = [d for d in report["diagnostics"] if d["phase"] == "syntax"]
        self.assertEqual(len(syntax), 1)
        self.assertEqual(syntax[0]["line"], 2)
        self.assertIn("expected ';'", syntax[0]["message"])
        self.assertIn("b", symbols_by_name(report))

    def test_unclosed_block_is_one_error_and_scopes_stay_balanced(self):
        report = analyze_source("int main() {\n    int a = 1;\n    {\n        a = 2;\n")
        self.assertGreaterEqual(report["summary"]["errors"], 1)
        self.assertEqual(report["active_scopes"], ["global"])

    def test_garbage_at_top_level_does_not_hang(self):
        report = analyze_source("x = 5;\n} } )\nint ok;\n")
        self.assertIn("ok", symbols_by_name(report))
        self.assertGreaterEqual(report["summary"]["errors"], 1)

    def test_lexical_errors_are_included(self):
        report = analyze_source("int a = 1 # 2;")
        self.assertTrue(any(d["phase"] == "lexical" for d in report["diagnostics"]))

    def test_pathological_inputs_terminate(self):
        for source in ("(((((", "int", "int f(", "for(;;", "else else else", "int a[", "{{{{}}", "int f(int,) {}"):
            report = analyze_source(source)
            self.assertEqual(report["active_scopes"], ["global"], source)


if __name__ == "__main__":
    unittest.main()


class CLanguageCoverageTests(unittest.TestCase):
    """Constructs that real-world C programs use (beyond the original mini-C)."""

    def run_src(self, src):
        return analyze_source(src)

    def assert_clean(self, src):
        report = self.run_src(src)
        bad = [d for d in report["diagnostics"] if d["severity"] == "error"]
        self.assertEqual(bad, [], bad)
        return report

    def test_extra_types_and_literal_suffixes(self):
        self.assert_clean(wrap(
            "    long a = 100000L; unsigned int u = 5U; double d = 2.5; short s = 1;\n"
            "    unsigned long long big = 1ULL; long double ld = 1.5L; float f = 1.5f;\n"
            "    printf(\"%ld %u %f %d %llu %Lf %f\", a, u, d, s, big, ld, f);"))

    def test_pointers_and_address_of(self):
        report = self.assert_clean(wrap(
            "    int n = 3; int *p = &n; int **pp = &p; char *s = \"hi\";\n"
            "    *p = 4; **pp = 5; p++; printf(\"%d %s\", n, s);"))
        syms = symbols_by_name(report)
        self.assertEqual(syms["pp"][0]["type"], "int **")
        self.assertEqual(syms["p"][0]["signature"], "int *p")

    def test_scanf_address_of_marks_initialized(self):
        report = analyze_source(wrap("    int n;\n    scanf(\"%d\", &n);\n    printf(\"%d\", n);"))
        self.assertEqual(messages(report, "warning"), [])
        self.assertEqual(report["summary"]["errors"], 0)

    def test_pointer_type_problems(self):
        r = analyze_source(wrap("    int n = 1;\n    int *p = n;\n    int q = *n;\n    printf(\"%d\", q);\n    p = &n;"))
        text = " | ".join(messages(r))
        self.assertIn("makes a pointer from an integer", text)
        self.assertIn("indirection requires a pointer", text)

    def test_switch_case_default(self):
        self.assert_clean(wrap(
            "    int n = 2;\n    switch (n) { case 1: printf(\"a\"); break; case 2: n++; break; default: break; }"))

    def test_switch_errors(self):
        r = analyze_source(wrap("    int n = 1;\n    switch (n) { case 1: break; case 1: break; default: break; default: break; }"))
        text = " | ".join(messages(r, "error"))
        self.assertIn("duplicate case value 1", text)
        self.assertIn("duplicate 'default'", text)
        r2 = analyze_source(wrap("    case 3: ;"))
        self.assertIn("not within a switch", " ".join(messages(r2, "error")))

    def test_break_allowed_in_switch_but_continue_is_not(self):
        r = analyze_source(wrap("    int n = 1;\n    switch (n) { case 1: continue; }"))
        self.assertIn("'continue' statement is not inside a loop", " ".join(messages(r, "error")))

    def test_ternary_bitwise_and_comma(self):
        self.assert_clean(wrap(
            "    int a = 5, b = 3, i, j;\n    int m = a > b ? a : b;\n    int x = (a & b) | (a ^ b) << 1;\n"
            "    for (i = 0, j = 10; i < j; i++, j--) { x += i; }\n    printf(\"%d %d\", m, x);"))

    def test_bitwise_on_float_is_rejected(self):
        r = analyze_source(wrap("    float f = 1.5; int x = f & 1;\n    printf(\"%d\", x);"))
        self.assertIn("requires integer operands", " ".join(messages(r, "error")))

    def test_two_dimensional_arrays(self):
        report = self.assert_clean(wrap(
            "    int m[2][3] = {{1,2,3},{4,5,6}}; char names[2][8] = {\"ab\",\"cd\"};\n"
            "    printf(\"%d %s\", m[1][2], names[0]);"))
        m = symbols_by_name(report)["m"][0]
        self.assertEqual((m["dims"], m["size"], m["signature"]), ([2, 3], 24, "int m[2][3]"))

    def test_array_size_inferred_from_initializer(self):
        report = self.assert_clean(wrap("    int a[] = {1,2,3,4};\n    char s[] = \"hey\";\n    printf(\"%d %s\", a[0], s);"))
        syms = symbols_by_name(report)
        self.assertEqual((syms["a"][0]["array_size"], syms["a"][0]["size"]), (4, 16))
        self.assertEqual(syms["s"][0]["array_size"], 4)

    def test_struct_members_get_their_own_scope(self):
        report = self.assert_clean(
            "struct Point { int x; int y; };\n" + wrap(
                "    struct Point p = {1, 2}; struct Point *q = &p;\n    p.x = q->y + 1;\n    printf(\"%d\", p.x);"))
        scope_names = [s["name"] for s in report["scopes"]]
        self.assertIn("struct Point", scope_names)
        tag = symbols_by_name(report)["struct Point"][0]
        self.assertEqual((tag["kind"], tag["size"]), ("struct", 8))
        self.assertEqual(symbols_by_name(report)["p"][0]["size"], 8)

    def test_struct_member_errors(self):
        r = analyze_source("struct P { int x; };\n" + wrap("    struct P p;\n    p.z = 1;\n    printf(\"%d\", p.x);"))
        self.assertIn("has no member named 'z'", " ".join(messages(r, "error")))

    def test_self_referential_struct_and_typedef(self):
        self.assert_clean(
            "typedef struct Node { int v; struct Node *next; } Node;\n"
            "int main() { Node *h = NULL; Node n; n.v = 1; n.next = h; h = &n; return h->v; }")

    def test_enum_constants_work_in_case_and_array_sizes(self):
        report = self.assert_clean(
            "enum Color { RED, GREEN = 5, BLUE };\n" + wrap(
                "    int a[BLUE]; enum Color c = GREEN;\n    switch (c) { case RED: break; case BLUE: break; }\n    a[0] = c;"))
        self.assertEqual(symbols_by_name(report)["BLUE"][0]["value"], 6)
        self.assertEqual(symbols_by_name(report)["a"][0]["array_size"], 6)

    def test_typedef_names_are_types(self):
        self.assert_clean("typedef unsigned int uint;\n" + wrap("    uint a = 3; size_t n = 2; printf(\"%u %lu\", a, n);"))

    def test_define_macros_are_expanded_and_recorded(self):
        report = self.assert_clean(
            "#define N 4\n#define SQ(x) ((x) * (x))\n" + wrap("    int a[N]; a[0] = SQ(N);\n    printf(\"%d\", a[0]);"))
        syms = symbols_by_name(report)
        self.assertEqual(syms["N"][0]["kind"], "macro")
        self.assertEqual(syms["a"][0]["array_size"], 4)
        self.assertEqual(syms["SQ"][0]["signature"], "#define SQ(x) ((x) * (x))")

    def test_casts_and_sizeof(self):
        self.assert_clean(wrap(
            "    int *p = (int *)malloc(sizeof(int) * 4); double d = (double)3 / 2;\n"
            "    printf(\"%d %f\", sizeof(p), d);\n    free(p);"))

    def test_library_functions_are_builtin(self):
        self.assert_clean(wrap(
            "    char s[20]; FILE *fp = fopen(\"a\", \"r\");\n    strcpy(s, \"x\"); printf(\"%lu %d\", strlen(s), strcmp(s, \"x\"));\n"
            "    if (fp != NULL) fclose(fp);"))

    def test_user_function_replaces_builtin(self):
        report = analyze_source("int abs(int x) { return x < 0 ? -x : x; }\n" + wrap("    printf(\"%d\", abs(-3));"))
        self.assertEqual(report["summary"]["errors"], 0)
        self.assertIn("replaces the built-in", " ".join(messages(report, "info")))

    def test_main_without_return_is_not_warned(self):
        r = analyze_source("int main() { printf(\"hi\"); }")
        self.assertEqual(messages(r, "warning"), [])

    def test_unknown_type_is_reported_once_and_variable_still_declared(self):
        r = analyze_source(wrap("    Widget w = 3;\n    printf(\"%d\", w);"))
        self.assertEqual(messages(r, "error"), ["unknown type name 'Widget'"])

    def test_unsupported_constructs_give_one_clear_error(self):
        r = analyze_source("union U { int a; };\nint main() { return 0; }")
        self.assertEqual(len(messages(r, "error")), 1)
        self.assertIn("'union' is not supported", messages(r, "error")[0])

    def test_second_independent_syntax_error_is_still_reported(self):
        r = analyze_source(wrap("    int a = 1\n    int b = 2\n    printf(\"%d %d\", a, b);"))
        self.assertEqual(sum(1 for d in r["diagnostics"] if d["phase"] == "syntax"), 2)

    def test_deref_assignment_counts_as_use_of_pointer(self):
        r = analyze_source(wrap("    int n = 0; int *p = &n;\n    *p = 3;"))
        self.assertNotIn("'p' is declared but never used", " ".join(messages(r)))

    def test_global_offsets_start_at_zero_despite_builtin_streams(self):
        report = analyze_source("int g;\nint main() { return g; }")
        self.assertEqual(symbols_by_name(report)["g"][0]["offset"], 0)
