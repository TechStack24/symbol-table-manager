"""Token definitions for the C subset handled by the compiler front end."""

from dataclasses import asdict, dataclass

BASIC_TYPE_WORDS = frozenset({
    "int", "float", "char", "void", "double", "short", "long", "signed", "unsigned",
})
# Qualifiers / storage classes: accepted and ignored (they do not change the symbol's type here).
TYPE_QUALIFIERS = frozenset({"const", "static", "extern", "register", "volatile", "auto", "inline"})
# Words that can start a declaration (a typedef name can too, see Parser.at_type_start).
TYPE_KEYWORDS = BASIC_TYPE_WORDS | TYPE_QUALIFIERS | {"struct", "enum", "union", "typedef"}

CONTROL_KEYWORDS = frozenset({
    "if", "else", "while", "do", "for", "return", "break", "continue",
    "switch", "case", "default", "goto", "sizeof",
})
KEYWORDS = TYPE_KEYWORDS | CONTROL_KEYWORDS

# Token kinds
KEYWORD = "KEYWORD"
IDENT = "IDENT"
INT_LIT = "INT_LIT"
FLOAT_LIT = "FLOAT_LIT"
CHAR_LIT = "CHAR_LIT"
STRING_LIT = "STRING_LIT"
OP = "OP"
PUNCT = "PUNCT"
EOF = "EOF"
PP = "PP"          # internal: a #define / #undef directive travelling through the token list

THREE_CHAR_OPS = frozenset({"<<=", ">>="})
TWO_CHAR_OPS = frozenset({
    "==", "!=", "<=", ">=", "&&", "||", "<<", ">>", "->",
    "++", "--", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
})
ONE_CHAR_OPS = frozenset("+-*/%=<>!&|^~?:.")
PUNCTUATION = frozenset("(){}[],;")


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    line: int
    col: int

    def to_dict(self):
        return asdict(self)
