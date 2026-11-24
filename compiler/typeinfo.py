"""Helpers for C type names used by the symbol table and the parser.

A type is a plain string: ``"int"``, ``"unsigned long"``, ``"struct Point"``,
``"enum Color"`` or any of those followed by ``*`` for pointers (``"char *"``).
"""

INTEGER_TYPES = frozenset({
    "char", "unsigned char", "short", "unsigned short", "int", "unsigned int",
    "long", "unsigned long", "long long", "unsigned long long",
})
FLOAT_TYPES = frozenset({"float", "double", "long double"})

TYPE_SIZES = {
    "void": 0, "char": 1, "unsigned char": 1, "short": 2, "unsigned short": 2,
    "int": 4, "unsigned int": 4, "long": 8, "unsigned long": 8,
    "long long": 8, "unsigned long long": 8,
    "float": 4, "double": 8, "long double": 16,
}
POINTER_SIZE = 8


def is_pointer(t):
    return isinstance(t, str) and t.endswith("*")


def pointee(t):
    """``"int *"`` -> ``"int"``, ``"char **"`` -> ``"char *"``."""
    return t[:-1].rstrip()


def pointer_to(t, levels=1):
    for _ in range(levels):
        t = t + "*" if t.endswith("*") else t + " *"
    return t


def is_struct(t):
    return isinstance(t, str) and t.startswith("struct ") and not is_pointer(t)


def is_enum(t):
    return isinstance(t, str) and t.startswith("enum ") and not is_pointer(t)


def is_integral(t):
    return t in INTEGER_TYPES or is_enum(t)


def is_float(t):
    return t in FLOAT_TYPES


def is_numeric(t):
    return is_integral(t) or is_float(t)


def base_size(t):
    """Size in bytes of a non-struct type (pointers 8, enums 4, unknown 0)."""
    if is_pointer(t):
        return POINTER_SIZE
    if is_enum(t):
        return 4
    return TYPE_SIZES.get(t, 0)


def arithmetic_result(a, b):
    """Type of ``a <op> b`` for two numeric types (usual arithmetic conversions, simplified)."""
    if is_float(a) or is_float(b):
        if "long double" in (a, b):
            return "long double"
        if "double" in (a, b):
            return "double"
        return "float"
    wide = max(base_size(a), base_size(b))
    unsigned = any(t.startswith("unsigned") for t in (a, b))
    if wide >= 8:
        return "unsigned long" if unsigned else "long"
    return "unsigned int" if unsigned and wide >= 4 else "int"


def canonical_type(words):
    """Combine specifier keywords (``unsigned``, ``long``, ``int`` ...) into one type name.

    Returns ``(type, error_message_or_None)``.
    """
    count = {}
    for w in words:
        count[w] = count.get(w, 0) + 1
    bases = [b for b in ("void", "char", "float", "double", "int") if b in count]
    longs, shorts = count.get("long", 0), count.get("short", 0)
    unsigned, signed = count.get("unsigned", 0), count.get("signed", 0)

    problem = None
    if len(bases) > 1 or any(v > 1 for k, v in count.items() if k not in ("long",)) or longs > 2:
        problem = "invalid combination of type specifiers"
    elif unsigned and signed:
        problem = "'signed' and 'unsigned' cannot be combined"
    elif longs and shorts:
        problem = "'long' and 'short' cannot be combined"
    elif bases and bases[0] in ("void", "float") and (longs or shorts or unsigned or signed):
        problem = f"'{bases[0]}' cannot be combined with other type specifiers"
    elif bases and bases[0] == "double" and (shorts or unsigned or signed or longs > 1):
        problem = "invalid combination of type specifiers"
    elif bases and bases[0] == "char" and (longs or shorts):
        problem = "invalid combination of type specifiers"
    if problem:
        return "int", problem

    if bases and bases[0] == "double":
        return ("long double" if longs else "double"), None
    if bases and bases[0] in ("void", "float"):
        return bases[0], None
    sign = "unsigned " if unsigned else ""
    if bases and bases[0] == "char":
        return sign + "char", None
    if shorts:
        core = "short"
    elif longs == 1:
        core = "long"
    elif longs == 2:
        core = "long long"
    else:
        core = "int"
    return sign + core, None


def int_value(text):
    """Value of an integer-literal lexeme (decimal, hex, octal, with u/l suffixes) or None."""
    if text is None:
        return None
    body = text.rstrip("uUlL")
    try:
        if body[:2] in ("0x", "0X"):
            return int(body, 16)
        if len(body) > 1 and body[0] == "0" and body.isdigit():
            return int(body, 8)
        return int(body, 10)
    except ValueError:
        return None
