"""Symbol table for the C-subset compiler front end.

Design
------
* Every scope owns a ``HashTable`` (separate chaining, djb2 hash, auto-resize).
* Scopes live on a stack: opening a block pushes a scope, closing it pops one.
* ``lookup`` walks the stack from the innermost scope outwards, so an inner
  declaration shadows an outer one.
* A ``struct`` gets a scope of its own that holds its members (kind ``member``).
* Popped scopes are *kept* in ``SymbolTable.scopes`` so the web UI can show the
  complete history. A production compiler would discard them when popped.
* Every operation is written to an operation log (insert / lookup / delete /
  enter_scope / exit_scope) which the UI shows step by step.
"""

from dataclasses import dataclass, field
from typing import Optional

from .typeinfo import base_size, is_pointer, is_struct

# Kinds of symbol that occupy storage (and therefore get a size and a frame offset).
STORAGE_KINDS = ("variable", "array", "parameter", "member")

# (name, return type, [(param type, param name)], variadic)
BUILTINS = (
    ("printf", "int", [("char *", "format")], True),
    ("scanf", "int", [("char *", "format")], True),
    ("sprintf", "int", [("char *", "buffer"), ("char *", "format")], True),
    ("puts", "int", [("char *", "s")], False),
    ("putchar", "int", [("int", "c")], False),
    ("getchar", "int", [], False),
    ("gets", "char *", [("char *", "s")], False),
    ("strlen", "unsigned long", [("char *", "s")], False),
    ("strcpy", "char *", [("char *", "dest"), ("char *", "src")], False),
    ("strncpy", "char *", [("char *", "dest"), ("char *", "src"), ("unsigned long", "n")], False),
    ("strcat", "char *", [("char *", "dest"), ("char *", "src")], False),
    ("strcmp", "int", [("char *", "a"), ("char *", "b")], False),
    ("strncmp", "int", [("char *", "a"), ("char *", "b"), ("unsigned long", "n")], False),
    ("malloc", "void *", [("unsigned long", "size")], False),
    ("calloc", "void *", [("unsigned long", "count"), ("unsigned long", "size")], False),
    ("realloc", "void *", [("void *", "ptr"), ("unsigned long", "size")], False),
    ("free", "void", [("void *", "ptr")], False),
    ("exit", "void", [("int", "code")], False),
    ("atoi", "int", [("char *", "s")], False),
    ("atof", "double", [("char *", "s")], False),
    ("rand", "int", [], False),
    ("srand", "void", [("unsigned int", "seed")], False),
    ("time", "long", [("void *", "t")], False),
    ("abs", "int", [("int", "x")], False),
    ("labs", "long", [("long", "x")], False),
    ("sqrt", "double", [("double", "x")], False),
    ("pow", "double", [("double", "base"), ("double", "exp")], False),
    ("sin", "double", [("double", "x")], False),
    ("cos", "double", [("double", "x")], False),
    ("tan", "double", [("double", "x")], False),
    ("fabs", "double", [("double", "x")], False),
    ("floor", "double", [("double", "x")], False),
    ("ceil", "double", [("double", "x")], False),
    ("log", "double", [("double", "x")], False),
    ("exp", "double", [("double", "x")], False),
    ("fopen", "struct FILE *", [("char *", "path"), ("char *", "mode")], False),
    ("fclose", "int", [("struct FILE *", "f")], False),
    ("fprintf", "int", [("struct FILE *", "f"), ("char *", "format")], True),
    ("fscanf", "int", [("struct FILE *", "f"), ("char *", "format")], True),
    ("fgets", "char *", [("char *", "buf"), ("int", "n"), ("struct FILE *", "f")], False),
    ("fputs", "int", [("char *", "s"), ("struct FILE *", "f")], False),
    ("fgetc", "int", [("struct FILE *", "f")], False),
    ("fputc", "int", [("int", "c"), ("struct FILE *", "f")], False),
    ("feof", "int", [("struct FILE *", "f")], False),
    ("toupper", "int", [("int", "c")], False),
    ("tolower", "int", [("int", "c")], False),
    ("isdigit", "int", [("int", "c")], False),
    ("isalpha", "int", [("int", "c")], False),
    ("isalnum", "int", [("int", "c")], False),
    ("isspace", "int", [("int", "c")], False),
    ("isupper", "int", [("int", "c")], False),
    ("islower", "int", [("int", "c")], False),
)
# (name, type, value)
BUILTIN_CONSTANTS = (
    ("NULL", "void *", 0),
    ("EOF", "int", -1),
    ("INT_MAX", "int", 2147483647),
    ("INT_MIN", "int", -2147483648),
    ("RAND_MAX", "int", 2147483647),
)
# stdin / stdout / stderr are `FILE *` variables
BUILTIN_STREAMS = ("stdin", "stdout", "stderr")
# (name, underlying type)
BUILTIN_TYPEDEFS = (
    ("size_t", "unsigned long"),
    ("FILE", "struct FILE"),
    ("int8_t", "char"), ("uint8_t", "unsigned char"),
    ("int16_t", "short"), ("uint16_t", "unsigned short"),
    ("int32_t", "int"), ("uint32_t", "unsigned int"),
    ("int64_t", "long"), ("uint64_t", "unsigned long"),
)


