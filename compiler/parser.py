"""Recursive-descent parser with syntax-directed semantic analysis.

The parser is single-pass: while it recognises the grammar it also drives the
symbol table (enter/exit scope, insert declarations, look names up) and checks
types. No AST is built - the symbol table *is* the output of this phase.

Supported C subset (see README for details)
-------------------------------------------
types        char short int long (long long) float double void, signed/unsigned,
             pointers (any depth), 1-D and multi-dimensional arrays,
             struct, enum, typedef, const/static/extern (accepted, ignored)
statements   blocks, if/else, while, do-while, for, switch/case/default,
             return, break, continue, declarations anywhere in a block
expressions  assignment (=, +=, ...), ?:, ||, &&, | ^ &, == !=, < > <= >=, << >>,
             + - * / %, unary ! ~ - + & * ++ --, casts, sizeof, calls,
             a[i], s.m, p->m, comma operator

Grammar (simplified)
--------------------
program      -> external* EOF
external     -> type_spec ( ';' | declarator_list )          (function or variable/typedef)
type_spec    -> [qualifiers] ( basic_words | struct_spec | enum_spec | TYPEDEF_NAME )
declarator   -> '*'* IDENT ( '[' const ']' )* [ '=' initializer ]
statement    -> block | if | while | do-while | for | switch | return | break | continue
              | declaration | expression ';' | ';'
expression   -> assignment ( ',' assignment )*
assignment   -> conditional [ ASSIGN_OP assignment ]
conditional  -> logical_or [ '?' expression ':' conditional ]
logical_or   -> logical_and ( '||' logical_and )*
logical_and  -> bit_or ( '&&' bit_or )*
bit_or       -> bit_xor ( '|' bit_xor )*          bit_xor -> bit_and ( '^' bit_and )*
bit_and      -> equality ( '&' equality )*        equality -> relational ( ('=='|'!=') relational )*
relational   -> shift ( ('<'|'>'|'<='|'>=') shift )*
shift        -> additive ( ('<<'|'>>') additive )*
additive     -> term ( ('+'|'-') term )*          term -> unary ( ('*'|'/'|'%') unary )*
unary        -> ('!'|'~'|'-'|'+'|'&'|'*'|'++'|'--') unary | sizeof ... | '(' type ')' unary | postfix
postfix      -> primary ( '[' expr ']' | '.' IDENT | '->' IDENT | '++' | '--' )*
primary      -> literal | STRING+ | '(' expression ')' | IDENT | IDENT '(' args ')'
"""

from collections import namedtuple

from .lexer import char_value, string_length
from .symbol_table import Symbol
from .tokens import (
    BASIC_TYPE_WORDS, CHAR_LIT, EOF, FLOAT_LIT, IDENT, INT_LIT, KEYWORD, OP, PUNCT,
    STRING_LIT, TYPE_KEYWORDS, TYPE_QUALIFIERS,
)
from .typeinfo import (
    arithmetic_result, canonical_type, int_value, is_enum, is_float, is_integral,
    is_numeric, is_pointer, is_struct, pointee, pointer_to,
)

ASSIGN_OPS = ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=")
STATEMENT_START = TYPE_KEYWORDS | {
    "if", "while", "for", "do", "return", "break", "continue", "switch", "case", "default", "goto",
}
UNDECLARED = ("use of undeclared identifier", "call to undeclared function")

TypeSpec = namedtuple("TypeSpec", "type is_typedef tok")


class ParseError(Exception):
    def __init__(self, message, token):
        super().__init__(message)
        self.token = token


class Operand:
    """Result of parsing an expression: its type plus what it refers to."""

    __slots__ = ("type", "symbol", "kind", "lit", "dims")

    def __init__(self, type_, symbol=None, kind="value", lit=None, dims=()):
        self.type = type_        # a C type name, or: string | error
        self.symbol = symbol
        self.kind = kind         # value | var | elem | string | array | error
        self.lit = lit           # integer/float text when the operand is a constant
        self.dims = dims         # remaining dimensions for kind == "array"

    @property
    def is_lvalue(self):
        return self.kind in ("var", "elem")


def _is_zero(text):
    value = int_value(text)
    if value is not None:
        return value == 0
    try:
        return float(text) == 0
    except (TypeError, ValueError):
        return False


def _product(values):
    total = 1
    for v in values:
        total *= v
    return total


def _fold(op, a, b):
    """Constant-fold two integer constants; None when it cannot (or should not) be folded."""
    try:
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op in ("/", "%"):
            if b == 0:
                return None
            q = abs(a) // abs(b)
            q = q if (a >= 0) == (b >= 0) else -q
            return q if op == "/" else a - q * b
        if op == "<<":
            return a << b if 0 <= b < 64 else None
        if op == ">>":
            return a >> b if 0 <= b < 64 else None
        if op == "&":
            return a & b
        if op == "|":
            return a | b
        if op == "^":
            return a ^ b
        compare = {"==": a == b, "!=": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b,
                   "&&": bool(a and b), "||": bool(a or b)}
        if op in compare:
            return int(compare[op])
    except (OverflowError, ValueError):
        pass
    return None


