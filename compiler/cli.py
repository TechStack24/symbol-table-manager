"""Interactive command-line interface: type, paste or load a C program."""

import sys

from .analyzer import analyze_source
from .playground import run_commands

SAMPLE_PROGRAM = """\
// Sample: nested scopes and shadowing
int level = 0;

int main() {
    int x = 10;
    float y = 2.5;
    {
        int x = 20;
        char grade = 'A';
        printf("inner x = %d, grade = %c\\n", x, grade);
    }
    for (int i = 0; i < 3; i++) {
        int square = i * i;
        x = x + square;
    }
    printf("x = %d, y = %f, level = %d\\n", x, y, level);
    return 0;
}
"""

SAMPLE_ERRORS = """\
int counter = 0;
int counter = 1;

int square(int n) {
    return n * n;
}

int main() {
    float f = 3.7;
    int a = f;
    int total;
    int r = total + 1;
    b = 5;
    r = square(1, 2);
    break;
    return 0;
}
"""

HELP_TEXT = """\
Playground commands:
  insert <name> <type> [kind] [size]   kind: variable | array | function | parameter
                                       (arrays need a size, e.g. insert buf char array 16)
  lookup <name>                        search current scope, then outward to global
  delete <name>                        remove from the current scope
  enter [name]                         open a nested scope
  exit                                 close the innermost scope
  show                                 print the symbol tables
  help                                 this text
  back                                 return to the main menu
"""


def hr(char="-", width=78):
    print(char * width)


def read_program():
    """Read a multi-line program from the user; stop at a line with only END."""
    print("Type or paste your C program below.")
    print("When finished, type END on its own line and press Enter.")
    print("(Types: int float char void. Built-ins: printf, abs, sqrt.)")
    hr()
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().upper() == "END":
            break
        lines.append(line)
    return "\n".join(lines) + "\n"


def print_table(headers, rows):
    if not rows:
        print("  (empty)")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  " + " | ".join("{:<%d}" % w for w in widths)
    print(fmt.format(*headers))
    print("  " + "-+-".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))


def show_diagnostics(report):
    print("\nDIAGNOSTICS")
    hr()
    if not report["diagnostics"]:
        print("  No problems found.")
        return
    for d in report["diagnostics"]:
        pos = f"{d['line']}:{d['col']}"
        print(f"  {pos:<8}{d['severity']:<9}[{d['phase']}] {d['message']}")


def show_scopes(scopes, active=None, include_builtins=False):
    print("\nSYMBOL TABLE (one table per scope)")
    hr()
    for sc in scopes:
        symbols = [s for s in sc["symbols"] if include_builtins or not s["builtin"]]
        if not symbols and sc["kind"] == "global" and not include_builtins:
            pass
        indent = "  " * sc["level"]
        status = "closed" if sc["closed"] else "active"
        line = f" (opened line {sc['opened_line']})" if sc["opened_line"] else ""
        print(f"\n{indent}Scope '{sc['name']}'  kind={sc['kind']}  level={sc['level']}  "
              f"[{status}]{line}")
        rows = [
            (s["name"], s["kind"], s["signature"], s["line"] or "-",
             s["size"], "-" if s["offset"] is None else s["offset"],
             "yes" if s["initialized"] else "no", "yes" if s["used"] else "no",
             ",".join(str(r) for r in s["refs"]) or "-")
            for s in symbols
        ]
        print_table(
            ("Name", "Kind", "Signature", "Line", "Size", "Offset", "Init", "Used", "Ref lines"),
            rows,
        )
        st = sc["stats"]
        print(f"  hash table: {st['count']} item(s) in {st['capacity']} bucket(s), "
              f"load {st['load_factor']}, collisions {st['collisions']}, resizes {st['resizes']}")


def show_buckets(scopes):
    print("\nHASH BUCKETS (djb2 hash, separate chaining)")
    hr()
    for sc in scopes:
        print(f"\nScope '{sc['name']}' ({sc['stats']['capacity']} buckets)")
        for b in sc["buckets"]:
            chain = " -> ".join(c["name"] for c in b["chain"]) or "."
            print(f"  [{b['index']:>2}] {chain}")


def show_tokens(report):
    print("\nTOKENS")
    hr()
    print_table(
        ("#", "Kind", "Value", "Line", "Col"),
        [(i, t["kind"], t["value"], t["line"], t["col"])
         for i, t in enumerate(report["tokens"], 1)],
    )
    if report["tokens_truncated"]:
        print("  ... token list truncated")


