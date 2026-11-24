"""Lexical analyser (scanner) for the C subset handled by this compiler front end.

Phase 1 of the compiler: turns source text into a flat list of tokens and
collects lexical errors (bad characters, unterminated strings/comments, ...).

A small preprocessor is built in: ``#define`` (object-like and function-like
macros) is expanded at token level, ``#include`` is noted, and the other
directives (``#ifdef``, ``#pragma`` ...) are ignored with an info message.
"""

import re
from dataclasses import replace

from .tokens import (
    CHAR_LIT, EOF, FLOAT_LIT, IDENT, INT_LIT, KEYWORD, KEYWORDS, ONE_CHAR_OPS,
    OP, PP, PUNCT, PUNCTUATION, STRING_LIT, THREE_CHAR_OPS, TWO_CHAR_OPS, Token,
)

SIMPLE_ESCAPES = frozenset("ntr0\\'\"abfv?")
NUMERIC_ESCAPE = re.compile(r"\\(x[0-9a-fA-F]{1,2}|[0-7]{1,3})$")
MAX_EXPANDED_TOKENS = 100_000
IGNORED_DIRECTIVES = frozenset({
    "if", "ifdef", "ifndef", "elif", "else", "endif", "pragma", "error", "warning", "line",
})


def _is_digit(ch):
    return len(ch) == 1 and "0" <= ch <= "9"


def _is_alpha(ch):
    return len(ch) == 1 and ("a" <= ch <= "z" or "A" <= ch <= "Z" or ch == "_")


def string_length(lexeme):
    """Number of characters held by a string literal lexeme (quotes included)."""
    body = lexeme[1:-1]
    i = count = 0
    while i < len(body):
        i += 2 if body[i] == "\\" else 1
        count += 1
    return count


def char_value(lexeme):
    """Numeric value of a character-literal lexeme such as ``'A'`` or ``'\\n'`` (None if unknown)."""
    body = lexeme[1:-1]
    if len(body) == 1:
        return ord(body)
    table = {"n": 10, "t": 9, "r": 13, "0": 0, "\\": 92, "'": 39, '"': 34,
             "a": 7, "b": 8, "f": 12, "v": 11, "?": 63}
    if len(body) == 2 and body[0] == "\\":
        return table.get(body[1])
    m = NUMERIC_ESCAPE.match(body)
    if m:
        digits = m.group(1)
        return int(digits[1:], 16) if digits[0] == "x" else int(digits, 8)
    return None