class Parser:
    def __init__(self, tokens, symtab):
        self.toks = tokens
        self.pos = 0
        self.st = symtab
        self.diags = []
        self._seen = set()
        self._uninit_reported = set()
        self.current_function = None
        self.saw_return = False
        self.loop_depth = 0
        self.switch_stack = []
        self.syntax_errors = 0
        self.last_error_at = None
        self._no_read_next = False

    # ===================================================================
    # Token helpers
    # ===================================================================
    def peek(self, offset=0):
        return self.toks[min(self.pos + offset, len(self.toks) - 1)]

    def prev(self):
        return self.toks[max(self.pos - 1, 0)]

    def advance(self):
        tok = self.toks[self.pos]
        if tok.kind != EOF:
            self.pos += 1
        return tok

    def at_end(self):
        return self.peek().kind == EOF

    def check_punct(self, value, offset=0):
        t = self.peek(offset)
        return t.kind == PUNCT and t.value == value

    def match_punct(self, value):
        if self.check_punct(value):
            self.advance()
            return True
        return False

    def check_op(self, value, offset=0):
        t = self.peek(offset)
        return t.kind == OP and t.value == value

    def check_kw(self, value):
        t = self.peek()
        return t.kind == KEYWORD and t.value == value

    @staticmethod
    def describe(tok):
        return "end of input" if tok.kind == EOF else f"'{tok.value}'"

    def expect_punct(self, value, context=""):
        t = self.peek()
        if t.kind == PUNCT and t.value == value:
            return self.advance()
        # a missing ; ) ] is best reported where the previous token ended
        where = self.prev() if value in (";", ")", "]") and self.pos > 0 else t
        message = f"expected '{value}'" + (f" {context}" if context else "")
        raise ParseError(f"{message}, found {self.describe(t)}", where)

    def expect_op(self, value, context=""):
        t = self.peek()
        if t.kind == OP and t.value == value:
            return self.advance()
        message = f"expected '{value}'" + (f" {context}" if context else "")
        raise ParseError(f"{message}, found {self.describe(t)}", t)

    def expect_ident(self, message):
        t = self.peek()
        if t.kind == IDENT:
            return self.advance()
        raise ParseError(f"{message}, found {self.describe(t)}", t)

    # ===================================================================
    # Diagnostics and scope helpers
    # ===================================================================
    def report(self, severity, message, line, col, phase="semantic"):
        key = (severity, message, line, col)
        if key in self._seen:
            return
        self._seen.add(key)
        self.diags.append({
            "severity": severity, "phase": phase,
            "message": message, "line": line, "col": col,
        })

    def report_syntax(self, exc):
        """Report a syntax error, unless it is the same token that was just reported
        (recovery stopped without making progress, so the repeat adds no information)."""
        self.syntax_errors += 1
        key = (exc.token.line, exc.token.col)
        if key != self.last_error_at:
            self.report("error", str(exc), exc.token.line, exc.token.col, "syntax")
        self.last_error_at = key

    def drop_cascade(self, mark, start_tok, error_tok):
        """Remove 'undeclared identifier' errors produced by tokens of a statement that then
        failed to parse (e.g. an unknown type name read as an expression)."""
        lo = (start_tok.line, start_tok.col)
        hi = (error_tok.line, error_tok.col)
        kept = self.diags[:mark]
        for d in self.diags[mark:]:
            pos = (d["line"], d["col"])
            if d["phase"] == "semantic" and d["message"].startswith(UNDECLARED) and lo <= pos <= hi:
                continue
            kept.append(d)
        self.diags = kept
        self._seen = {(d["severity"], d["message"], d["line"], d["col"]) for d in self.diags}

    def insert_symbol(self, sym):
        ok, existing = self.st.insert(sym)
        if ok and sym.overrides_builtin:
            self.report("info", f"'{sym.name}' replaces the built-in declaration of the same name",
                        sym.line, sym.col)
        return ok, existing

    def close_scope(self, check_unused=True):
        scope = self.st.exit_scope()
        if check_unused:
            for sym in scope.table.items():
                if sym.kind in ("variable", "array") and not sym.used:
                    self.report(
                        "warning", f"variable '{sym.name}' is declared but never used",
                        sym.line, sym.col,
                    )
        return scope

    @staticmethod
    def prev_decl(sym):
        return "as a built-in" if sym.builtin else f"at line {sym.line}"

    # ===================================================================
    # Error recovery
    # ===================================================================
    def synchronize(self):
        """Statement-level panic mode: skip to a safe place to resume.

        Returns True when it ended by consuming a ';' (a clean restart point)."""
        while not self.at_end():
            t = self.peek()
            if t.kind == PUNCT and t.value == ";":
                self.advance()
                return True
            if t.kind == PUNCT and t.value == "}":
                return False
            if t.kind == KEYWORD and t.value in STATEMENT_START:
                return False
            self.advance()
        return False

    def synchronize_top(self):
        """Top-level panic mode: skip to the next type keyword outside any braces/parens."""
        braces = parens = 0
        first = True
        while not self.at_end():
            t = self.peek()
            if (not first and braces == 0 and parens == 0
                    and t.kind == KEYWORD and t.value in TYPE_KEYWORDS):
                return
            first = False
            if t.kind == PUNCT:
                if t.value == "{":
                    braces += 1
                elif t.value == "}":
                    braces = max(0, braces - 1)
                elif t.value == "(":
                    parens += 1
                elif t.value == ")":
                    parens = max(0, parens - 1)
            self.advance()

    # ===================================================================
    # Types
    # ===================================================================
    def at_type_start(self, offset=0):
        t = self.peek(offset)
        if t.kind == KEYWORD:
            return t.value in TYPE_KEYWORDS
        if t.kind == IDENT:
            sym = self.st.resolve(t.value)
            return sym is not None and sym.kind == "typedef"
        return False

    def unknown_type_name_ahead(self):
        """True for `Name x`, `Name *x` where Name is not declared at all (e.g. FILE *fp)."""
        t = self.peek()
        if t.kind != IDENT or self.st.resolve(t.value) is not None:
            return False
        nxt = self.peek(1)
        if nxt.kind == IDENT:
            return True
        return (nxt.kind == OP and nxt.value == "*" and self.peek(2).kind == IDENT
                and self.peek(3).kind in (PUNCT, OP) and self.peek(3).value in (";", "=", ",", "["))

    def unknown_type_spec(self):
        """Report an unknown type name once and keep parsing the declaration as `int`."""
        t = self.advance()
        self.report("error", f"unknown type name '{t.value}'", t.line, t.col, "syntax")
        return TypeSpec("int", False, t)

    def pointer_stars(self):
        count = 0
        while True:
            t = self.peek()
            if t.kind == OP and t.value == "*":
                self.advance()
                count += 1
            elif count and t.kind == KEYWORD and t.value in TYPE_QUALIFIERS:
                self.advance()
            else:
                return count

    @staticmethod
    def with_stars(base, stars):
        return pointer_to(base, stars) if stars else base

    def type_specifier(self):
        """Parse qualifiers and one type. Returns TypeSpec(type, is_typedef, first_token)."""
        first = self.peek()
        is_typedef = False
        words = []
        result = None
        while True:
            t = self.peek()
            if t.kind == KEYWORD:
                v = t.value
                if v == "typedef":
                    is_typedef = True
                    self.advance()
                elif v in TYPE_QUALIFIERS:
                    self.advance()
                elif v in BASIC_TYPE_WORDS:
                    if result is not None:
                        raise ParseError(f"unexpected '{v}' after the type '{result}'", t)
                    words.append(v)
                    self.advance()
                elif v in ("struct", "enum", "union"):
                    if words or result is not None:
                        raise ParseError(f"unexpected '{v}' in a type", t)
                    result = self.struct_or_enum()
                else:
                    break
            elif t.kind == IDENT and not words and result is None:
                sym = self.st.resolve(t.value)
                if sym is not None and sym.kind == "typedef":
                    self.advance()
                    sym.used = True
                    sym.add_ref(t.line)
                    result = sym.type
                else:
                    break
            else:
                break
        if result is None:
            if not words:
                raise ParseError(f"expected a type, found {self.describe(self.peek())}", self.peek())
            result, problem = canonical_type(words)
            if problem:
                self.report("error", problem, first.line, first.col)
        return TypeSpec(result, is_typedef, first)

    def struct_or_enum(self):
        kw = self.advance()
        if kw.value == "union":
            raise ParseError("'union' is not supported by this C subset", kw)
        tag_tok = self.advance() if self.peek().kind == IDENT else None
        if self.check_punct("{"):
            if kw.value == "struct":
                return self.struct_body(kw, tag_tok)
            return self.enum_body(kw, tag_tok)
        if tag_tok is None:
            raise ParseError(f"expected a tag name or '{{' after '{kw.value}', "
                             f"found {self.describe(self.peek())}", self.peek())
        name = f"{kw.value} {tag_tok.value}"
        sym = self.st.resolve(name)
        if sym is not None:
            sym.used = True
            sym.add_ref(tag_tok.line)
        elif kw.value == "struct":
            # a forward reference such as `struct Node *next;` - remember the tag as incomplete
            self.insert_symbol(Symbol(name, "struct", name, tag_tok.line, tag_tok.col,
                                      defined=False, initialized=True, used=True))
        return name

    def struct_body(self, kw, tag_tok):
        if tag_tok is not None:
            name = f"struct {tag_tok.value}"
        else:
            name = f"struct <anon@{kw.line}>"
        line, col = (tag_tok or kw).line, (tag_tok or kw).col
        sym = self.st.peek_current(name)
        if sym is not None and sym.kind == "struct":
            if sym.defined:
                self.report("error", f"redefinition of '{name}' (previously defined at line {sym.line})",
                            line, col)
                sym = Symbol(name, "struct", name, line, col)          # parse into a throw-away tag
            else:
                sym.defined = True
                sym.line, sym.col = line, col
        else:
            sym = Symbol(name, "struct", name, line, col, initialized=True, used=True)
            self.insert_symbol(sym)
        sym.defined = True
        self.expect_punct("{")
        scope = self.st.enter_scope(name, "struct", kw.line)
        members = []
        try:
            while not self.check_punct("}") and not self.at_end():
                self.member_declaration(members)
            self.expect_punct("}", "to close the struct")
        finally:
            sym.members = members
            sym.size = scope.offset
            self.st.exit_scope()
        return name

    def member_declaration(self, members):
        spec = self.type_specifier()
        if spec.is_typedef:
            raise ParseError("'typedef' is not allowed inside a struct", spec.tok)
        if self.match_punct(";"):
            return
        while True:
            stars = self.pointer_stars()
            name_tok = self.expect_ident("expected a member name")
            dims = self.array_dims(name_tok)
            if self.check_op(":"):
                raise ParseError("bit-fields are not supported by this C subset", self.peek())
            mtype = self.with_stars(spec.type, stars)
            self.check_complete(mtype, name_tok, "member")
            sym = Symbol(name_tok.value, "member", mtype, name_tok.line, name_tok.col,
                         dims=dims, initialized=True, used=True)
            ok, existing = self.insert_symbol(sym)
            if ok:
                members.append((sym.name, mtype, list(dims)))
            else:
                self.report("error",
                            f"duplicate member '{sym.name}' (previously declared at line {existing.line})",
                            name_tok.line, name_tok.col)
            if not self.match_punct(","):
                break
        self.expect_punct(";", "after the member declaration")

    def enum_body(self, kw, tag_tok):
        name = f"enum {tag_tok.value}" if tag_tok is not None else f"enum <anon@{kw.line}>"
        line, col = (tag_tok or kw).line, (tag_tok or kw).col
        if tag_tok is not None:
            tag = Symbol(name, "enum", name, line, col, initialized=True, used=True)
            ok, existing = self.insert_symbol(tag)
            if not ok:
                self.report("error", f"redefinition of '{name}' (previously declared at line "
                                     f"{existing.line})", line, col)
        self.expect_punct("{")
        value = 0
        while not self.check_punct("}"):
            ident = self.expect_ident("expected an enumerator name")
            if self.check_op("="):
                eq = self.advance()
                expr = self.conditional()
                number = int_value(expr.lit) if expr.type != "error" else None
                if number is None:
                    if expr.type != "error":
                        self.report("error", "enumerator value must be an integer constant",
                                    eq.line, eq.col)
                else:
                    value = number
            const = Symbol(ident.value, "constant", "int", ident.line, ident.col,
                           value=value, initialized=True, used=True)
            ok, existing = self.insert_symbol(const)
            if not ok:
                self.report("error", f"redeclaration of '{ident.value}' "
                                     f"(previously declared {self.prev_decl(existing)})",
                            ident.line, ident.col)
            value += 1
            if not self.match_punct(","):
                break
        self.expect_punct("}", "to close the enum")
        return name

    def check_complete(self, type_, tok, what="variable"):
        """A struct used by value must already be defined."""
        if is_struct(type_):
            tag = self.st.resolve(type_)
            if tag is None or not tag.defined:
                self.report("error", f"{what} '{tok.value}' has incomplete type '{type_}'",
                            tok.line, tok.col)

    def array_dims(self, name_tok, allow_empty=False):
        """Parse zero or more `[const]` suffixes. A 0 entry means 'size unknown (VLA)'; None means `[]`."""
        dims = []
        name = name_tok.value
        while self.match_punct("["):
            if self.check_punct("]"):
                if not (allow_empty and not dims):
                    raise ParseError(f"array '{name}' needs a size", self.peek())
                self.advance()
                dims.append(None)
                continue
            size_tok = self.peek()
            value = self.conditional()
            if value.type == "error":
                n = 1
            else:
                n = int_value(value.lit)
                if n is None:
                    if not is_integral(value.type):
                        raise ParseError("array size must be an integer constant", size_tok)
                    self.report("info", f"'{name}' is a variable-length array: its size is only "
                                        f"known at run time", size_tok.line, size_tok.col)
                    n = 0
                elif n <= 0:
                    self.report("error", f"array '{name}' must have a positive size",
                                size_tok.line, size_tok.col)
                    n = 1
            dims.append(n)
            self.expect_punct("]", "after the array size")
        return dims

    # ===================================================================
    # Program and declarations
    # ===================================================================
    def parse(self):
        while not self.at_end():
            start = self.pos
            mark = len(self.diags)
            t = self.peek()
            try:
                if t.kind == PUNCT and t.value == ";":
                    self.advance()
                elif self.at_type_start():
                    self.declaration(top_level=True)
                elif self.unknown_type_name_ahead():
                    self.declaration(top_level=True, spec=self.unknown_type_spec())
                else:
                    raise ParseError(
                        f"expected a declaration at top level, found {self.describe(t)}", t)
            except ParseError as exc:
                self.drop_cascade(mark, t, exc.token)
                self.report_syntax(exc)
                self.synchronize_top()
            if self.pos == start:
                self.advance()

        if len(self.toks) > 1:
            main = self.st.lookup_global("main")
            if main is None or main.kind != "function" or not main.defined:
                self.report("info", "no 'main' function is defined", 1, 1)
        self.st.finish()
        return self.diags

    def declaration(self, top_level, spec=None):
        spec = spec or self.type_specifier()
        if self.match_punct(";"):           # `struct P { ... };`  `enum { A, B };`  `struct P;`
            return
        if spec.is_typedef:
            self.typedef_declarators(spec)
            return
        stars = self.pointer_stars()
        name_tok = self.expect_ident(f"expected a name after '{self.with_stars(spec.type, stars)}'")
        if self.check_punct("("):
            if not top_level:
                raise ParseError("nested function definitions are not allowed", name_tok)
            self.function_rest(self.with_stars(spec.type, stars), name_tok)
            return
        self.var_declarators(spec, stars, name_tok)
        self.expect_punct(";", "after the declaration")

    def typedef_declarators(self, spec):
        while True:
            stars = self.pointer_stars()
            if self.check_punct("("):
                raise ParseError("function-pointer typedefs are not supported by this C subset",
                                 self.peek())
            name_tok = self.expect_ident("expected a name in the typedef")
            if self.check_punct("["):
                raise ParseError("array typedefs are not supported by this C subset", self.peek())
            sym = Symbol(name_tok.value, "typedef", self.with_stars(spec.type, stars),
                         name_tok.line, name_tok.col, initialized=True, used=True)
            ok, existing = self.insert_symbol(sym)
            if not ok:
                self.report("error", f"redeclaration of '{sym.name}' "
                                     f"(previously declared {self.prev_decl(existing)})",
                            name_tok.line, name_tok.col)
            if not self.match_punct(","):
                break
        self.expect_punct(";", "after the typedef")

    def var_declarators(self, spec, stars, name_tok):
        while True:
            self.one_declarator(spec, stars, name_tok)
            if not self.match_punct(","):
                break
            stars = self.pointer_stars()
            name_tok = self.expect_ident("expected a name after ','")

    def one_declarator(self, spec, stars, name_tok):
        name = name_tok.value
        base = spec.type
        vtype = self.with_stars(base, stars)
        dims = self.array_dims(name_tok, allow_empty=True)
        infer = bool(dims) and dims[0] is None
        if infer:
            dims[0] = 1
        if vtype == "void":
            self.report("error", f"variable '{name}' cannot be declared with type 'void'",
                        name_tok.line, name_tok.col)
        self.check_complete(vtype, name_tok)

        sym = Symbol(
            name, "array" if dims else "variable", vtype,
            name_tok.line, name_tok.col, dims=list(dims),
            initialized=bool(dims) or self.st.current.level == 0,
        )
        shadowed = self.st.find_outer(name)
        ok, existing = self.insert_symbol(sym)
        if not ok:
            self.report("error",
                        f"redeclaration of '{name}' (previously declared {self.prev_decl(existing)})",
                        name_tok.line, name_tok.col)
        elif shadowed is not None and not shadowed.builtin and shadowed.kind != "member":
            self.report("info", f"'{name}' shadows the declaration at line {shadowed.line}",
                        name_tok.line, name_tok.col)

        eq = self.peek()
        if eq.kind == OP and eq.value == "=":
            self.advance()
            if dims:
                count = self.array_initializer(sym, vtype, infer)
                if infer and ok:
                    self.st.resize(sym, [count] + dims[1:])
            elif is_struct(vtype) and self.check_punct("{"):
                self.brace_initializer()
                sym.initialized = True
            else:
                value = self.assignment()
                self.check_assign(vtype, value, eq, f"initialization of '{name}'")
                sym.initialized = True
        elif infer:
            self.report("error", f"array '{name}' needs a size or an initializer",
                        name_tok.line, name_tok.col)

    def brace_initializer(self):
        """`{...}` initializer whose elements are not type-checked (structs, nested lists)."""
        self.expect_punct("{")
        if not self.check_punct("}"):
            while True:
                if self.check_punct("{"):
                    self.brace_initializer()
                else:
                    if self.check_op("."):                       # designator  .member = value
                        self.advance()
                        self.expect_ident("expected a member name after '.'")
                        self.expect_op("=", "after the designator")
                    elif self.check_punct("["):                  # designator  [index] = value
                        self.advance()
                        self.conditional()
                        self.expect_punct("]", "after the designator")
                        self.expect_op("=", "after the designator")
                    self.assignment()
                if not self.match_punct(","):
                    break
                if self.check_punct("}"):
                    break
        self.expect_punct("}", "to close the initializer list")

    def array_initializer(self, sym, elem_type, infer):
        """Parse the initializer of an array. Returns the number of top-level elements."""
        if self.check_punct("{"):
            first = self.peek()
            count, nested = self.init_list(sym, elem_type)
            limit = sym.dims[0] if (nested or len(sym.dims) == 1) else _product(sym.dims)
            if not infer and limit and count > limit:
                self.report(
                    "error",
                    f"too many initializers for '{sym.name}' (size {limit}, given {count})",
                    first.line, first.col)
            return count
        if self.peek().kind == STRING_LIT and elem_type in ("char", "unsigned char"):
            tok = self.advance()
            needed = string_length(tok.value) + 1
            if not infer and sym.dims[0] and needed > sym.dims[0]:
                self.report(
                    "error",
                    f"string needs {needed} bytes but '{sym.name}' only holds {sym.dims[0]}",
                    tok.line, tok.col)
            return needed
        raise ParseError("an array must be initialized with a '{...}' list", self.peek())

    def init_list(self, sym, elem_type):
        first = self.expect_punct("{")
        count = 0
        nested = False
        if not self.check_punct("}"):
            while True:
                if self.check_punct("{"):
                    nested = True
                    self.brace_initializer()
                else:
                    if self.check_punct("["):                    # designator  [index] = value
                        self.advance()
                        self.conditional()
                        self.expect_punct("]", "after the designator")
                        self.expect_op("=", "after the designator")
                    value = self.assignment()
                    string_row = value.type == "string" and elem_type == "char" and len(sym.dims) > 1
                    if not is_struct(elem_type) and not string_row:
                        self.check_assign(elem_type, value, first, f"initializer of '{sym.name}'")
                count += 1
                if not self.match_punct(","):
                    break
                if self.check_punct("}"):
                    break
        self.expect_punct("}", "to close the initializer list")
        return count, nested

    # ------------------------------------------------------------- functions
    def parse_parameters(self):
        """Parse `( ... )`. Returns (params [(type, name token|None)], variadic)."""
        self.expect_punct("(")
        params = []
        variadic = False
        if self.check_kw("void") and self.check_punct(")", 1):
            self.advance()
        elif not self.check_punct(")"):
            while True:
                if self.check_op(".") and self.check_op(".", 1) and self.check_op(".", 2):
                    for _ in range(3):
                        self.advance()
                    variadic = True
                    break
                t = self.peek()
                if not self.at_type_start():
                    raise ParseError(f"expected a parameter type, found {self.describe(t)}", t)
                spec = self.type_specifier()
                stars = self.pointer_stars()
                pname = self.advance() if self.peek().kind == IDENT else None
                ptype = self.with_stars(spec.type, stars)
                if ptype == "void":
                    self.report("error", "a parameter cannot have type 'void'", t.line, t.col)
                while self.match_punct("["):                      # array parameters decay to pointers
                    if not self.check_punct("]"):
                        self.conditional()
                    self.expect_punct("]", "after the array size")
                    ptype = pointer_to(ptype)
                params.append((ptype, pname))
                if not self.match_punct(","):
                    break
        self.expect_punct(")", "after the parameter list")
        return params, variadic

    def function_rest(self, ret_type, name_tok):
        name = name_tok.value
        params, variadic = self.parse_parameters()

        fsym = Symbol(
            name, "function", ret_type, name_tok.line, name_tok.col,
            params=[(t, n.value if n else "") for t, n in params],
            defined=False, initialized=True, variadic=variadic,
        )
        existing = self.st.peek_current(name)
        if existing is not None and existing.builtin:
            existing = None                      # a user declaration replaces the built-in

        if self.match_punct(";"):                        # prototype only
            if existing is None:
                self.insert_symbol(fsym)
            else:
                self.check_function_redeclaration(existing, fsym, name_tok)
            return

        if not self.check_punct("{"):
            raise ParseError(
                f"expected ';' or a function body, found {self.describe(self.peek())}", self.peek())

        fsym.defined = True
        if existing is None:
            self.insert_symbol(fsym)
        elif self.check_function_redeclaration(existing, fsym, name_tok):
            if existing.defined:
                self.report("error", f"redefinition of function '{name}'",
                            name_tok.line, name_tok.col)
            else:
                existing.defined = True
                existing.params = fsym.params
                fsym = existing

        self.current_function = fsym
        self.saw_return = False
        self.loop_depth = 0
        self.switch_stack = []
        errors_before = self.syntax_errors
        self.st.enter_scope(name, "function", name_tok.line)
        ok = False
        try:
            for ptype, ptok in params:
                if ptok is None:
                    self.report("error", "parameter name omitted in function definition",
                                name_tok.line, name_tok.col)
                    continue
                psym = Symbol(ptok.value, "parameter", ptype, ptok.line, ptok.col,
                              initialized=True)
                added, prev = self.insert_symbol(psym)
                if not added:
                    self.report("error", f"redeclaration of parameter '{ptok.value}'",
                                ptok.line, ptok.col)
            self.block(new_scope=False)
            clean = self.syntax_errors == errors_before
            if ret_type != "void" and not self.saw_return and name != "main" and clean:
                self.report("warning", f"non-void function '{name}' has no return statement",
                            name_tok.line, name_tok.col)
            ok = clean
        finally:
            self.close_scope(check_unused=ok)
            self.current_function = None

    def check_function_redeclaration(self, existing, new, name_tok):
        """Returns True when `new` is a compatible redeclaration of `existing`."""
        name = new.name
        if existing.kind != "function":
            self.report(
                "error",
                f"'{name}' redeclared as a function (previously declared {self.prev_decl(existing)})",
                name_tok.line, name_tok.col)
            return False
        same = (existing.type == new.type
                and [p[0] for p in existing.params] == [p[0] for p in new.params])
        if not same:
            self.report(
                "error",
                f"conflicting types for '{name}' (previous declaration: {existing.signature()})",
                name_tok.line, name_tok.col)
            return False
        return True

    # ===================================================================
    # Statements
    # ===================================================================
    def block(self, scope_name=None, new_scope=True):
        lbrace = self.expect_punct("{")
        if new_scope:
            self.st.enter_scope(scope_name or f"block@{lbrace.line}", "block", lbrace.line)
        errors_before = self.syntax_errors
        ok = False
        try:
            while not self.check_punct("}") and not self.at_end():
                start = self.pos
                mark = len(self.diags)
                first = self.peek()
                try:
                    self.statement()
                except ParseError as exc:
                    self.drop_cascade(mark, first, exc.token)
                    self.report_syntax(exc)
                    self.synchronize()
                if self.pos == start:
                    self.advance()
            self.expect_punct("}", "to close the block")
            ok = self.syntax_errors == errors_before
        finally:
            if new_scope:
                self.close_scope(check_unused=ok)

    def statement(self):
        t = self.peek()
        if t.kind == PUNCT:
            if t.value == "{":
                return self.block()
            if t.value == ";":
                self.advance()
                return None
        if t.kind == KEYWORD:
            if t.value in TYPE_KEYWORDS:
                return self.declaration(top_level=False)
            handlers = {
                "if": self.if_stmt, "while": self.while_stmt, "do": self.do_stmt,
                "for": self.for_stmt, "return": self.return_stmt, "switch": self.switch_stmt,
                "case": self.case_label, "default": self.default_label,
                "break": self.jump_stmt, "continue": self.jump_stmt,
            }
            handler = handlers.get(t.value)
            if t.value == "goto":
                self.advance()
                raise ParseError("'goto' and labels are not supported by this C subset", t)
            if handler is not None:
                return handler()
            if t.value != "sizeof":
                raise ParseError(f"unexpected keyword '{t.value}'", t)
        elif t.kind == IDENT:
            if self.at_type_start():
                return self.declaration(top_level=False)
            if self.check_op(":", 1):
                self.advance()
                self.advance()
                raise ParseError("'goto' and labels are not supported by this C subset", t)
            if self.unknown_type_name_ahead():
                return self.declaration(top_level=False, spec=self.unknown_type_spec())
        self.expression()
        self.expect_punct(";", "after the expression")
        return None

    def body_statement(self):
        """Body of if/while/for: a block or a single non-declaration statement."""
        t = self.peek()
        if self.at_type_start():
            raise ParseError(
                "a declaration cannot be the body of a control statement; wrap it in braces", t)
        self.statement()

    def loop_body(self):
        self.loop_depth += 1
        try:
            self.body_statement()
        finally:
            self.loop_depth -= 1

    def if_stmt(self):
        kw = self.advance()
        self.expect_punct("(", "after 'if'")
        cond = self.expression()
        self.check_condition(cond, kw)
        self.expect_punct(")", "after the condition")
        self.body_statement()
        if self.check_kw("else"):
            self.advance()
            self.body_statement()

    def while_stmt(self):
        kw = self.advance()
        self.expect_punct("(", "after 'while'")
        cond = self.expression()
        self.check_condition(cond, kw)
        self.expect_punct(")", "after the condition")
        self.loop_body()

    def do_stmt(self):
        self.advance()
        self.loop_body()
        if not self.check_kw("while"):
            raise ParseError(
                f"expected 'while' after the do-body, found {self.describe(self.peek())}",
                self.peek())
        kw = self.advance()
        self.expect_punct("(", "after 'while'")
        cond = self.expression()
        self.check_condition(cond, kw)
        self.expect_punct(")", "after the condition")
        self.expect_punct(";", "after do-while")

    def for_stmt(self):
        kw = self.advance()
        self.expect_punct("(", "after 'for'")
        self.st.enter_scope(f"for@{kw.line}", "block", kw.line)
        errors_before = self.syntax_errors
        ok = False
        try:
            if self.check_punct(";"):
                self.advance()
            elif self.at_type_start():
                self.declaration(top_level=False)
            else:
                self.expression()
                self.expect_punct(";", "after the for-initializer")
            if not self.check_punct(";"):
                cond = self.expression()
                self.check_condition(cond, kw)
            self.expect_punct(";", "after the for-condition")
            if not self.check_punct(")"):
                self.expression()
            self.expect_punct(")", "after the for-clauses")
            self.loop_body()
            ok = self.syntax_errors == errors_before
        finally:
            self.close_scope(check_unused=ok)

    def switch_stmt(self):
        kw = self.advance()
        self.expect_punct("(", "after 'switch'")
        cond = self.expression()
        if cond.type != "error" and not is_integral(cond.type):
            self.report("error", f"switch expression must be an integer, found {cond.type}",
                        kw.line, kw.col)
        self.expect_punct(")", "after the switch expression")
        if not self.check_punct("{"):
            raise ParseError(f"expected '{{' to start the switch body, found "
                             f"{self.describe(self.peek())}", self.peek())
        self.switch_stack.append({"cases": set(), "default": False})
        try:
            self.block(scope_name=f"switch@{kw.line}")
        finally:
            self.switch_stack.pop()

    def case_label(self):
        kw = self.advance()
        if not self.switch_stack:
            raise ParseError("'case' label not within a switch statement", kw)
        value = self.conditional()
        if value.type != "error":
            if not is_integral(value.type):
                self.report("error", "case label must be an integer constant", kw.line, kw.col)
            else:
                number = int_value(value.lit)
                if number is None:
                    self.report("error", "case label must be a constant expression",
                                kw.line, kw.col)
                elif number in self.switch_stack[-1]["cases"]:
                    self.report("error", f"duplicate case value {number}", kw.line, kw.col)
                else:
                    self.switch_stack[-1]["cases"].add(number)
        self.expect_op(":", "after the case value")

    def default_label(self):
        kw = self.advance()
        if not self.switch_stack:
            raise ParseError("'default' label not within a switch statement", kw)
        if self.switch_stack[-1]["default"]:
            self.report("error", "duplicate 'default' label in switch", kw.line, kw.col)
        self.switch_stack[-1]["default"] = True
        self.expect_op(":", "after 'default'")

    def return_stmt(self):
        kw = self.advance()
        fn = self.current_function
        self.saw_return = True
        if self.check_punct(";"):
            if fn is not None and fn.type != "void":
                self.report("warning",
                            f"'return' without a value in non-void function '{fn.name}'",
                            kw.line, kw.col)
        else:
            value = self.expression()
            if fn is not None:
                if fn.type == "void":
                    self.report("error",
                                f"void function '{fn.name}' should not return a value",
                                kw.line, kw.col)
                else:
                    self.check_assign(fn.type, value, kw, "return statement")
        self.expect_punct(";", "after 'return'")

    def jump_stmt(self):
        kw = self.advance()
        if kw.value == "break":
            if self.loop_depth == 0 and not self.switch_stack:
                self.report("error", "'break' statement is not inside a loop or switch",
                            kw.line, kw.col)
        elif self.loop_depth == 0:
            self.report("error", "'continue' statement is not inside a loop", kw.line, kw.col)
        self.expect_punct(";", f"after '{kw.value}'")

    # ===================================================================
    # Type checks
    # ===================================================================
    @staticmethod
    def as_type(operand):
        """The type an operand has when used as a value (string literals are `char *`)."""
        return "char *" if operand.type == "string" else operand.type

    def check_condition(self, operand, tok):
        if operand.type == "error":
            return
        t = self.as_type(operand)
        if not (is_numeric(t) or is_pointer(t)):
            self.report("error",
                        f"condition must be a numeric expression, found {operand.type}",
                        tok.line, tok.col)

    def check_assign(self, target, src, tok, context="assignment"):
        if "error" in (target, src.type):
            return
        if src.type == "void":
            self.report("error", f"void value used in {context}", tok.line, tok.col)
            return
        if target == "void":
            return
        if is_pointer(target):
            self.check_pointer_assign(target, src, tok, context)
            return
        if src.kind == "array":
            name = src.symbol.name if src.symbol is not None else "array"
            self.report("error", f"array '{name}' is used without a subscript", tok.line, tok.col)
            return
        if src.type == "string":
            self.report("error",
                        f"a string literal cannot be used in {context} (expected {target})",
                        tok.line, tok.col)
            return
        if is_struct(target) or is_struct(src.type):
            if target != src.type:
                self.report("error",
                            f"incompatible types in {context}: cannot convert {src.type} to {target}",
                            tok.line, tok.col)
            return
        if is_pointer(src.type):
            self.report("warning", f"{context} makes {target} from a pointer without a cast",
                        tok.line, tok.col)
            return
        if is_float(src.type) and is_integral(target):
            self.report("warning",
                        f"implicit conversion from {src.type} to {target} in {context} "
                        f"may lose precision", tok.line, tok.col)

    def check_pointer_assign(self, target, src, tok, context):
        st = src.type
        if st == "string":
            if pointee(target) not in ("char", "unsigned char", "void"):
                self.report("warning", f"incompatible pointer types in {context} "
                                       f"(string literal to {target})", tok.line, tok.col)
        elif is_pointer(st):
            a, b = pointee(target), pointee(st)
            if "void" not in (a, b) and a != b:
                self.report("warning",
                            f"incompatible pointer types in {context} ({st} to {target})",
                            tok.line, tok.col)
        elif is_integral(st):
            if src.lit is None or int_value(src.lit) != 0:
                self.report("warning", f"{context} makes a pointer from an integer without a cast",
                            tok.line, tok.col)
        else:
            self.report("error",
                        f"incompatible types in {context}: cannot convert {st} to {target}",
                        tok.line, tok.col)

    # ===================================================================
    # Expressions
    # ===================================================================
    def expression(self):
        result = self.assignment()
        while self.check_punct(","):
            self.advance()
            result = self.assignment()
        return result

    def assignment(self):
        left = self.conditional()
        t = self.peek()
        if t.kind == OP and t.value in ASSIGN_OPS:
            self.advance()
            right = self.assignment()
            return self.finish_assignment(left, t, right)
        return left

    def finish_assignment(self, left, op, right):
        if left.type == "error":
            return Operand("error")
        if not left.is_lvalue:
            self.report("error", f"left side of '{op.value}' is not assignable", op.line, op.col)
            return Operand("error")
        if left.kind == "var" and left.symbol is not None:
            left.symbol.initialized = True
        if right.type == "error":
            return Operand(left.type)
        if op.value == "=":
            self.check_assign(left.type, right, op, "assignment")
            return Operand(left.type)
        base_op = op.value[:-1]
        rt = self.as_type(right)
        if is_pointer(left.type):
            if base_op in ("+", "-") and is_integral(rt):
                return Operand(left.type)
            self.report("error", f"invalid operands to '{op.value}'", op.line, op.col)
            return Operand("error")
        if not is_numeric(left.type) or not is_numeric(rt):
            self.report("error", f"invalid operands to '{op.value}'", op.line, op.col)
            return Operand("error")
        if base_op in ("%", "<<", ">>", "&", "|", "^") and (is_float(left.type) or is_float(rt)):
            self.report("error", f"operator '{base_op}' requires integer operands",
                        op.line, op.col)
            return Operand("error")
        if base_op in ("/", "%") and right.lit is not None and _is_zero(right.lit):
            self.report("warning", "division by zero", op.line, op.col)
        self.check_assign(left.type, Operand(arithmetic_result(left.type, rt)), op,
                          "compound assignment")
        return Operand(left.type)

    def conditional(self):
        cond = self.logical_or()
        if not self.check_op("?"):
            return cond
        q = self.advance()
        self.check_condition(cond, q)
        a = self.expression()
        self.expect_op(":", "in the conditional expression")
        b = self.conditional()
        return self.merge_branches(a, b, q)

    def merge_branches(self, a, b, tok):
        if "error" in (a.type, b.type):
            return Operand("error")
        at, bt = self.as_type(a), self.as_type(b)
        if is_numeric(at) and is_numeric(bt):
            return Operand(arithmetic_result(at, bt))
        if is_pointer(at) and (is_pointer(bt) or is_integral(bt)):
            return Operand(at)
        if is_pointer(bt) and is_integral(at):
            return Operand(bt)
        if at == bt:
            return Operand(at)
        self.report("error", f"operands of '?:' have incompatible types ({at} and {bt})",
                    tok.line, tok.col)
        return Operand("error")

    def _binary(self, sub, ops, category):
        left = sub()
        while self.peek().kind == OP and self.peek().value in ops:
            op = self.advance()
            right = sub()
            left = self.binary_result(op, left, right, category)
        return left

    def logical_or(self):
        return self._binary(self.logical_and, ("||",), "logic")

    def logical_and(self):
        return self._binary(self.bit_or, ("&&",), "logic")

    def bit_or(self):
        return self._binary(self.bit_xor, ("|",), "bit")

    def bit_xor(self):
        return self._binary(self.bit_and, ("^",), "bit")

    def bit_and(self):
        return self._binary(self.equality, ("&",), "bit")

    def equality(self):
        return self._binary(self.relational, ("==", "!="), "compare")

    def relational(self):
        return self._binary(self.shift, ("<", ">", "<=", ">="), "compare")

    def shift(self):
        return self._binary(self.additive, ("<<", ">>"), "bit")

    def additive(self):
        return self._binary(self.term, ("+", "-"), "arith")

    def term(self):
        return self._binary(self.unary, ("*", "/", "%"), "arith")

    def describe_operand(self, operand):
        t = self.as_type(operand)
        if operand.type == "string":
            return "a string literal"
        if t == "void":
            return "a void value"
        if is_struct(t):
            return f"a {t} value"
        return "a pointer"

    def binary_result(self, op, left, right, category):
        if "error" in (left.type, right.type):
            return Operand("error")
        lt, rt = self.as_type(left), self.as_type(right)
        for side, t in ((left, lt), (right, rt)):
            if not (is_numeric(t) or is_pointer(t)):
                self.report("error",
                            f"invalid operand to '{op.value}': {self.describe_operand(side)}",
                            op.line, op.col)
                return Operand("error")
        lptr, rptr = is_pointer(lt), is_pointer(rt)
        folded = None
        if left.lit is not None and right.lit is not None:
            a, b = int_value(left.lit), int_value(right.lit)
            if a is not None and b is not None:
                folded = _fold(op.value, a, b)

        if category == "logic":
            return Operand("int", lit=None if folded is None else str(folded))
        if category == "compare":
            return Operand("int", lit=None if folded is None else str(folded))

        if lptr or rptr:                                    # pointer arithmetic
            if category == "arith" and op.value == "+" and (lptr != rptr):
                return Operand(lt if lptr else rt)
            if category == "arith" and op.value == "-":
                if lptr and rptr:
                    return Operand("long")
                if lptr and is_integral(rt):
                    return Operand(lt)
            self.report("error", f"invalid operands to '{op.value}': a pointer is not allowed here",
                        op.line, op.col)
            return Operand("error")

        if op.value in ("%", "<<", ">>", "&", "|", "^") and (is_float(lt) or is_float(rt)):
            self.report("error", f"operator '{op.value}' requires integer operands",
                        op.line, op.col)
            return Operand("error")
        if op.value in ("/", "%") and right.lit is not None and _is_zero(right.lit):
            self.report("warning", "division by zero", op.line, op.col)
        return Operand(arithmetic_result(lt, rt), lit=None if folded is None else str(folded))

    # ------------------------------------------------------------- unary
    def unary(self):
        t = self.peek()
        if t.kind == OP and t.value in ("!", "-", "+", "~"):
            self.advance()
            operand = self.unary()
            return self.unary_arith(t, operand)
        if t.kind == OP and t.value == "&":
            self.advance()
            return self.address_of(t)
        if t.kind == OP and t.value == "*":
            self.advance()
            return self.dereference(t, self.unary())
        if t.kind == OP and t.value in ("++", "--"):
            self.advance()
            operand = self.unary()
            return self.check_increment(t, operand)
        if t.kind == KEYWORD and t.value == "sizeof":
            return self.sizeof_expr()
        if t.kind == PUNCT and t.value == "(" and self.at_type_start(1):
            return self.cast_expr()
        return self.postfix()

    def unary_arith(self, t, operand):
        if operand.type == "error":
            return Operand("error")
        ot = self.as_type(operand)
        if t.value == "!":
            if not (is_numeric(ot) or is_pointer(ot)):
                self.report("error", f"invalid operand to unary '!'", t.line, t.col)
                return Operand("error")
            v = int_value(operand.lit)
            return Operand("int", lit=None if v is None else str(int(v == 0)))
        if not is_numeric(ot) or (t.value == "~" and not is_integral(ot)):
            self.report("error", f"invalid operand to unary '{t.value}'", t.line, t.col)
            return Operand("error")
        lit = operand.lit
        v = int_value(lit)
        if v is not None:
            lit = str(-v) if t.value == "-" else str(~v) if t.value == "~" else str(v)
        elif lit is not None and t.value == "-":
            lit = lit[1:] if lit.startswith("-") else "-" + lit
        result = "int" if (is_integral(ot) and base_size_small(ot)) else ot
        return Operand(result, lit=lit)

    def address_of(self, op):
        nxt, after = self.peek(), self.peek(1)
        if nxt.kind == IDENT and not (after.kind == PUNCT and after.value in ("(", "[")) \
                and not (after.kind == OP and after.value in (".", "->")):
            self._no_read_next = True          # &x : the address is taken, x is not read
        operand = self.unary()
        self._no_read_next = False
        if operand.type == "error":
            return Operand("error")
        if operand.kind == "array":
            return Operand(operand.type)
        if not operand.is_lvalue:
            self.report("error", "cannot take the address of an rvalue", op.line, op.col)
            return Operand("error")
        return Operand(pointer_to(operand.type))

    def dereference(self, op, operand):
        if operand.type == "error":
            return Operand("error")
        t = self.as_type(operand)
        if not is_pointer(t):
            self.report("error", f"indirection requires a pointer operand, found {t}",
                        op.line, op.col)
            return Operand("error")
        inner = pointee(t)
        if inner == "void":
            self.report("error", "cannot dereference a 'void *' pointer", op.line, op.col)
            return Operand("error")
        return Operand(inner, None, "elem")

    def sizeof_expr(self):
        self.advance()
        if self.check_punct("(") and self.at_type_start(1):
            self.advance()
            spec = self.type_specifier()
            stars = self.pointer_stars()
            self.expect_punct(")", "after the sizeof type")
            size = self.st.sizeof(self.with_stars(spec.type, stars))
            return Operand("unsigned long", lit=str(size) if size else None)
        self.unary()
        return Operand("unsigned long")

    def cast_expr(self):
        self.advance()                                  # '('
        spec = self.type_specifier()
        stars = self.pointer_stars()
        self.expect_punct(")", "after the cast type")
        target = self.with_stars(spec.type, stars)
        operand = self.unary()
        if operand.type == "error":
            return Operand(target)
        v = int_value(operand.lit)
        lit = operand.lit if (v is not None and is_integral(target)) else None
        return Operand(target, lit=lit)

    # ------------------------------------------------------------ postfix
    def postfix(self):
        operand = self.primary()
        while True:
            t = self.peek()
            if t.kind == PUNCT and t.value == "[":
                operand = self.subscript(operand)
            elif t.kind == OP and t.value in (".", "->"):
                operand = self.member_access(operand)
            elif t.kind == OP and t.value in ("++", "--"):
                self.advance()
                operand = self.check_increment(t, operand)
            else:
                return operand

    def check_increment(self, op, operand):
        if operand.type == "error":
            return Operand("error")
        if not operand.is_lvalue or not (is_numeric(operand.type) or is_pointer(operand.type)):
            self.report("error",
                        f"operand of '{op.value}' must be a modifiable numeric variable",
                        op.line, op.col)
            return Operand("error")
        if operand.kind == "var" and operand.symbol is not None:
            operand.symbol.initialized = True
        return Operand(operand.type)

    def subscript(self, operand):
        lb = self.advance()
        index = self.expression()
        self.expect_punct("]", "after the array index")
        if operand.type == "error":
            return Operand("error")
        sym = operand.symbol
        name = sym.name if sym is not None else "expression"
        if operand.kind == "array":
            self.check_index_value(index, name, operand.dims[0] if operand.dims else 0, lb)
            rest = operand.dims[1:]
            if rest:
                return Operand(operand.type, sym, "array", dims=rest)
            return Operand(pointee(operand.type), sym, "elem")
        t = self.as_type(operand)
        if is_pointer(t):
            self.check_index_value(index, name, 0, lb)
            return Operand(pointee(t), None, "elem")
        self.report("error", f"'{name}' is not an array and cannot be subscripted",
                    lb.line, lb.col)
        return Operand("error")

    def check_index_value(self, index, name, bound, tok):
        if index.type == "error":
            return
        if not is_integral(index.type):
            self.report("error", f"array index must be an integer, found {index.type}",
                        tok.line, tok.col)
            return
        n = int_value(index.lit)
        if n is not None and bound and (n < 0 or n >= bound):
            self.report("warning",
                        f"index {n} is out of bounds for array '{name}' of size {bound}",
                        tok.line, tok.col)

    def member_access(self, operand):
        op = self.advance()
        name_tok = self.expect_ident(f"expected a member name after '{op.value}'")
        if operand.type == "error":
            return Operand("error")
        t = self.as_type(operand)
        if op.value == "->":
            if not is_pointer(t) or not is_struct(pointee(t)):
                self.report("error", f"'->' needs a pointer to a struct, found {t}",
                            op.line, op.col)
                return Operand("error")
            t = pointee(t)
        elif not is_struct(t):
            hint = " (use '->' for pointers)" if is_pointer(t) and is_struct(pointee(t)) else ""
            self.report("error", f"'.' needs a struct, found {t}{hint}", op.line, op.col)
            return Operand("error")
        tag = self.st.resolve(t)
        if tag is None or not tag.defined:
            self.report("error", f"'{t}' is an incomplete type", op.line, op.col)
            return Operand("error")
        for mname, mtype, mdims in tag.members:
            if mname == name_tok.value:
                if mdims:
                    return Operand(pointer_to(mtype), None, "array", dims=tuple(mdims))
                return Operand(mtype, None, "elem")
        self.report("error", f"'{t}' has no member named '{name_tok.value}'",
                    name_tok.line, name_tok.col)
        return Operand("error")

    # ------------------------------------------------------------ primary
    def primary(self):
        t = self.peek()
        if t.kind == INT_LIT:
            self.advance()
            low = t.value.lower()
            if "u" in low and "l" in low and not low.startswith("0x"):
                typ = "unsigned long"
            elif low.endswith(("ul", "lu", "ull", "llu")) and not low.startswith("0x"):
                typ = "unsigned long"
            elif low.rstrip("u").endswith("l") and not low.startswith("0x"):
                typ = "long"
            elif low.endswith("u") and not low.startswith("0x"):
                typ = "unsigned int"
            else:
                typ = "int"
            return Operand(typ, lit=t.value)
        if t.kind == FLOAT_LIT:
            self.advance()
            return Operand("float", lit=t.value)
        if t.kind == CHAR_LIT:
            self.advance()
            code = char_value(t.value)
            return Operand("char", lit=None if code is None else str(code))
        if t.kind == STRING_LIT:
            self.advance()
            while self.peek().kind == STRING_LIT:          # "a" "b" is one string
                self.advance()
            return Operand("string", kind="string")
        if t.kind == PUNCT and t.value == "(":
            self.advance()
            inner = self.expression()
            self.expect_punct(")", "to close the parenthesis")
            return inner
        if t.kind == IDENT:
            return self.identifier()
        raise ParseError(f"unexpected {self.describe(t)} in expression", t)

    # ------------------------------------------------------ names and calls
    def identifier(self):
        tok = self.advance()
        name = tok.value
        no_read = self._no_read_next
        self._no_read_next = False
        nxt = self.peek()
        is_call = nxt.kind == PUNCT and nxt.value == "("
        before = self.toks[self.pos - 2] if self.pos >= 2 else None
        after_star = before is not None and before.kind == OP and before.value == "*"
        # `x = ...` writes x, but `*p = ...` only reads the pointer p
        is_plain_write = nxt.kind == OP and nxt.value == "=" and not after_star
        sym = self.st.lookup(name, tok.line)

        if is_call:
            args = self.parse_args()
            if sym is None:
                self.report("error", f"call to undeclared function '{name}'", tok.line, tok.col)
                return Operand("error")
            return self.check_call(tok, sym, args)

        if sym is None:
            self.report("error", f"use of undeclared identifier '{name}'", tok.line, tok.col)
            return Operand("error")
        if sym.kind == "function":
            self.report("error", f"function '{name}' is used without a call", tok.line, tok.col)
            return Operand("error")
        if sym.kind in ("typedef", "struct", "enum", "macro"):
            what = "a type name" if sym.kind in ("typedef", "struct", "enum") else "a macro"
            self.report("error", f"'{name}' is {what}, not a variable", tok.line, tok.col)
            return Operand("error")
        sym.add_ref(tok.line)
        if sym.kind == "constant":
            sym.used = True
            return Operand(sym.type, sym, "value", lit=str(sym.value))
        if sym.kind == "array":
            sym.used = True
            return Operand(pointer_to(sym.type), sym, "array", dims=tuple(sym.dims))

        if no_read:
            sym.used = True
            sym.initialized = True          # &x - assume the callee (e.g. scanf) fills it in
        elif not is_plain_write:
            self.mark_read(sym, tok)
        return Operand(sym.type, sym, "var")

    def mark_read(self, sym, tok):
        sym.used = True
        key = (sym.scope_id, sym.name)
        if (not sym.initialized and sym.level > 0 and sym.kind == "variable"
                and not is_struct(sym.type) and key not in self._uninit_reported):
            self._uninit_reported.add(key)
            self.report("warning", f"variable '{sym.name}' is used before it is initialized",
                        tok.line, tok.col)

    def parse_args(self):
        self.expect_punct("(")
        args = []
        if not self.check_punct(")"):
            while True:
                args.append(self.assignment())
                if not self.match_punct(","):
                    break
        self.expect_punct(")", "to close the argument list")
        return args

    def check_call(self, tok, sym, args):
        name = sym.name
        if sym.kind != "function":
            self.report("error", f"'{name}' is not a function and cannot be called",
                        tok.line, tok.col)
            return Operand("error")
        sym.used = True
        sym.add_ref(tok.line)
        expected, given = len(sym.params), len(args)
        if given < expected or (given > expected and not sym.variadic):
            qualifier = "at least " if sym.variadic else ""
            self.report("error",
                        f"function '{name}' expects {qualifier}{expected} argument(s) but {given} given",
                        tok.line, tok.col)
        else:
            for n, arg in enumerate(args, start=1):
                if n <= expected:
                    self.check_assign(sym.params[n - 1][0], arg, tok, f"argument {n} of '{name}'")
                elif arg.type == "void":
                    self.report("error", f"void value passed as argument {n} of '{name}'",
                                tok.line, tok.col)
        return Operand(sym.type)


def base_size_small(t):
    """True for integer types narrower than int (they are promoted to int in arithmetic)."""
    return t in ("char", "unsigned char", "short", "unsigned short") or is_enum(t)
