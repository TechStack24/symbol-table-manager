<<<<<<< HEAD
# SymTab - Symbol Table Management System

A **Compiler Design** mini project: a web application (Python + Flask) that shows how a compiler
builds and manages its **symbol table**. You type a C program, and the app tokenizes it,
parses it, and records every declaration in a scoped, hash-based symbol table while checking the
program for semantic errors.

## Features

- **Scoped symbol table** - one hash table per scope, kept on a stack (global, function, block, `for`).
- **Hash table with separate chaining** - djb2 hash, automatic resize at load factor 0.75, bucket view.
- **Core operations** - `insert`, `lookup` (inner scope to outer scope), `delete`, `enter_scope`, `exit_scope`.
- **Attributes stored per symbol** - name, kind, type, scope and level, declaration line, size in bytes,
  frame offset, initialized flag, used flag, reference lines, function signature.
- **Semantic checks driven by the table** - undeclared names, redeclaration, shadowing notes, argument count
  and type checks, return type checks, float-to-int narrowing, use before initialization, unused variables,
  array bounds on constant indexes, division by constant zero, `break` outside loops, conflicting prototypes.
- **Syntax error recovery** - panic-mode recovery, so one mistake does not hide the rest of the report.
- **Web UI** - editor with line numbers and error markers, symbol tables per scope, hash bucket view,
  token list, step-by-step operation log, CSV export, and an interactive command playground.

## Project structure

```
symbol-table-manager/
├── main.py                     # Console version with user input (no Flask needed)
├── app.py                      # Flask app: routes and JSON API
├── standalone/
│   └── symbol_table_manager.py # Everything in ONE file, with user input
├── requirements.txt            # Flask
├── README.md
├── compiler/                   # The compiler front end (pure Python, no Flask)
│   ├── __init__.py
│   ├── tokens.py               # Token class, keywords, operator sets
│   ├── typeinfo.py             # C type helpers (pointers, sizes, specifier combining)
│   ├── lexer.py                # Phase 1 - lexical analysis + #define / #include preprocessor
│   ├── symbol_table.py         # HashTable, Symbol, Scope, SymbolTable (the core)
│   ├── parser.py               # Phase 2/3 - recursive-descent parser + semantic checks
│   ├── analyzer.py             # Runs the phases and builds the JSON report
│   └── playground.py           # Text-command interpreter for the playground tab
├── templates/
│   └── index.html              # Single-page UI (Analyzer / Playground / Theory)
├── static/
│   ├── css/style.css           # Light and dark theme
│   └── js/app.js               # UI logic (fetch API, tables, editor gutter)
├── samples/                    # Example programs shown in the UI
│   ├── 01_scopes_and_shadowing.c
│   ├── 02_functions_and_recursion.c
│   ├── 03_semantic_errors.c
│   ├── 04_arrays_and_loops.c
│   └── 05_pointers_structs_switch.c
├── tools/
│   └── build_standalone.py     # Regenerates the single-file version from compiler/*.py
└── tests/                      # unittest suite (133 tests)
    ├── test_lexer.py
    ├── test_symbol_table.py
    ├── test_analyzer.py
    ├── test_playground.py
    └── test_app.py
```

## Console version (user input, no Flask)

```bash
python main.py              # menu: type/paste a program, load a file, playground, samples
python main.py prog.c       # analyze a file
python standalone/symbol_table_manager.py   # same thing as a single file
```

In option 1 type your program and finish with a line containing only `END`.

## Setup and run (web version)

Requires Python 3.9 or newer.