def _decl(type_, name):
    """``int`` + ``x`` -> ``int x``;  ``int *`` + ``p`` -> ``int *p``."""
    return f"{type_}{name}" if type_.endswith("*") else f"{type_} {name}"


def _product(values):
    total = 1
    for v in values:
        total *= v
    return total


@dataclass
class Symbol:
    name: str
    kind: str                     # variable | array | parameter | function | member | typedef
                                  # | struct | enum | constant | macro
    type: str                     # int | double | char * | struct Point ... (return type for functions)
    line: int = 0
    col: int = 0
    array_size: Optional[int] = None             # first dimension of an array
    params: list = field(default_factory=list)   # [(type, name)] for functions
    defined: bool = True          # False for a prototype / an incomplete struct
    builtin: bool = False
    variadic: bool = False
    initialized: bool = False
    used: bool = False
    refs: list = field(default_factory=list)     # lines where the name appears
    scope_id: int = 0
    scope_name: str = ""
    level: int = 0
    size: int = 0                 # bytes of storage
    offset: Optional[int] = None  # byte offset inside the scope's frame
    seq: int = 0                  # declaration order
    dims: list = field(default_factory=list)     # all array dimensions, e.g. [3, 4] for a[3][4]
    value: Optional[int] = None   # value of an enum constant / builtin constant
    members: list = field(default_factory=list)  # [(name, type, dims)] for a struct
    text: str = ""                # the "#define ..." line of a macro
    overrides_builtin: bool = False

    def __post_init__(self):
        if self.array_size is not None and not self.dims:
            self.dims = [self.array_size]
        if self.dims and self.array_size is None:
            self.array_size = self.dims[0]

    def add_ref(self, line):
        if line and line not in self.refs:
            self.refs.append(line)

    def signature(self):
        if self.kind == "function":
            parts = [_decl(t, n).strip() for t, n in self.params]
            if self.variadic:
                parts.append("...")
            return _decl(self.type, f"{self.name}({', '.join(parts)})")
        if self.kind == "array":
            return _decl(self.type, self.name) + "".join(f"[{d}]" for d in self.dims)
        if self.kind == "macro":
            return self.text
        if self.kind == "typedef":
            return f"typedef {_decl(self.type, self.name)}"
        if self.kind == "constant":
            return f"{self.name} = {self.value}"
        if self.kind == "struct":
            if not self.defined:
                return f"{self.name} (incomplete)"
            fields = " ".join(
                _decl(t, n) + "".join(f"[{d}]" for d in dims) + ";" for n, t, dims in self.members)
            return f"{self.name} {{ {fields} }}" if fields else f"{self.name} {{ }}"
        if self.kind == "enum":
            return self.name
        return _decl(self.type, self.name)

    def to_dict(self):
        return {
            "name": self.name,
            "kind": self.kind,
            "type": self.type,
            "signature": self.signature(),
            "line": self.line,
            "col": self.col,
            "array_size": self.array_size,
            "dims": list(self.dims),
            "value": self.value,
            "members": [{"name": n, "type": t, "dims": list(d)} for n, t, d in self.members],
            "defined": self.defined,
            "builtin": self.builtin,
            "initialized": self.initialized,
            "used": self.used,
            "refs": sorted(self.refs),
            "scope_id": self.scope_id,
            "scope_name": self.scope_name,
            "level": self.level,
            "size": self.size,
            "offset": self.offset,
        }


