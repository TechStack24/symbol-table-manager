"""Interactive playground: drive a SymbolTable with simple text commands.

Commands
--------
insert <name> <type> [kind] [size]   kind = variable | array | function | parameter
                                     (an array needs a size, e.g. ``insert buf char array 16``)
lookup <name>
delete <name>
enter [scope-name]
exit

The web UI is stateless: it re-sends the whole command history and the server
replays it on a fresh table, so results are always deterministic.
"""

import re

from .symbol_table import Symbol, SymbolTable

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TYPES = ("int", "float", "char", "void", "double", "long", "short")
KINDS = ("variable", "array", "function", "parameter")
MAX_COMMANDS = 200


def _need_ident(word, what="name"):
    if not IDENT_RE.match(word):
        raise ValueError(f"'{word}' is not a valid {what} (use letters, digits and _)")
    return word


def parse_command(text):
    parts = text.strip().split()
    if not parts:
        raise ValueError("empty command")
    op, args = parts[0].lower(), parts[1:]

    if op == "insert":
        if not 2 <= len(args) <= 4:
            raise ValueError("usage: insert <name> <type> [kind] [size]")
        name = _need_ident(args[0])
        type_ = args[1].lower()
        if type_ not in TYPES:
            raise ValueError(f"unknown type '{args[1]}' (choose from {', '.join(TYPES)})")
        kind = args[2].lower() if len(args) > 2 else "variable"
        if kind not in KINDS:
            raise ValueError(f"unknown kind '{args[2]}' (choose from {', '.join(KINDS)})")
        size = None
        if kind == "array":
            if len(args) != 4 or not args[3].isdigit() or int(args[3]) <= 0:
                raise ValueError("an array needs a positive size, e.g. insert buf char array 16")
            size = int(args[3])
        elif len(args) == 4:
            raise ValueError("a size is only valid for kind 'array'")
        if type_ == "void" and kind != "function":
            raise ValueError("only functions can have type 'void'")
        return {"op": "insert", "name": name, "type": type_, "kind": kind, "size": size}

    if op in ("lookup", "delete"):
        if len(args) != 1:
            raise ValueError(f"usage: {op} <name>")
        return {"op": op, "name": _need_ident(args[0])}

    if op == "enter":
        if len(args) > 1:
            raise ValueError("usage: enter [scope-name]")
        return {"op": "enter", "name": _need_ident(args[0], "scope name") if args else None}

    if op == "exit":
        if args:
            raise ValueError("usage: exit")
        return {"op": "exit"}

    raise ValueError(f"unknown command '{parts[0]}' (try insert, lookup, delete, enter, exit)")


def _execute(table, cmd, number):
    op = cmd["op"]
    if op == "insert":
        sym = Symbol(
            cmd["name"], cmd["kind"], cmd["type"], line=number,
            array_size=cmd["size"], initialized=True,
        )
        ok, existing = table.insert(sym)
        if ok:
            return True, (f"inserted '{sym.signature()}' ({sym.kind}) into scope "
                          f"'{table.current.name}'")
        return False, (f"'{cmd['name']}' is already declared in scope "
                       f"'{table.current.name}' (command #{existing.line})")

    if op == "lookup":
        sym = table.lookup(cmd["name"], number)
        if sym is None:
            return False, f"'{cmd['name']}' is not visible from scope '{table.current.name}'"
        return True, (f"'{sym.signature()}' found in scope '{sym.scope_name}' "
                      f"(level {sym.level})")

    if op == "delete":
        if table.delete(cmd["name"], number):
            return True, f"removed '{cmd['name']}' from scope '{table.current.name}'"
        return False, f"'{cmd['name']}' is not declared in scope '{table.current.name}'"

    if op == "enter":
        name = cmd["name"] or f"block{number}"
        table.enter_scope(name, "block", number)
        return True, f"entered scope '{name}' (nesting depth {table.depth})"

    if table.depth <= 1:
        raise ValueError("cannot exit the global scope")
    scope = table.exit_scope(number)
    return True, (f"left scope '{scope.name}'; back in '{table.current.name}' "
                  f"({scope.table.count} name(s) went out of scope)")


def run_commands(commands):
    table = SymbolTable(with_builtins=False)
    results = []
    for number, text in enumerate(commands, start=1):
        try:
            cmd = parse_command(text)
            ok, message = _execute(table, cmd, number)
        except ValueError as exc:
            ok, message = False, str(exc)
        results.append({"index": number, "command": text.strip(), "ok": ok, "message": message})
    state = table.to_dict()
    state["results"] = results
    return state