```bash
cd symbol-table-manager
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000>. Optional environment variables: `PORT` (default 5000; on macOS port 5000
can be taken by AirPlay, so try `PORT=5001 python app.py`), `HOST`, and `FLASK_DEBUG=1` for auto-reload.

Run the tests:

```bash
python -m unittest discover -v        # 133 tests
python tools/build_standalone.py        # only if you edit compiler/*.py (refreshes the single file)
```

## How to use the app

**Analyzer tab** - choose a sample or write your own program, then press **Analyze** (or Ctrl+Enter).
Use the result tabs: *Symbol table* (one card per scope, indented by nesting level), *Diagnostics*
(click one to jump to its line), *Hash buckets*, *Tokens*, and *Operations log*.

**Playground tab** - drive the table by hand with commands. Press **Load demo** to see shadowing and
scope exit in action.

| Command | Meaning |
|---|---|
| `insert <name> <type> [kind] [size]` | add to the current scope; kind is `variable`, `array`, `function` or `parameter` (arrays need a size) |
| `lookup <name>` | search the current scope, then outward to global |
| `delete <name>` | remove from the current scope |
| `enter [name]` | open a nested scope |
| `exit` | close the innermost scope |

## Login and accounts

The whole app (analyzer, playground, samples and the API) sits behind a login.

* Open the site, click **Create an account**, then log in. Users are stored in `instance/users.db`
  (SQLite, created automatically on first run). If the `DATABASE_URL` environment variable is set, users
  are stored in that Postgres database instead.
* Passwords are stored only as salted hashes (werkzeug), never as plain text.
* Sessions use a signed cookie (7 days). The secret key is read from the `SECRET_KEY` environment variable,
  or generated once and saved in `instance/secret_key`.
* Forms have CSRF tokens, logout is a POST, five wrong passwords in five minutes lock that username/IP out
  for a while, and the "next page" redirect after login cannot leave the site.
* `/api/health` stays public; every other `/api/*` call returns `401` when you are not logged in.

## Deploying on Vercel

Vercel runs the app as short-lived serverless functions with **no persistent disk**, so the local SQLite file
cannot hold accounts. Use a hosted Postgres database (free tiers: Neon, Supabase, Vercel Postgres):

1. In the Vercel dashboard open your project -> **Storage** (or the Marketplace) -> create a **Neon / Postgres**
   database and connect it to the project. This adds the `DATABASE_URL` / `POSTGRES_URL` environment variable.
   (Or copy the connection string from Neon/Supabase and add it yourself as `DATABASE_URL`.)
2. Project -> **Settings -> Environment Variables**: add `SECRET_KEY` with a long random value, e.g.
   `python -c "import secrets; print(secrets.token_hex(32))"`.
3. **Redeploy** (environment variables only apply to new deployments).

The `users` table is created automatically on the first request. Until `DATABASE_URL` is set, the deployed site
shows a short "Setup needed" message instead of failing silently. Accounts made before this setup lived in a
temporary file and are gone, so register again.

Run the tests against Postgres too with
`TEST_DATABASE_URL=postgresql://user:pass@host/db python -m unittest discover -v`.

## Supported C language

This is a C **subset** front end: it handles the constructs below completely and reports a single clear
error for anything outside them. It does not generate code.

| Area | Supported |
|---|---|
| Types | `char short int long long long`, `signed/unsigned`, `float double long double`, `void`; `const static extern register volatile` are accepted and ignored |
| Pointers | any depth (`int **p`), `&x`, `*p`, pointer arithmetic, `NULL`, casts `(int *)p`, `sizeof` |
| Arrays | 1-D and multi-dimensional, `{...}` and string initializers, size inferred from `[]`, constant-index bounds check, variable-length arrays noted |
| User types | `struct` (members get their own scope, `.` and `->`, self-reference), `enum` (constants usable in `case` and array sizes), `typedef` |
| Statements | blocks, `if/else`, `while`, `do-while`, `for` (own scope), `switch/case/default`, `return`, `break`, `continue` |
| Expressions | assignment and compound assignment, `?:`, `\|\|` `&&`, `\| ^ &`, comparisons, `<< >>`, `+ - * / %`, `! ~ - + ++ --`, comma operator, calls |
| Literals | decimal/hex/octal, `10L 5U 1.5f`, char escapes (`'\n' '\x41' '\101'`), adjacent string concatenation |
| Preprocessor | `#define` object-like and function-like macros (expanded and stored in the table as kind `macro`); `#include` noted; `#ifdef/#if/#pragma` ignored |
| Library | `printf scanf sprintf puts getchar strlen strcpy strcmp strcat malloc calloc realloc free exit atoi rand abs sqrt pow sin cos floor ceil fopen fclose fprintf fscanf fgets toupper isdigit ...`, `size_t FILE int32_t ...`, `NULL EOF INT_MAX` |

Not supported (reported once, clearly): `union`, `goto`/labels, function pointers, bit-fields, compound
literals, `#if` evaluation (all branches are analysed), `#` / `##` in macros, unknown headers.
Struct sizes are packed (no alignment padding) to keep the size column easy to explain.

New symbol kinds shown in the table: `macro`, `typedef`, `struct`, `enum`, `constant`, `member`.

## How it works

```
source text ──► Lexer ──► tokens ──► Parser (recursive descent) ──► diagnostics + symbol table
                                           │  ▲
                              insert / lookup / enter_scope / exit_scope
                                           ▼  │
                                      SymbolTable (stack of Scope → HashTable)
```

1. **Lexer** (`lexer.py`) scans characters into tokens with line and column, skipping comments and reporting
   bad characters or unterminated strings.
2. **Parser** (`parser.py`) is single-pass and syntax-directed: while it recognises the grammar it calls the
   symbol table. Entering a function, block or `for` pushes a scope; a declaration inserts a `Symbol`; every
   identifier use performs a `lookup`; leaving a scope pops it and reports unused variables.
3. **SymbolTable** (`symbol_table.py`) holds a stack of scopes. Each scope's `HashTable` computes
   `djb2(name) mod capacity`, appends to the bucket chain, and doubles when the load factor exceeds 0.75.
4. **Analyzer** (`analyzer.py`) merges lexical, syntax and semantic diagnostics and returns one JSON report.

Design notes:

- A variable is inserted *before* its initializer is parsed, as in C, so `int x = x;` reads the new `x`.
- Popped scopes are **kept** so the UI can display the full history. A real compiler would discard them.
- Offsets: the global scope starts at 0; a function scope starts at 0; a nested block continues from its
  parent's current offset (a simplified stack-frame layout). Sizes: `char` 1, `short` 2, `int`/`float` 4, `long`/`double`/pointer 8.
- Uninitialized-use detection is textual (flow-insensitive): a read before any assignment in source order
  triggers the warning, so it can miss cases that a real data-flow analysis would catch.
- Error recovery uses panic mode: after a syntax error the parser skips to a `;`, a `}` or the start of the
  next statement (or declaration at top level) and continues.

## JSON API

| Method and path | Body | Response |
|---|---|---|
| `GET /` | | the web page |
| `GET /api/samples` | | `[{id, title, code}]` |
| `POST /api/analyze` | `{"source": "..."}` (max 20,000 chars) | summary, diagnostics, tokens, scopes (symbols, buckets, stats), metrics, operation log |
| `POST /api/playground` | `{"commands": ["insert x int", ...]}` (max 200) | per-command results plus the resulting scopes and log |
| `GET /api/health` | | `{"status": "ok"}` |

Invalid input returns `{"error": "..."}` with status 400 (413 for oversized bodies).

## Sample output

For `03_semantic_errors.c` the analyzer reports, among others:

```
3:5   error    redeclaration of 'counter' (previously declared at line 2)
18:7  warning  implicit conversion from float to int in assignment may lose precision
19:13 warning  variable 'total' is used before it is initialized
20:5  error    use of undeclared identifier 'b'
21:9  error    function 'square' expects 1 argument(s) but 2 given
24:5  error    'break' statement is not inside a loop
25:5  error    void value used in return statement
```

## Viva questions

1. **What is a symbol table and which phases use it?** A per-compilation database of identifiers and their
   attributes; used by semantic analysis, intermediate code generation and code generation, and for error reporting.
2. **Why a hash table?** O(1) average insert and lookup. A linear list is O(n); a BST is O(log n).
3. **How are scopes handled?** A stack of tables. Lookup walks from the innermost table outward, so inner
   declarations shadow outer ones; exiting a block pops its table.
4. **How are collisions resolved here?** Separate chaining: each bucket holds a list of symbols, and the table
   doubles and rehashes when the load factor passes 0.75.
5. **What is the difference between redeclaration and shadowing?** Redeclaring a name in the *same* scope is
   an error; declaring it in a *nested* scope is legal and hides the outer one.
6. **Why is a function inserted before its body is parsed?** So recursive calls can find it.
7. **What does the offset column mean?** The byte position of the variable inside its frame, computed from
   the sizes of the variables declared before it.

## Ideas for extension

- Persist analyses in SQLite and compare runs.
- Add unions, function pointers and `#if` evaluation.
- Generate three-address code using the offsets stored in the table.
- Replace the textual initialization check with data-flow analysis.
=======
# symbol-table-manager
A Simple Symbol Table Manager for Compiler Design
>>>>>>> e7a070b27539ca60a7288dd07ccf3ff56fd447aa