class HashTable:
    """Separate-chaining hash table keyed by identifier name."""

    def __init__(self, capacity=8, max_load=0.75):
        self.capacity = capacity
        self.max_load = max_load
        self.buckets = [[] for _ in range(capacity)]
        self.count = 0
        self.resizes = 0

    @staticmethod
    def hash_name(name):
        """djb2 string hash (32-bit)."""
        h = 5381
        for ch in name:
            h = ((h << 5) + h + ord(ch)) & 0xFFFFFFFF
        return h

    def _index(self, name):
        return self.hash_name(name) % self.capacity

    def find(self, name):
        """Return (symbol or None, bucket index, number of probes)."""
        idx = self._index(name)
        probes = 0
        for sym in self.buckets[idx]:
            probes += 1
            if sym.name == name:
                return sym, idx, probes
        return None, idx, probes

    def lookup(self, name):
        return self.find(name)[0]

    def insert(self, sym):
        """Insert a symbol. Returns the bucket index, or None if the name exists."""
        idx = self._index(sym.name)
        if any(existing.name == sym.name for existing in self.buckets[idx]):
            return None
        self.buckets[idx].append(sym)
        self.count += 1
        if self.count / self.capacity > self.max_load:
            self._resize()
        return idx

    def delete(self, name):
        idx = self._index(name)
        bucket = self.buckets[idx]
        for pos, sym in enumerate(bucket):
            if sym.name == name:
                del bucket[pos]
                self.count -= 1
                return True
        return False

    def _resize(self):
        old = self.items()
        self.capacity *= 2
        self.buckets = [[] for _ in range(self.capacity)]
        for sym in old:
            self.buckets[self._index(sym.name)].append(sym)
        self.resizes += 1

    def items(self):
        """All symbols in declaration order."""
        return sorted((s for bucket in self.buckets for s in bucket), key=lambda s: s.seq)

    def bucket_view(self):
        return [
            {"index": i, "chain": [{"name": s.name, "builtin": s.builtin} for s in bucket]}
            for i, bucket in enumerate(self.buckets)
        ]

    def stats(self):
        lengths = [len(b) for b in self.buckets]
        return {
            "capacity": self.capacity,
            "count": self.count,
            "load_factor": round(self.count / self.capacity, 2),
            "used_buckets": sum(1 for n in lengths if n),
            "longest_chain": max(lengths) if lengths else 0,
            "collisions": sum(n - 1 for n in lengths if n > 1),
            "resizes": self.resizes,
        }


class Scope:
    def __init__(self, scope_id, name, kind, level, parent, start_offset, opened_line):
        self.id = scope_id
        self.name = name
        self.kind = kind              # global | function | block
        self.level = level
        self.parent = parent
        self.offset = start_offset    # next free byte in this scope's frame
        self.opened_line = opened_line
        self.closed = False
        self.table = HashTable()

    def to_dict(self):
        symbols = [s.to_dict() for s in self.table.items()]
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "level": self.level,
            "parent_id": self.parent.id if self.parent else None,
            "opened_line": self.opened_line,
            "closed": self.closed,
            "storage_bytes": sum(s["size"] for s in symbols if s["kind"] in STORAGE_KINDS),
            "symbols": symbols,
            "buckets": self.table.bucket_view(),
            "stats": self.table.stats(),
        }


