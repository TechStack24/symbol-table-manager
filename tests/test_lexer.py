import unittest

from compiler.lexer import string_length, tokenize
from compiler.tokens import EOF


def kinds_and_values(source):
    tokens, errors = tokenize(source)
    return [(t.kind, t.value) for t in tokens if t.kind != EOF], errors


class LexerTests(unittest.TestCase):
    def test_declaration_tokens(self):
        toks, errors = kinds_and_values("int x = 42;")
        self.assertEqual(errors, [])
        self.assertEqual(toks, [
            ("KEYWORD", "int"), ("IDENT", "x"), ("OP", "="), ("INT_LIT", "42"), ("PUNCT", ";"),
        ])

    def test_numbers_chars_and_strings(self):
        toks, errors = kinds_and_values("3.14 .5 'a' '\\n' \"hi\\n\"")
        self.assertEqual(errors, [])
        self.assertEqual([k for k, _ in toks], ["FLOAT_LIT", "FLOAT_LIT", "CHAR_LIT", "CHAR_LIT", "STRING_LIT"])

    def test_two_character_operators(self):
        toks, _ = kinds_and_values("a <= b && c != d; i++; x += 1;")
        values = [v for _, v in toks]
        for op in ("<=", "&&", "!=", "++", "+="):
            self.assertIn(op, values)

    def test_comments_are_skipped(self):
        toks, errors = kinds_and_values("int a; // note\n/* multi\nline */ int b;")
        self.assertEqual(errors, [])
        self.assertEqual([v for _, v in toks].count("int"), 2)

    def test_line_and_column_tracking(self):
        tokens, _ = tokenize("int a;\n  float b;")
        by_value = {t.value: t for t in tokens}
        self.assertEqual((by_value["a"].line, by_value["a"].col), (1, 5))
        self.assertEqual((by_value["float"].line, by_value["float"].col), (2, 3))

    def test_lexical_errors(self):
        _, errors = tokenize("int a = 5 @ 3;")
        self.assertEqual(len(errors), 1)
        self.assertIn("unexpected character '@'", errors[0]["message"])

        _, errors = tokenize('char *s = "oops;')
        messages = " | ".join(e["message"] for e in errors)
        self.assertIn("unterminated string literal", messages)

        _, errors = tokenize("/* never closed")
        self.assertIn("unterminated /* comment", errors[0]["message"])

        _, errors = tokenize("int 9lives;")
        self.assertIn("invalid numeric literal '9lives'", errors[0]["message"])

        _, errors = tokenize("char c = 'ab';")
        self.assertIn("invalid character literal", errors[0]["message"])

    def test_string_length_counts_escapes_once(self):
        self.assertEqual(string_length('"abc"'), 3)
        self.assertEqual(string_length('"a\\nb"'), 3)
        self.assertEqual(string_length('""'), 0)


if __name__ == "__main__":
    unittest.main()


class NumberAndPreprocessorTests(unittest.TestCase):
    def test_float_forms(self):
        for lit in ("1.5", "1.", ".5", "1e3", "2.5e-2", "1.5f"):
            tokens, errors = tokenize(lit)
            self.assertEqual(errors, [], lit)
            self.assertEqual(tokens[0].kind, "FLOAT_LIT", lit)

    def test_hex_literal(self):
        tokens, errors = tokenize("0x1F")
        self.assertEqual(errors, [])
        self.assertEqual(tokens[0].kind, "INT_LIT")

    def test_bad_literal_still_emits_token(self):
        tokens, errors = tokenize("12abc;")
        self.assertEqual(len(errors), 1)
        self.assertEqual(tokens[0].value, "12abc")

    def test_include_line_is_ignored(self):
        tokens, errors = tokenize("#include <stdio.h>\nint x;")
        self.assertEqual([e["severity"] for e in errors], ["info"])
        self.assertEqual(tokens[0].value, "int")


class ExtendedLexerTests(unittest.TestCase):
    def test_suffixes_and_new_operators(self):
        tokens, errors = tokenize("10L 5U 1.5f 3UL x<<=2 a->b c?d:e &x")
        self.assertEqual(errors, [])
        values = [t.value for t in tokens if t.kind != EOF]
        for expected in ("10L", "5U", "1.5f", "3UL", "<<=", "->", "?", ":", "&"):
            self.assertIn(expected, values)

    def test_numeric_escapes_in_char_literals(self):
        for lit in ("'\\x41'", "'\\101'", "'\\a'"):
            tokens, errors = tokenize(lit)
            self.assertEqual(errors, [], lit)
            self.assertEqual(tokens[0].kind, "CHAR_LIT")

    def test_object_like_macro_expansion(self):
        tokens, errors = tokenize("#define N 3\nint a[N];")
        self.assertEqual([t.value for t in tokens if t.kind != EOF], ["int", "a", "[", "3", "]", ";"])

    def test_function_like_macro_expansion(self):
        tokens, errors = tokenize("#define ADD(a, b) ((a) + (b))\nx = ADD(1, 2);")
        self.assertEqual(" ".join(t.value for t in tokens if t.kind != EOF), "x = ( ( 1 ) + ( 2 ) ) ;")

    def test_macro_argument_count_is_checked(self):
        tokens, errors = tokenize("#define F(a) a\nint x = F(1, 2);")
        self.assertTrue(any("expects 1 argument" in e["message"] for e in errors))

    def test_recursive_macro_does_not_loop(self):
        tokens, errors = tokenize("#define A A\nA")
        self.assertEqual(tokens[0].value, "A")
