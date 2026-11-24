"""Glue code: runs lexer -> parser/semantic analysis and builds a JSON-ready report."""

from collections import Counter

from .lexer import Lexer
from .parser import Parser
from .symbol_table import SymbolTable
from .tokens import EOF

MAX_TOKENS_RETURNED = 3000


def analyze_source(source):
    """Analyse C source text and return a JSON-serialisable report."""
    lexer = Lexer(source)
    tokens, lex_errors = lexer.tokenize()
    table = SymbolTable()
    table.install_macros(lexer.macros)
    parse_diags = Parser(tokens, table).parse()

    diagnostics = sorted(lex_errors + parse_diags, key=lambda d: (d["line"], d["col"]))
    counts = Counter(d["severity"] for d in diagnostics)
    visible_tokens = [t.to_dict() for t in tokens if t.kind != EOF]

    report = {
        "ok": counts["error"] == 0,
        "summary": {
            "lines": len(source.splitlines()),
            "tokens": len(visible_tokens),
            "symbols": table.symbol_count(),
            "scopes": len(table.scopes),
            "errors": counts["error"],
            "warnings": counts["warning"],
            "infos": counts["info"],
        },
        "diagnostics": diagnostics,
        "tokens": visible_tokens[:MAX_TOKENS_RETURNED],
        "tokens_truncated": len(visible_tokens) > MAX_TOKENS_RETURNED,
    }
    report.update(table.to_dict())
    return report