class SymbolTable:
    MAX_LOG = 5000

    def __init__(self, with_builtins=True):
        self.scopes = []              # every scope ever opened (creation order)
        self.stack = []               # currently active scopes
        self.log = []
        self.log_truncated = False
        self.metrics = self._fresh_metrics()
        self._seq = 0
        self._step = 0
        self._logging = True
        self.enter_scope("global", "global")
        if with_builtins:
            self._install_builtins()

    # ----------------------------------------------------------------- set-up
    @staticmethod
    def _fresh_metrics():
        return {
            "inserts": 0, "insert_rejected": 0,
            "lookups": 0, "lookup_hits": 0, "lookup_misses": 0,
            "deletes": 0, "scopes_opened": 0, "max_depth": 0,
        }

    def _install_builtins(self):
        self._logging = False
        for name, ret, params, variadic in BUILTINS:
            self.insert(Symbol(
                name, "function", ret, params=list(params), variadic=variadic,
                builtin=True, initialized=True, used=True,
            ))
        for name, type_, value in BUILTIN_CONSTANTS:
            self.insert(Symbol(name, "constant", type_, value=value,
                               builtin=True, initialized=True, used=True))
        self.insert(Symbol("struct FILE", "struct", "struct FILE", builtin=True,
                           initialized=True, used=True))          # FILE is opaque (no members)
        for name, type_ in BUILTIN_TYPEDEFS:
            self.insert(Symbol(name, "typedef", type_, builtin=True, initialized=True, used=True))
        for name in BUILTIN_STREAMS:
            self.insert(Symbol(name, "variable", "struct FILE *", builtin=True, initialized=True, used=True))
        self._logging = True
        self.metrics = self._fresh_metrics()
        self.metrics["scopes_opened"] = 1
        self.metrics["max_depth"] = 1

    def install_macros(self, macros):
        """Record the preprocessor's #define names in the global scope."""
        for m in macros:
            head = m["name"] if m["params"] is None else f"{m['name']}({', '.join(m['params'])})"
            text = f"#define {head} {m['body']}".rstrip()
            self.insert(Symbol(m["name"], "macro", "macro", m["line"], m["col"],
                               initialized=True, used=True, text=text), scope=self.stack[0])

    # ------------------------------------------------------------- properties
    @property
    def current(self):
        return self.stack[-1]

    @property
    def depth(self):
        return len(self.stack)

    def symbol_count(self, include_builtins=False):
        return sum(
            1 for scope in self.scopes for s in scope.table.items()
            if include_builtins or not s.builtin
        )

    # ---------------------------------------------------------------- logging
    def _log(self, op, name, ok, detail, line=None):
        if not self._logging:
            return
        if len(self.log) >= self.MAX_LOG:
            self.log_truncated = True
            return
        self._step += 1
        self.log.append({
            "step": self._step, "op": op, "name": name,
            "scope": self.current.name, "ok": ok, "detail": detail, "line": line,
        })

    # ----------------------------------------------------------------- scopes
    def enter_scope(self, name, kind="block", line=None):
        parent = self.stack[-1] if self.stack else None
        start_offset = parent.offset if parent is not None and parent.level > 0 else 0
        if kind == "struct":
            start_offset = 0                 # members are laid out from the start of the struct
        scope = Scope(len(self.scopes), name, kind, len(self.stack), parent, start_offset, line)
        self.scopes.append(scope)
        self.stack.append(scope)
        self.metrics["scopes_opened"] += 1
        self.metrics["max_depth"] = max(self.metrics["max_depth"], len(self.stack))
        self._log("enter_scope", name, True, f"pushed {kind} scope at level {scope.level}", line)
        return scope

    def exit_scope(self, line=None):
        if len(self.stack) <= 1:
            raise RuntimeError("cannot exit the global scope")
        scope = self.stack[-1]
        self._log(
            "exit_scope", scope.name, True,
            f"popped level {scope.level}; {scope.table.count} symbol(s) go out of scope", line,
        )
        scope.closed = True
        self.stack.pop()
        return scope

    def finish(self):
        """Close the global scope at the end of the program."""
        glob = self.stack[0]
        self._log("exit_scope", glob.name, True, "end of program: global scope closed")
        glob.closed = True

    # -------------------------------------------------------------- operations
    def insert(self, sym, scope=None):
        """Insert into the current scope. Returns (ok, symbol_in_table)."""
        scope = scope or self.current
        existing = scope.table.lookup(sym.name)
        replaced = False
        if (existing is not None and existing.builtin and not sym.builtin
                and scope.level == 0 and sym.kind != "macro"):
            scope.table.delete(sym.name)         # a user definition replaces the built-in
            existing, replaced = None, True
            sym.overrides_builtin = True
        if existing is not None:
            self.metrics["insert_rejected"] += 1
            self._log(
                "insert", sym.name, False,
                f"rejected: already declared in scope '{scope.name}' (line {existing.line})",
                sym.line,
            )
            return False, existing

        sym.scope_id = scope.id
        sym.scope_name = scope.name
        sym.level = scope.level
        self._seq += 1
        sym.seq = self._seq
        if sym.kind in STORAGE_KINDS and not sym.builtin:
            sym.size = self.sizeof(sym.type) * (_product(sym.dims) if sym.dims else 1)
            sym.offset = scope.offset
            scope.offset += sym.size

        resizes_before = scope.table.resizes
        bucket = scope.table.insert(sym)
        self.metrics["inserts"] += 1
        detail = f"stored {sym.kind} '{sym.type}' in bucket {bucket}"
        if sym.offset is not None:
            detail += f", offset {sym.offset}, size {sym.size} byte(s)"
        if replaced:
            detail += "; replaces the built-in of the same name"
        if scope.table.resizes != resizes_before:
            detail += f"; table grew to {scope.table.capacity} buckets and was rehashed"
        self._log("insert", sym.name, True, detail, sym.line)
        return True, sym

    def sizeof(self, type_):
        """Storage size in bytes of a type (a struct's size comes from its tag symbol)."""
        if is_pointer(type_):
            return base_size(type_)
        if is_struct(type_):
            tag = self.resolve(type_)
            return tag.size if tag is not None and tag.kind == "struct" else 0
        return base_size(type_)

    def resize(self, sym, dims):
        """Change the dimensions of the most recently inserted array (``int a[] = {...}``)."""
        scope = self.current
        old = sym.size
        sym.dims = list(dims)
        sym.array_size = sym.dims[0]
        sym.size = self.sizeof(sym.type) * _product(sym.dims)
        scope.offset += sym.size - old

    def lookup(self, name, line=None):
        """Search from the innermost scope outwards. Returns Symbol or None."""
        self.metrics["lookups"] += 1
        probes_total = 0
        for hops, scope in enumerate(reversed(self.stack)):
            sym, idx, probes = scope.table.find(name)
            probes_total += probes
            if sym is not None:
                self.metrics["lookup_hits"] += 1
                where = "current scope" if hops == 0 else f"{hops} scope(s) up"
                self._log(
                    "lookup", name, True,
                    f"found in '{scope.name}' ({where}), bucket {idx}, {probes_total} probe(s)",
                    line,
                )
                return sym
        self.metrics["lookup_misses"] += 1
        self._log(
            "lookup", name, False,
            f"not found in any of the {len(self.stack)} active scope(s)", line,
        )
        return None

    def delete(self, name, line=None):
        """Remove a name from the current scope only."""
        scope = self.current
        removed = scope.table.delete(name)
        if removed:
            self.metrics["deletes"] += 1
            self._log("delete", name, True, f"removed from scope '{scope.name}'", line)
        else:
            self._log("delete", name, False, f"not declared in scope '{scope.name}'", line)
        return removed

    # Silent helpers (not logged, used for diagnostics only)
    def resolve(self, name):
        """Like ``lookup`` but silent: no log entry and no metrics (used to classify names)."""
        for scope in reversed(self.stack):
            sym = scope.table.lookup(name)
            if sym is not None:
                return sym
        return None

    def find_outer(self, name):
        for scope in reversed(self.stack[:-1]):
            sym = scope.table.lookup(name)
            if sym is not None:
                return sym
        return None

    def lookup_global(self, name):
        return self.stack[0].table.lookup(name)

    def peek_current(self, name):
        return self.current.table.lookup(name)

    # ------------------------------------------------------------ serialisation
    def to_dict(self):
        return {
            "scopes": [scope.to_dict() for scope in self.scopes],
            "active_scopes": [scope.name for scope in self.stack],
            "metrics": dict(self.metrics),
            "log": self.log,
            "log_truncated": self.log_truncated,
        }