class Lexer:
    def __init__(self, source, sub=False):
        self.src = source
        self.sub = sub              # True when scanning the body of a #define
        self.i = 0
        self.line = 1
        self.col = 1
        self.tokens = []
        self.errors = []
        self.macros = []            # active macros at the end of the file (for the symbol table)
        self._pp_data = {}
        self._noted = set()
        self._budget = MAX_EXPANDED_TOKENS

    # ------------------------------------------------------------------ helpers
    def _peek(self, k=0):
        j = self.i + k
        return self.src[j] if j < len(self.src) else ""

    def _advance(self):
        ch = self.src[self.i]
        self.i += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def _error(self, message, line, col):
        self.errors.append({
            "severity": "error", "phase": "lexical",
            "message": message, "line": line, "col": col,
        })

    def _info(self, message, line, col):
        self.errors.append({
            "severity": "info", "phase": "lexical",
            "message": message, "line": line, "col": col,
        })

    def _emit(self, kind, value, line, col):
        self.tokens.append(Token(kind, value, line, col))

    # --------------------------------------------------------------- main loop
    def tokenize(self):
        while self.i < len(self.src):
            ch = self._peek()
            line, col = self.line, self.col
            if ch in " \t\r\n\f\v":
                self._advance()
            elif ch == "/" and self._peek(1) == "/":
                while self.i < len(self.src) and self._peek() != "\n":
                    self._advance()
            elif ch == "/" and self._peek(1) == "*":
                self._block_comment(line, col)
            elif _is_alpha(ch):
                self._word(line, col)
            elif _is_digit(ch) or (ch == "." and _is_digit(self._peek(1))):
                self._number(line, col)
            elif ch == "#" and not self.sub and self._at_line_start():
                self._preprocessor(line, col)
            elif ch == "'":
                self._char(line, col)
            elif ch == '"':
                self._string(line, col)
            else:
                self._symbol(ch, line, col)
        self._emit(EOF, "", self.line, self.col)
        if not self.sub:
            self.tokens = self._run_preprocessor(self.tokens)
        return self.tokens, self.errors

    def _at_line_start(self):
        j = self.i - 1
        while j >= 0 and self.src[j] in " \t":
            j -= 1
        return j < 0 or self.src[j] == "\n"

    # ------------------------------------------------------------ preprocessor
    def _preprocessor(self, line, col):
        """Read one directive (with backslash continuations) and handle it."""
        self._advance()                                  # the '#'
        chars = []
        while self.i < len(self.src):
            ch = self._peek()
            if ch == "\\" and self._peek(1) == "\n":
                self._advance()
                self._advance()
                chars.append(" ")
                continue
            if ch == "\\" and self._peek(1) == "\r" and self._peek(2) == "\n":
                self._advance()
                self._advance()
                self._advance()
                chars.append(" ")
                continue
            if ch == "\n":
                break
            chars.append(self._advance())
        text = "".join(chars).strip()
        m = re.match(r"([A-Za-z_]\w*)\s*(.*)$", text, re.S)
        if not m:
            if text:
                self._info(f"unrecognised preprocessor line '#{text}' ignored", line, col)
            return
        word, rest = m.group(1), m.group(2).strip()
        if word == "include":
            self._include(rest, line, col)
        elif word == "define":
            self._define(rest, line, col)
        elif word == "undef":
            name = rest.split()[0] if rest.split() else ""
            self._emit_pp(("undef", name, None, None, ""), line, col)
        elif word in IGNORED_DIRECTIVES:
            if word not in self._noted:
                self._noted.add(word)
                self._info(
                    f"'#{word}' is ignored: conditional compilation is not evaluated, "
                    f"all code is analysed", line, col)
        else:
            self._info(f"unknown preprocessor directive '#{word}' ignored", line, col)

    def _include(self, rest, line, col):
        m = re.match(r'([<"])([^>"]+)[>"]', rest)
        if not m:
            self._info(f"malformed '#include {rest}' ignored", line, col)
        elif m.group(1) == "<":
            self._info(
                f"'#include <{m.group(2)}>' noted: the standard library functions this "
                f"compiler knows are built in", line, col)
        else:
            self._info(
                f"'#include \"{m.group(2)}\"' cannot be read: names declared in it are unknown",
                line, col)

    def _define(self, rest, line, col):
        m = re.match(r"([A-Za-z_]\w*)(\(([^)]*)\))?(.*)$", rest, re.S)
        if not m:
            self._error("macro name missing in '#define'", line, col)
            return
        name, params_text, body_text = m.group(1), m.group(3), m.group(4).strip()
        params = None
        if m.group(2) is not None:
            params = [p.strip() for p in params_text.split(",")] if params_text.strip() else []
            if any(not re.fullmatch(r"[A-Za-z_]\w*", p) for p in params):
                self._error(f"unsupported parameter list in macro '{name}'", line, col)
                return
        sub = Lexer(body_text, sub=True)
        body, errors = sub.tokenize()
        for err in errors:
            self._error(f"in macro '{name}': {err['message']}", line, col)
        body = [t for t in body if t.kind != EOF]
        self._emit_pp(("define", name, params, body, body_text), line, col)

    def _emit_pp(self, data, line, col):
        self._pp_data[(line, col)] = data
        self._emit(PP, data[0], line, col)

    def _run_preprocessor(self, toks):
        """Apply #define / #undef in order and expand macro uses."""
        macros = {}
        order = []
        out, chunk = [], []
        for t in toks:
            if t.kind != PP:
                chunk.append(t)
                continue
            out += self._expand(chunk, macros, frozenset())
            chunk = []
            kind, name, params, body, raw = self._pp_data[(t.line, t.col)]
            if kind == "define":
                macros[name] = (params, body)
                order = [e for e in order if e["name"] != name]
                order.append({"name": name, "params": params, "body": raw, "line": t.line, "col": t.col})
            else:
                macros.pop(name, None)
                order = [e for e in order if e["name"] != name]
        out += self._expand(chunk, macros, frozenset())
        self.macros = order
        return out

    def _expand(self, toks, macros, hide):
        if not macros:
            return list(toks)
        out = []
        i = 0
        while i < len(toks):
            t = toks[i]
            if t.kind != IDENT or t.value not in macros or t.value in hide:
                out.append(t)
                i += 1
                continue
            params, body = macros[t.value]
            self._budget -= len(body) + 1
            if self._budget < 0:
                if "budget" not in self._noted:
                    self._noted.add("budget")
                    self._error("macro expansion is too large; stopped expanding", t.line, t.col)
                out.append(t)
                i += 1
                continue
            at = lambda toks_: [replace(b, line=t.line, col=t.col) for b in toks_]
            if params is None:
                out += self._expand(at(body), macros, hide | {t.value})
                i += 1
                continue
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if nxt is None or not (nxt.kind == PUNCT and nxt.value == "("):
                out.append(t)                      # a function-like macro name without a call
                i += 1
                continue
            args, j = self._collect_args(toks, i + 1)
            if args is None:
                self._error(f"unterminated argument list for macro '{t.value}'", t.line, t.col)
                out.append(t)
                i += 1
                continue
            if params == [] and args == [[]]:
                args = []
            if len(args) != len(params):
                self._error(
                    f"macro '{t.value}' expects {len(params)} argument(s) but {len(args)} given",
                    t.line, t.col)
                out.append(Token(INT_LIT, "0", t.line, t.col))
                i = j
                continue
            bound = {p: self._expand(a, macros, hide) for p, a in zip(params, args)}
            rep = []
            for b in body:
                if b.kind == IDENT and b.value in bound:
                    rep += at(bound[b.value])
                else:
                    rep += at([b])
            out += self._expand(rep, macros, hide | {t.value})
            i = j
        return out

    @staticmethod
    def _collect_args(toks, i):
        """toks[i] is '('. Returns (list of argument token lists, index after ')') or (None, i)."""
        depth = 0
        args = [[]]
        j = i
        while j < len(toks):
            t = toks[j]
            if t.kind == EOF:
                break
            if t.kind == PUNCT and t.value == "(":
                depth += 1
                if depth > 1:
                    args[-1].append(t)
            elif t.kind == PUNCT and t.value == ")":
                depth -= 1
                if depth == 0:
                    return args, j + 1
                args[-1].append(t)
            elif t.kind == PUNCT and t.value == "," and depth == 1:
                args.append([])
            else:
                args[-1].append(t)
            j += 1
        return None, i

    # ------------------------------------------------------------- token kinds
    def _block_comment(self, line, col):
        self._advance()
        self._advance()
        while self.i < len(self.src):
            if self._peek() == "*" and self._peek(1) == "/":
                self._advance()
                self._advance()
                return
            self._advance()
        self._error("unterminated /* comment", line, col)

    def _word(self, line, col):
        start = self.i
        while _is_alpha(self._peek()) or _is_digit(self._peek()):
            self._advance()
        text = self.src[start:self.i]
        self._emit(KEYWORD if text in KEYWORDS else IDENT, text, line, col)

    def _number(self, line, col):
        start = self.i
        is_float = False
        if self._peek() == "0" and self._peek(1) in ("x", "X") and (
                self._peek(2) != "" and self._peek(2) in "0123456789abcdefABCDEF"):
            self._advance()
            self._advance()
            while self._peek() != "" and self._peek() in "0123456789abcdefABCDEF":
                self._advance()
        else:
            while _is_digit(self._peek()):
                self._advance()
            if self._peek() == ".":
                # accepts 1.5, 1. and .5
                is_float = True
                self._advance()
                while _is_digit(self._peek()):
                    self._advance()
            if self._peek() in ("e", "E") and (
                    _is_digit(self._peek(1))
                    or (self._peek(1) in ("+", "-") and _is_digit(self._peek(2)))):
                is_float = True
                self._advance()
                if self._peek() in ("+", "-"):
                    self._advance()
                while _is_digit(self._peek()):
                    self._advance()
        # type suffixes: 10L 10u 10UL 10ull   /   1.5f 1.5L
        if is_float:
            if self._peek() != "" and self._peek() in "fFlL":
                self._advance()
        else:
            for _ in range(3):
                if self._peek() != "" and self._peek() in "uUlL":
                    self._advance()
                else:
                    break
        if _is_alpha(self._peek()):
            while _is_alpha(self._peek()) or _is_digit(self._peek()):
                self._advance()
            self._error(f"invalid numeric literal '{self.src[start:self.i]}'", line, col)
            # still emit a token so the parser does not report follow-on errors
            self._emit(FLOAT_LIT if is_float else INT_LIT, self.src[start:self.i], line, col)
            return
        self._emit(FLOAT_LIT if is_float else INT_LIT, self.src[start:self.i], line, col)

    def _read_quoted(self, quote):
        """Read up to the closing quote on the current line; None if unterminated."""
        chars = []
        while self.i < len(self.src):
            ch = self._peek()
            if ch == "\n":
                return None
            self._advance()
            if ch == quote:
                return "".join(chars)
            chars.append(ch)
            if ch == "\\" and self._peek() not in ("", "\n"):
                chars.append(self._advance())
        return None

    def _char(self, line, col):
        self._advance()
        body = self._read_quoted("'")
        if body is None:
            self._error("unterminated character literal", line, col)
            return
        if not body:
            self._error("empty character literal", line, col)
            return
        single = len(body) == 1 and body != "\\"
        escape = len(body) == 2 and body[0] == "\\" and body[1] in SIMPLE_ESCAPES
        numeric = bool(NUMERIC_ESCAPE.match(body))
        if not (single or escape or numeric):
            self._error(f"invalid character literal '{body}'", line, col)
            return
        self._emit(CHAR_LIT, f"'{body}'", line, col)

    def _string(self, line, col):
        self._advance()
        body = self._read_quoted('"')
        if body is None:
            self._error("unterminated string literal", line, col)
            return
        self._emit(STRING_LIT, f'"{body}"', line, col)

    def _symbol(self, ch, line, col):
        three = ch + self._peek(1) + self._peek(2)
        two = ch + self._peek(1)
        if three in THREE_CHAR_OPS:
            for _ in range(3):
                self._advance()
            self._emit(OP, three, line, col)
        elif two in TWO_CHAR_OPS:
            self._advance()
            self._advance()
            self._emit(OP, two, line, col)
        elif ch in ONE_CHAR_OPS:
            self._advance()
            self._emit(OP, ch, line, col)
        elif ch in PUNCTUATION:
            self._advance()
            self._emit(PUNCT, ch, line, col)
        else:
            self._advance()
            self._error(f"unexpected character '{ch}'", line, col)


def tokenize(source):
    """Convenience wrapper: returns (tokens, errors)."""
    return Lexer(source).tokenize()