def show_log(report):
    print("\nOPERATIONS LOG")
    hr()
    print_table(
        ("Step", "Operation", "Name", "Scope", "OK", "Detail"),
        [(e["step"], e["op"], e["name"], e["scope"], "yes" if e["ok"] else "no", e["detail"])
         for e in report["log"]],
    )


def show_summary(report):
    s = report["summary"]
    print("\nSUMMARY")
    hr()
    print(f"  lines: {s['lines']}   tokens: {s['tokens']}   symbols: {s['symbols']}   "
          f"scopes: {s['scopes']}")
    print(f"  errors: {s['errors']}   warnings: {s['warnings']}   info: {s['infos']}")
    m = report["metrics"]
    print(f"  inserts: {m['inserts']} (rejected {m['insert_rejected']})   "
          f"lookups: {m['lookups']} (hits {m['lookup_hits']}, misses {m['lookup_misses']})   "
          f"max depth: {m['max_depth']}")
    print("  RESULT: " + ("no errors" if report["ok"] else "errors found"))


def analyze_and_show(source, verbose=False):
    if not source.strip():
        print("Nothing to analyze (empty input).")
        return
    report = analyze_source(source)
    show_summary(report)
    show_diagnostics(report)
    show_scopes(report["scopes"])
    if verbose:
        show_buckets(report["scopes"])
        show_tokens(report)
        show_log(report)
        return
    while True:
        try:
            choice = input("\nMore? [b]uckets  [t]okens  [l]og  [a]ll  [Enter] continue: ")
        except EOFError:
            return
        choice = choice.strip().lower()
        if choice in ("", "c", "q"):
            return
        if choice in ("b", "a"):
            show_buckets(report["scopes"])
        if choice in ("t", "a"):
            show_tokens(report)
        if choice in ("l", "a"):
            show_log(report)
        if choice == "a":
            return


def playground():
    print("\nPLAYGROUND - drive the symbol table by hand.")
    print(HELP_TEXT)
    commands = []
    while True:
        try:
            text = input("symtab> ").strip()
        except EOFError:
            return
        if not text:
            continue
        low = text.lower()
        if low in ("back", "quit", "q"):
            return
        if low == "help":
            print(HELP_TEXT)
            continue
        # The playground replays the whole history, so every result is deterministic.
        trial = commands + [text]
        state = run_commands(trial)
        result = state["results"][-1]
        if low != "show":
            print(("  OK    " if result["ok"] else "  ERROR ") + result["message"])
            if result["ok"]:
                commands = trial
            if low.split()[0] in ("insert", "lookup", "delete", "enter", "exit") and result["ok"]:
                continue
            if not result["ok"]:
                continue
        state = run_commands(commands)
        print("  active scopes: " + " > ".join(state["active_scopes"]))
        show_scopes([sc for sc in state["scopes"] if not sc["closed"]], include_builtins=False)


def read_file_source():
    path = input("Path of the .c file: ").strip().strip('"').strip("'")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        print(f"Could not read file: {exc}")
        return ""


MENU = """
==================== SYMBOL TABLE MANAGER ====================
  1) Enter my own program (type / paste)
  2) Load a program from a file
  3) Playground (insert / lookup / delete / enter / exit)
  4) Run built-in sample (scopes and shadowing)
  5) Run built-in sample with errors
  0) Exit
"""


def main(argv):
    # Non-interactive use: a file argument or piped stdin.
    if len(argv) > 1:
        try:
            with open(argv[1], "r", encoding="utf-8") as fh:
                analyze_and_show(fh.read(), verbose="-v" in argv[2:])
        except OSError as exc:
            print(f"Could not read '{argv[1]}': {exc}")
            return 1
        return 0
    if not sys.stdin.isatty():
        analyze_and_show(sys.stdin.read(), verbose=True)
        return 0

    while True:
        print(MENU)
        try:
            choice = input("Choose an option: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice == "1":
            analyze_and_show(read_program())
        elif choice == "2":
            analyze_and_show(read_file_source())
        elif choice == "3":
            playground()
        elif choice == "4":
            print(SAMPLE_PROGRAM)
            analyze_and_show(SAMPLE_PROGRAM)
        elif choice == "5":
            print(SAMPLE_ERRORS)
            analyze_and_show(SAMPLE_ERRORS)
        elif choice in ("0", "q", "exit"):
            print("Goodbye.")
            return 0
        else:
            print("Please choose 0-5.")


def run():
    try:
        return main(sys.argv)
    except KeyboardInterrupt:
        print()
        return 0
