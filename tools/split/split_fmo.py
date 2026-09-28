#!/usr/bin/env python3
"""Split one flat service module into a package, one module per concern.

A generalisation of release/split_core.py (which cut OpenLobby's
responders.py into services/core/), first used on CrystalFront's
services/fmo.py. It is deterministic: the same source and the same map give
the same package, so the split can be regenerated after a catch-up pass
merges new work into the flat file.

    python split_fmo.py --src <repo>/services/fmo.py --map split_fmo_map.txt \
        --package fmoserver --out <repo>/services/fmoserver \
        --facade <repo>/services/fmo.py
    python split_fmo.py --src ... --map ... --package ... --check   # report only
    python split_fmo.py --src ... --ranges ranges.txt               # map from line ranges

How it works
- Every top-level statement of the flat file is assigned to one module of the
  package by the map: defs and module globals by NAME; an unnamed statement
  (an `if`, a `for`, `X[k] = v`, `X.update(...)`) by a `~prefix` of its first
  line, else by the names it binds or mutates, else it stays with the
  statement above it. The comment block above a statement travels with it, so
  the section banners and the per-function commentary survive.
- Inside a moved statement, every reference to a top-level name that now lives
  in ANOTHER module is rewritten to `<module>.<name>`. Scope analysis follows
  Python's rules (function scopes nest, class bodies do not), so a local that
  shadows a module name is left alone. A `global X` for a name another module
  owns is dropped and every use of X in that function becomes `<module>.X`,
  which rebinds the owner's global exactly as the old statement did.
- `globals()` in moved code becomes `flat_globals()` (from deps): a mapping over every
  top-level name of the old flat module, read and written through the facade,
  so a test that patches `globals()["KNOB"]` still reaches the module that
  reads KNOB.
- The package sits one directory below the flat file, so
  `os.path.dirname(os.path.abspath(__file__))` becomes the parent of that.
- Import-like statements (plain imports, and `try: import x / except: x = None`
  blocks) go to <package>/deps.py; a module that uses such a name gets the plain
  import, or `from .deps import x` for the optional ones.
- A module imports at its TOP only the modules it needs while it is being
  imported (a constant computed from another module's constant, a default
  argument, a function it calls at import); the modules it only calls at run
  time are imported at its END. The package __init__ imports every module in
  one fixed order, and since importing any submodule runs the __init__ first,
  that is the load order whatever a caller imports. The tool derives the
  order and simulates the whole import with it: no module may read another
  whose body has not run yet.
- The flat file becomes a facade: `import fmo` keeps working for every tool and
  test, reads AND writes (`fmo.KNOB = x`) are forwarded to the owning module,
  and `python fmo.py ...` still runs the old `if __name__ == "__main__":` block.

Map directives, beside `[module] docstring` headers and name lines:
    ~<prefix>          an unnamed statement whose first line starts so
    !first <module>    imported by the package __init__ before anything else
                       (a module whose import has side effects others rely on)
    !sub <old> => <new>
                       a literal substitution applied to the flat source before
                       it is parsed; each must match exactly once
    !byname <module> <name> ...
                       names other modules import by name (`from .wirelog
                       import log`) instead of qualifying every use; the
                       facade still forwards a patch to every copy
    Class.method       (a name token with a dot) moves that method of a
                       top-level class into a mixin class in this module
    %Class Mixin: doc  names that mixin; the class then inherits from it
"""
import argparse
import ast
import collections
import io
import os
import re
import sys
import textwrap
import tokenize

FILE_DIR = "os.path.dirname(os.path.abspath(__file__))"
PARENT_DIR = "os.path.dirname(os.path.dirname(os.path.abspath(__file__)))"
COMPOUND = (ast.If, ast.For, ast.While, ast.Try, ast.With)


# --------------------------------------------------------------------------- #
# Source model
# --------------------------------------------------------------------------- #
class Stmt:
    __slots__ = ("node", "names", "bases", "kind", "first", "last", "span_first",
                 "text", "module", "drop", "header", "prefix")

    def __init__(self, node, names, bases, kind, first, last):
        self.node = node
        self.names = names        # top-level names this statement binds
        self.bases = bases        # top-level names it mutates (X[k] = v, X.update())
        self.kind = kind          # def | assign | import | optimport | other
        self.first = first        # first line of the statement proper
        self.last = last
        self.span_first = None    # first line including the comment block above
        self.text = None
        self.module = None
        self.drop = ()            # lines left out (the methods a class split moved away)
        self.header = None        # (line, text) replacing a class header
        self.prefix = ""          # text written before the statement (a mixin's class line)


def bound_targets(target):
    """Names a binding target stores to (not the ones it only reads, such as
    the key of `X[KEY] = v`)."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        out = []
        for e in target.elts:
            out += bound_targets(e)
        return out
    if isinstance(target, ast.Starred):
        return bound_targets(target.value)
    return []


def target_base(target):
    """The top-level name `X[k].a = v` mutates, or None."""
    while isinstance(target, (ast.Subscript, ast.Attribute)):
        target = target.value
    return target.id if isinstance(target, ast.Name) else None


def compound_bindings(node):
    """Names a compound statement at module level binds at module level (its
    loop variables, the knobs an `if` block sets, the defs inside it)."""
    names = []

    def walk(n):
        for c in ast.iter_child_nodes(n):
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(c.name)
                continue
            if isinstance(c, (ast.Lambda, ast.ListComp, ast.SetComp, ast.GeneratorExp,
                              ast.DictComp)):
                continue
            if isinstance(c, ast.Name) and isinstance(c.ctx, (ast.Store, ast.Del)):
                names.append(c.id)
            elif isinstance(c, ast.ExceptHandler) and c.name:
                names.append(c.name)
            elif isinstance(c, (ast.Import, ast.ImportFrom)):
                names.extend((a.asname or a.name).split(".")[0] for a in c.names)
            walk(c)
    walk(node)
    seen = []
    for n in names:
        if n not in seen:
            seen.append(n)
    return seen


def classify(node):
    """(names, bases, kind) for a top-level statement."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name], [], "def"
    if isinstance(node, ast.Assign):
        names, bases = [], []
        for t in node.targets:
            names += bound_targets(t)
            b = None if bound_targets(t) else target_base(t)
            if b:
                bases.append(b)
        return names, bases, ("assign" if names else "other")
    if isinstance(node, ast.AnnAssign):
        names = bound_targets(node.target)
        b = None if names else target_base(node.target)
        return names, [b] if b else [], ("assign" if names else "other")
    if isinstance(node, ast.AugAssign):
        b = target_base(node.target)
        return [], [b] if b else [], "other"
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return [(a.asname or a.name).split(".")[0] for a in node.names], [], "import"
    if isinstance(node, ast.Try):
        body_imports = all(isinstance(b, (ast.Import, ast.ImportFrom)) for b in node.body)
        if body_imports and node.handlers:
            names = []
            for b in node.body:
                names += [(a.asname or a.name).split(".")[0] for a in b.names]
            return names, [], "optimport"
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        b = target_base(node.value.func)
        return [], [b] if b else [], "other"
    if isinstance(node, ast.Delete):
        return [], [], "other"
    if isinstance(node, COMPOUND):
        return compound_bindings(node), [], "other"
    return [], [], "other"


def load_source(text):
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    stmts = []
    for node in tree.body:
        names, bases, kind = classify(node)
        first = node.lineno
        # decorators sit above the def line
        for d in getattr(node, "decorator_list", []):
            first = min(first, d.lineno)
        stmts.append(Stmt(node, names, bases, kind, first, node.end_lineno))
    prev_end = 0
    for s in stmts:
        s.span_first = prev_end + 1
        s.text = "".join(lines[s.span_first - 1:s.last])
        prev_end = s.last
    trailing = "".join(lines[prev_end:])
    return lines, tree, stmts, trailing


# --------------------------------------------------------------------------- #
# The map
# --------------------------------------------------------------------------- #
def read_map(path):
    """({module: {"doc", "names", "prefixes", "mixins", "methods"}} in file order,
    [first], [(old, new)], {name: module imported by name})."""
    mods = collections.OrderedDict()
    first, subs, byname = [], [], {}
    cur = None
    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("!first "):
            first += line.split()[1:]
            continue
        if line.startswith("!byname "):
            _kw, home, *names = line.split()
            for n in names:
                byname[n] = home
            continue
        if line.startswith("!sub "):
            old, sep, new = line[5:].partition(" => ")
            if not sep:
                raise SystemExit(f"{path}: !sub needs ' => ': {line!r}")
            subs.append((old, new))
            continue
        m = re.match(r"^\[([A-Za-z_][A-Za-z0-9_]*)\]\s*(.*)$", line)
        if m:
            cur = m.group(1)
            if cur in mods:
                raise SystemExit(f"{path}: module [{cur}] listed twice")
            mods[cur] = {"doc": m.group(2).strip(), "names": set(), "prefixes": [],
                         "mixins": {}, "methods": set()}
            continue
        if cur is None:
            raise SystemExit(f"{path}: entry before any [module] header: {line!r}")
        if line.startswith("~"):
            mods[cur]["prefixes"].append(line[1:].rstrip())
        elif line.startswith("%"):
            m = re.match(r"^%([A-Za-z_]\w*)\s+([A-Za-z_]\w*):\s*(.*)$", line)
            if not m:
                raise SystemExit(f"{path}: want '%<Class> <Mixin>: docstring': {line!r}")
            mods[cur]["mixins"][m.group(1)] = (m.group(2), m.group(3).strip())
        else:
            for tok in line.split():
                if "." in tok:
                    mods[cur]["methods"].add(tuple(tok.split(".", 1)))
                else:
                    mods[cur]["names"].add(tok)
    return mods, first, subs, byname


def apply_subs(text, subs):
    for old, new in subs:
        n = text.count(old)
        if n != 1:
            raise SystemExit(f"!sub {old!r}: matches {n} times in the source, not once")
        text = text.replace(old, new)
    return text


def first_line_of(s):
    return "".join(s.text.splitlines(keepends=True)[s.first - s.span_first:
                                                     s.first - s.span_first + 1]).rstrip()


def assign_modules(stmts, mods, facade_prefixes):
    owner = {}
    for mod, spec in mods.items():
        for n in spec["names"]:
            if n in owner:
                raise SystemExit(f"map: {n} listed in both {owner[n]} and {mod}")
            owner[n] = mod
    unmapped, followed = [], []
    prev = None
    for s in stmts:
        first_line = first_line_of(s)
        if s.kind in ("import", "optimport"):
            s.module = "deps"
            continue
        if any(first_line.startswith(p) for p in facade_prefixes):
            s.module = "__facade__"
            continue
        if (s.kind == "other" and isinstance(s.node, ast.Expr)
                and isinstance(getattr(s.node, "value", None), ast.Constant) and s.first <= 2):
            s.module = "__facade__"      # the module docstring
            continue
        hit = None
        for mod, spec in mods.items():
            if any(first_line.startswith(p) for p in spec["prefixes"]):
                hit = mod
                break
        if hit is None and (s.names or s.bases):
            owners = {owner.get(n) for n in s.names + s.bases}
            owners.discard(None)
            if len(owners) > 1:
                raise SystemExit(f"L{s.first}: names {s.names + s.bases} map to several "
                                 f"modules {owners}; add a ~prefix line")
            if owners:
                hit = owners.pop()
        if hit is None and s.kind == "other" and prev is not None:
            # a knob-parsing loop, an `if` that corrects the knob above it:
            # it stays with the statement right above it
            hit = prev
            followed.append(s)
        if hit is None:
            unmapped.append(s)
            continue
        s.module = hit
        prev = hit
    # names bound only by unnamed statements belong to the module those sit in;
    # a temporary bound in several modules (`for _q in ...`) belongs to none
    binders = collections.defaultdict(set)
    for s in stmts:
        if s.module and s.module not in ("__facade__", "deps") and s.kind == "other":
            for n in s.names:
                binders[n].add(s.module)
    shared_temps = set()
    for n, where in binders.items():
        if n in owner:
            if where - {owner[n]}:
                raise SystemExit(f"{n} is owned by {owner[n]} but also bound in {sorted(where)}")
            continue
        if len(where) == 1:
            owner[n] = next(iter(where))
        else:
            shared_temps.add(n)
    return owner, unmapped, followed, shared_temps


# --------------------------------------------------------------------------- #
# Scope analysis
# --------------------------------------------------------------------------- #
def declared_global(scope_node):
    """Names a function declares `global` directly in its own body."""
    out = set()

    def walk(n):
        for c in ast.iter_child_nodes(n):
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(c, ast.Global):
                out.update(c.names)
            walk(c)
    if isinstance(scope_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        walk(scope_node)
    return out


def local_bindings(scope_node):
    """Names bound directly in this function/lambda/comprehension scope. A name
    the function declares `global` is not local."""
    bound = set()
    args = getattr(scope_node, "args", None)
    if args is not None:
        for a in args.posonlyargs + args.args + args.kwonlyargs:
            bound.add(a.arg)
        if args.vararg:
            bound.add(args.vararg.arg)
        if args.kwarg:
            bound.add(args.kwarg.arg)
    if isinstance(scope_node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        for g in scope_node.generators:
            bound |= set(bound_targets(g.target))

    def walk(n):
        for c in ast.iter_child_nodes(n):
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(c.name)
                for d in c.decorator_list:
                    walk(d)
                if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for d in c.args.defaults + c.args.kw_defaults:
                        if d is not None:
                            walk(d)
                continue
            if isinstance(c, ast.Lambda):
                for d in c.args.defaults + c.args.kw_defaults:
                    if d is not None:
                        walk(d)
                continue
            if isinstance(c, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                # the first iterable is evaluated in the enclosing scope
                walk(c.generators[0].iter)
                continue
            if isinstance(c, ast.Name) and isinstance(c.ctx, (ast.Store, ast.Del)):
                bound.add(c.id)
            elif isinstance(c, ast.ExceptHandler) and c.name:
                bound.add(c.name)
            elif isinstance(c, (ast.Import, ast.ImportFrom)):
                for a in c.names:
                    bound.add((a.asname or a.name).split(".")[0])
            elif isinstance(c, ast.NamedExpr):
                bound.add(c.target.id)
            elif isinstance(c, ast.MatchAs) and c.name:
                bound.add(c.name)
            elif isinstance(c, ast.MatchStar) and c.name:
                bound.add(c.name)
            elif isinstance(c, ast.MatchMapping) and c.rest:
                bound.add(c.rest)
            walk(c)

    if isinstance(scope_node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        for g in scope_node.generators:
            for cond in g.ifs:
                walk(cond)
            if g is not scope_node.generators[0]:
                walk(g.iter)
        if isinstance(scope_node, ast.DictComp):
            walk(scope_node.key)
            walk(scope_node.value)
        else:
            walk(scope_node.elt)
    else:
        walk(scope_node)
    return bound - declared_global(scope_node)


def global_name_refs(stmt_node):
    """Yield every Name node in the statement that resolves to MODULE scope."""
    out = []

    def visit(n, fn_scopes, in_class):
        # fn_scopes: list of sets of names bound in enclosing FUNCTION scopes
        for c in ast.iter_child_nodes(n):
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for d in getattr(c, "decorator_list", []):
                    visit_expr(d, fn_scopes)
                for d in c.args.defaults + c.args.kw_defaults:
                    if d is not None:
                        visit_expr(d, fn_scopes)
                for a in c.args.posonlyargs + c.args.args + c.args.kwonlyargs:
                    if a.annotation is not None:
                        visit_expr(a.annotation, fn_scopes)
                if getattr(c, "returns", None) is not None:
                    visit_expr(c.returns, fn_scopes)
                inner = local_bindings(c)
                # a `global X` in this function makes X module scope here, even
                # when an enclosing function has a local X
                glob = declared_global(c)
                scopes = [sc - glob for sc in fn_scopes] + [inner]
                body = c.body if isinstance(c.body, list) else [c.body]
                for b in body:
                    visit(b, scopes, False)
                    if isinstance(b, ast.Name):
                        resolve(b, scopes)
                continue
            if isinstance(c, ast.ClassDef):
                visit_class(c, fn_scopes)
                continue
            if isinstance(c, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                visit(c.generators[0].iter, fn_scopes, in_class)
                if isinstance(c.generators[0].iter, ast.Name):
                    resolve(c.generators[0].iter, fn_scopes)
                inner = local_bindings(c)
                scopes = fn_scopes + [inner]
                parts = [g.ifs for g in c.generators] + [[g.iter] for g in c.generators[1:]]
                elts = [c.key, c.value] if isinstance(c, ast.DictComp) else [c.elt]
                for group in parts + [elts]:
                    for p in group:
                        visit(p, scopes, False)
                        if isinstance(p, ast.Name):
                            resolve(p, scopes)
                continue
            if isinstance(c, ast.Name):
                resolve(c, fn_scopes)
            visit(c, fn_scopes, in_class)

    def visit_expr(node, fn_scopes):
        if isinstance(node, ast.Name):
            resolve(node, fn_scopes)
        else:
            visit(node, fn_scopes, False)

    def resolve(name_node, fn_scopes):
        for sc in fn_scopes:
            if name_node.id in sc:
                return
        out.append(name_node)

    def visit_class(c, fn_scopes):
        for d in c.decorator_list:
            visit_expr(d, fn_scopes)
        for b in c.bases + [k.value for k in c.keywords]:
            visit_expr(b, fn_scopes)
        # names assigned directly in the class body are class attributes: they
        # shadow module names for the body's own statements, not for methods
        class_bound = set()
        for b in c.body:
            if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                class_bound.add(b.name)
                continue
            for n in ast.walk(b):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    class_bound.add(n.id)
        for b in c.body:
            if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                visit(ast.Module(body=[b], type_ignores=[]), fn_scopes, False)
            else:
                visit(b, fn_scopes + [class_bound], True)
                if isinstance(b, ast.Name):
                    resolve(b, fn_scopes + [class_bound])

    if isinstance(stmt_node, ast.Name):
        out.append(stmt_node)
        return out
    if isinstance(stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        # the statement IS a function: its body runs in its own local scope
        for d in getattr(stmt_node, "decorator_list", []):
            visit_expr(d, [])
        for d in stmt_node.args.defaults + stmt_node.args.kw_defaults:
            if d is not None:
                visit_expr(d, [])
        for a in stmt_node.args.posonlyargs + stmt_node.args.args + stmt_node.args.kwonlyargs:
            if a.annotation is not None:
                visit_expr(a.annotation, [])
        if getattr(stmt_node, "returns", None) is not None:
            visit_expr(stmt_node.returns, [])
        inner = local_bindings(stmt_node)
        for b in stmt_node.body:
            visit(b, [inner], False)
        return out
    if isinstance(stmt_node, ast.ClassDef):
        visit_class(stmt_node, [])
        return out
    visit(stmt_node, [], False)
    return out


# --------------------------------------------------------------------------- #
# Rewriting
# --------------------------------------------------------------------------- #
def charcol(line, col):
    """AST column offsets count UTF-8 bytes; the text is indexed by character."""
    return len(line.encode("utf-8")[:col].decode("utf-8"))


def realign(text_lines, first_lineno, grown):
    """Extra indent for the continuation lines of one statement.

    `grown` is {line: [(col, n)]}: n characters were inserted at `col` of that
    line (a name qualified with its module). A continuation line indented to
    sit one column right of an open bracket (the visual indent this code base
    uses) follows that bracket when the bracket moves. Returns {line: n}."""
    try:
        toks = list(tokenize.generate_tokens(io.StringIO("".join(text_lines)).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return {}
    in_string = set()
    for t in toks:
        if t.type == tokenize.STRING and t.end[0] > t.start[0]:
            in_string.update(range(t.start[0] + 1, t.end[0] + 1))
    shift, stack, seen = {}, [], set()

    def moved_col(rel_line, col):
        line = rel_line + first_lineno - 1
        return col + shift.get(line, 0) + sum(n for c, n in grown.get(line, ()) if c < col)
    skip = (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
            tokenize.ENDMARKER)
    for t in toks:
        rel = t.start[0]
        if rel not in seen and t.type not in skip:
            seen.add(rel)
            if stack and rel not in in_string and stack[-1][1] != rel:
                ocol, oline = stack[-1]
                if t.start[1] == ocol + 1:
                    target = moved_col(oline, ocol) + 1
                    if target > t.start[1]:
                        shift[rel + first_lineno - 1] = target - t.start[1]
        if t.type == tokenize.OP and t.string in "([{":
            stack.append((t.start[1], rel))
        elif t.type == tokenize.OP and t.string in ")]}" and stack:
            stack.pop()
    return shift


def rewrite_statement(stmt, owner, lines, import_names, report,
                      qualify=lambda mod, ident: f"{mod}.{ident}", byname=()):
    """Return the statement text with cross-module names qualified, plus the
    set of modules it references and the import-like names it uses."""
    edits = []          # (lineno, col, end_col, new); new None drops the line
    deps = set()
    used_imports = set()
    for nm in global_name_refs(stmt.node):
        ident = nm.id
        if ident in import_names:
            used_imports.add(ident)
            continue
        mod = owner.get(ident)
        if mod is None or mod == stmt.module:
            continue
        if ident in byname:
            # imported by name (`from .wirelog import log`), not qualified
            used_imports.add(ident)
            continue
        line = lines[nm.lineno - 1]
        c0, c1 = charcol(line, nm.col_offset), charcol(line, nm.end_col_offset)
        if line[c0:c1] != ident:
            report.append(f"L{nm.lineno}:{nm.col_offset} position mismatch for {ident} "
                          f"(f-string?); fix by hand: {line.strip()[:80]}")
            continue
        edits.append((nm.lineno, c0, c1, qualify(mod, ident)))
        deps.add(mod)
    # `global X` for a name another module owns: the uses above are qualified
    # now, so the declaration goes (or keeps only this module's names)
    for g in ast.walk(stmt.node):
        if not isinstance(g, ast.Global):
            continue
        keep = [n for n in g.names if owner.get(n) in (None, stmt.module)]
        if len(keep) == len(g.names):
            continue
        line = lines[g.lineno - 1]
        indent = line[:len(line) - len(line.lstrip())]
        if keep:
            edits.append((g.lineno, 0, len(line.rstrip("\r\n")),
                          indent + "global " + ", ".join(keep)))
        else:
            edits.append((g.lineno, 0, None, None))
    # globals() -> the flat module's namespace, through the facade
    for c in ast.walk(stmt.node):
        if (isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "globals"
                and not c.args and not c.keywords):
            if "globals" in owner:
                report.append(f"L{c.lineno}: globals is a top-level name; cannot rewrite")
                continue
            line = lines[c.func.lineno - 1]
            edits.append((c.func.lineno, charcol(line, c.func.col_offset),
                          charcol(line, c.func.end_col_offset), "flat_globals"))
            used_imports.add("flat_globals")
    # apply edits right-to-left per line
    text_lines = lines[stmt.span_first - 1:stmt.last]
    by_line = collections.defaultdict(list)
    grown = collections.defaultdict(list)
    for ln, c0, c1, new in edits:
        by_line[ln].append((c0, c1, new))
        if new is not None and c1 is not None and len(new) > c1 - c0:
            grown[ln].append((c0, len(new) - (c1 - c0)))
    indent = realign(text_lines, stmt.span_first, grown) if grown else {}
    for ln, n in indent.items():
        by_line[ln].append((0, 0, " " * n))
    for ln, eds in by_line.items():
        idx = ln - stmt.span_first
        if ln in stmt.drop:
            continue
        s = text_lines[idx]
        if any(new is None for _c0, _c1, new in eds):
            text_lines[idx] = None
            continue
        for c0, c1, new in sorted(eds, reverse=True):
            s = s[:c0] + new + s[c1:]
        text_lines[idx] = s
    for ln in stmt.drop:
        text_lines[ln - stmt.span_first] = None
    if stmt.header:
        text_lines[stmt.header[0] - stmt.span_first] = stmt.header[1]
    text = stmt.prefix + "".join(t for t in text_lines if t is not None)
    # the package is one directory below the flat file
    if FILE_DIR in text:
        text = text.replace(FILE_DIR, PARENT_DIR)
    return text, deps, used_imports


def sibling_import(names, tail=""):
    """`from . import a, b` on one line, or wrapped in parentheses at 96 columns."""
    one = "from . import " + ", ".join(names) + tail
    if len(one) <= 96:
        return one + "\n"
    out, line = ["from . import (" + tail], "   "
    for n in names:
        if len(line) + len(n) + 2 > 96:
            out.append(line.rstrip(" "))
            line = "   "
        line += " " + n + ","
    out.append(line)
    out.append(")")
    return "\n".join(out) + "\n"


def module_docstring(doc):
    """A module's docstring, wrapped at 96 columns."""
    if not doc:
        return ""
    return '"""' + "\n".join(textwrap.wrap(doc, 93)) + '"""\n'


def import_lines_for(used, import_stmts, optional_names, byname=None):
    """Emit the import statements a module needs, in the head's order."""
    byname = byname or {}
    plain = []
    optional = sorted(n for n in used if n in optional_names)
    for names, text, is_from in import_stmts:
        want = [n for n in names if n in used and n not in optional_names]
        if not want:
            continue
        if is_from and len(names) > 1:
            mod = re.match(r"\s*from\s+(\S+)\s+import", text).group(1)
            plain.append(f"from {mod} import {', '.join(sorted(want))}\n")
        else:
            plain.append(text.strip() + "\n")
    out = "".join(plain)
    if "flat_globals" in used:
        optional.append("flat_globals")
    if optional:
        out += f"from .deps import {', '.join(optional)}\n"
    homes = collections.defaultdict(list)
    for n in sorted(used):
        if n in byname:
            homes[byname[n]].append(n)
    for home in sorted(homes):
        out += f"from .{home} import {', '.join(homes[home])}\n"
    return out


FLAT_GLOBALS = '''

def flat_globals():
    """What `globals()` meant in the single-file {facade}.py: every top-level
    name of the package, read and written through the facade, so a patch of
    `globals()["NAME"]` lands in the module that owns NAME."""
    import {facade}
    return {facade}.FLAT_GLOBALS
'''

FACADE_TEMPLATE = '''{docstring}
# The code lives in the {package} package, one module per concern
# ({package}/__init__.py lists them). This file is the entry point the
# container runs (`python {facade}.py`) and a compatibility facade:
# `import {facade}` still resolves every top-level name of the old single file,
# reading or writing, to the module that owns it, so tools and tests written
# against the flat file keep working.
{main_imports}
# ONE COPY OF THIS MODULE. The container runs `python {facade}.py`, so the
# running copy is `__main__`, and a later `import {facade}` (the package's own
# deps.flat_globals, a tool) would otherwise load a second copy.
if __name__ == "__main__":
    sys.modules.setdefault("{facade}", sys.modules[__name__])

# Which {package} module owns each top-level name of the old {facade}.py.
_OWNERS = {{
{owners}
}}
_MODULES = {{m: importlib.import_module("{package}." + m) for m in (
{module_names}
)}}


class _Facade(types.ModuleType):
    """`{facade}.<name>` reads and writes go to the owning {package} module."""

    def __getattr__(self, name):
        mod = _OWNERS.get(name)
        if mod is None:
            raise AttributeError(f"module '{facade}' has no attribute {{name!r}}")
        return getattr(_MODULES[mod], name)

    def __setattr__(self, name, value):
        mod = _OWNERS.get(name)
        if mod is None:
            super().__setattr__(name, value)
            return
        setattr(_MODULES[mod], name, value)
        # a name imported by name (fmostore from deps, log from wirelog) is a
        # copy in every module that imported it; a patch has to reach each copy
        for other in _MODULES.values():
            if other is not _MODULES[mod] and name in vars(other):
                setattr(other, name, value)

    def __delattr__(self, name):
        mod = _OWNERS.get(name)
        if mod is None:
            super().__delattr__(name)
            return
        delattr(_MODULES[mod], name)

    def __dir__(self):
        return sorted(set(super().__dir__()) | set(_OWNERS))


class _FlatGlobals(dict):
    """`globals()` of the old single file, as the moved code still uses it."""

    def __init__(self, facade):
        super().__init__()
        self._facade = facade

    def __getitem__(self, name):
        try:
            return getattr(self._facade, name)
        except AttributeError:
            raise KeyError(name) from None

    def __setitem__(self, name, value):
        setattr(self._facade, name, value)

    def __delitem__(self, name):
        try:
            delattr(self._facade, name)
        except AttributeError:
            raise KeyError(name) from None

    def __contains__(self, name):
        return name in _OWNERS or name in vars(self._facade)

    def get(self, name, default=None):
        try:
            return self[name]
        except KeyError:
            return default

    def setdefault(self, name, default=None):
        if name not in self:
            self[name] = default
        return self[name]

    def pop(self, name, *default):
        try:
            value = self[name]
        except KeyError:
            if default:
                return default[0]
            raise
        del self[name]
        return value

    def update(self, *args, **kw):
        for k, v in dict(*args, **kw).items():
            self[k] = v

    def keys(self):
        return list(_OWNERS)

    def __iter__(self):
        return iter(_OWNERS)

    def __len__(self):
        return len(_OWNERS)


sys.modules[__name__].__class__ = _Facade
FLAT_GLOBALS = _FlatGlobals(sys.modules[__name__])
{main_block}'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--map")
    ap.add_argument("--package", default="core")
    ap.add_argument("--title", default="", help="first line of the package docstring")
    ap.add_argument("--out")
    ap.add_argument("--facade")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--ranges", help="derive a map from 'lo hi module' lines and print it")
    args = ap.parse_args()

    text = open(args.src, encoding="utf-8").read()
    mods, first_mods, subs, byname = read_map(args.map) if args.map else ({}, [], [], {})
    text = apply_subs(text, subs)
    lines, tree, stmts, trailing = load_source(text)
    facade_name = os.path.splitext(os.path.basename(args.facade or args.src))[0]

    if args.ranges:
        ranges = []
        for raw in open(args.ranges, encoding="utf-8"):
            raw = raw.split("#")[0].strip()
            if not raw:
                continue
            lo, hi, mod = raw.split()
            ranges.append((int(lo), int(hi), mod))
        per = collections.OrderedDict()
        for lo, hi, mod in ranges:
            per.setdefault(mod, [])
        for s in stmts:
            if s.kind in ("import", "optimport") or not s.names or s.kind == "other":
                continue
            for lo, hi, mod in ranges:
                if lo <= s.first <= hi:
                    per[mod].extend(n for n in s.names if n not in per[mod])
                    break
            else:
                print(f"# UNCOVERED L{s.first}: {s.names}", file=sys.stderr)
        for mod, names in per.items():
            print(f"[{mod}]")
            buf = ""
            for n in names:
                if len(buf) + len(n) + 1 > 96:
                    print(buf.rstrip())
                    buf = ""
                buf += n + " "
            if buf:
                print(buf.rstrip())
            print()
        return

    if "deps" not in mods:
        mods["deps"] = {"doc": "Imports shared by the package modules: the standard "
                               "library and the optional sibling services.",
                        "names": set(), "prefixes": [], "mixins": {}, "methods": set()}
    facade_prefixes = ["if __name__ == \"__main__\":", "if __name__ == '__main__':"]
    owner, unmapped, followed, shared_temps = assign_modules(stmts, mods, facade_prefixes)

    # import-like names and their statements
    import_stmts = []       # (names, text, is_from)
    optional_names = set()
    import_names = set()
    for s in stmts:
        if s.kind == "import":
            import_stmts.append((s.names, "".join(lines[s.first - 1:s.last]),
                                 isinstance(s.node, ast.ImportFrom)))
            import_names |= set(s.names)
        elif s.kind == "optimport":
            optional_names |= set(s.names)
            import_names |= set(s.names)
    # names bound in deps are owned by deps for the facade
    for n in import_names:
        owner.setdefault(n, "deps")

    report = []
    for s in unmapped:
        report.append(f"UNMAPPED L{s.first}-{s.last} {s.kind} {s.names or ''}: "
                      f"{lines[s.first - 1].rstrip()[:90]}")
    clash = sorted(set(mods) & set(owner))
    for c in clash:
        report.append(f"MODULE NAME CLASH: module {c!r} is also a top-level name")
    toplevel = {n for s in stmts if s.module != "__facade__" for n in s.names}
    # a name bound in several modules is one global in the flat file and
    # several after the split: fine for a loop temporary each module rebinds
    # before it reads it, wrong for anything another module reads
    if shared_temps:
        where_bound = collections.defaultdict(set)
        where_read = collections.defaultdict(set)
        for s in stmts:
            if s.module in (None, "__facade__", "deps"):
                continue
            for n in set(s.names) & shared_temps:
                where_bound[n].add(s.module)
            for nm in global_name_refs(s.node):
                if nm.id in shared_temps and isinstance(nm.ctx, ast.Load):
                    where_read[nm.id].add(s.module)
        for n in sorted(shared_temps):
            if where_read[n] - where_bound[n]:
                report.append(f"SHARED name {n} is bound in {sorted(where_bound[n])} and read in "
                              f"{sorted(where_read[n] - where_bound[n])}; give it one module")
    for n in sorted(toplevel - set(owner) - shared_temps):
        report.append(f"UNOWNED name {n}")
    for n, home in sorted(byname.items()):
        if owner.get(n) != home:
            report.append(f"UNOWNED name {n}: !byname says {home}, the map says {owner.get(n)}")

    # Class splits. `Class.method` in a module's section moves that method
    # into a mixin class the module defines (`%Class Mixin: docstring`), and
    # the class inherits from its mixins. The class keeps every method the map
    # does not move, its class attributes and its name; `Class.method` still
    # resolves through the inheritance, and a patch of `Class.method` still
    # wins over the mixin's copy.
    method_home = {}
    for mod, spec in mods.items():
        for cm in spec["methods"]:
            method_home[cm] = mod
    units = []
    extra_top = collections.defaultdict(set)     # module -> {"mixmod.Mixin"}
    for s in stmts:
        if not (s.kind == "def" and isinstance(s.node, ast.ClassDef)
                and any(c == s.node.name for c, _m in method_home)):
            units.append(s)
            continue
        cls = s.node
        names_in_body = [b.name for b in cls.body
                         if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))]
        wanted = {m for c, m in method_home if c == cls.name}
        for m in sorted(wanted - set(names_in_body)):
            report.append(f"MIXIN: {cls.name}.{m} is in the map but not in the class")
        if len(names_in_body) != len(set(names_in_body)):
            report.append(f"MIXIN: {cls.name} defines a method twice; cannot split it")
        if cls.bases or cls.keywords:
            report.append(f"MIXIN: {cls.name} already has bases; not supported")
        kept, drop, moved = [], set(), collections.OrderedDict()
        prev_end = cls.lineno
        for b in cls.body:
            first = min([b.lineno] + [d.lineno for d in getattr(b, "decorator_list", [])])
            span = (prev_end + 1, b.end_lineno)
            prev_end = b.end_lineno
            home = method_home.get((cls.name, getattr(b, "name", None)))
            if home is None or home == s.module:
                kept.append(b)
                continue
            u = Stmt(b, [], [], "def", first, b.end_lineno)
            u.span_first, u.module = span[0], home
            u.text = "".join(lines[span[0] - 1:span[1]])
            moved.setdefault(home, []).append(u)
            drop.update(range(span[0], span[1] + 1))
        bases = []
        for home, us in moved.items():
            mixin = mods[home]["mixins"].get(cls.name)
            if mixin is None:
                report.append(f"MIXIN: [{home}] moves {cls.name} methods but declares no "
                              f"%{cls.name} mixin")
                continue
            name, doc = mixin
            us[0].prefix = f"\n\nclass {name}:\n    \"\"\"{doc}\"\"\"\n"
            owner[name] = home
            bases.append(f"{home}.{name}")
            extra_top[s.module].add(f"{home}.{name}")
        header = lines[cls.lineno - 1]
        if not header.rstrip().endswith(f"class {cls.name}:"):
            report.append(f"MIXIN: {cls.name}'s header is not a plain `class {cls.name}:`")
        new_header = header.replace(f"class {cls.name}:", f"class {cls.name}({', '.join(bases)}):")
        if len(new_header.rstrip("\n")) > 96:
            ind = " " * (len(header) - len(header.lstrip()))
            new_header = (f"{ind}class {cls.name}(\n"
                          + "".join(f"{ind}        {b},\n" for b in bases) + f"{ind}):\n")
        kept_node = ast.ClassDef(name=cls.name, bases=[], keywords=[], body=kept,
                                 decorator_list=cls.decorator_list)
        ast.copy_location(kept_node, cls)
        k = Stmt(kept_node, s.names, [], "def", s.first, s.last)
        k.span_first, k.text, k.module = s.span_first, s.text, s.module
        k.drop, k.header = drop, (cls.lineno, new_header)
        units.append(k)
        for home, us in moved.items():
            units.extend(us)

    # build module bodies
    bodies = collections.OrderedDict((m, []) for m in mods)
    mod_deps = collections.defaultdict(set)
    mod_imports = collections.defaultdict(set)
    facade_head = []
    stats = collections.Counter()
    for s in units:
        if s.module is None:
            continue
        if s.module == "__facade__":
            facade_head.append(s)
            continue
        if s.kind in ("import", "optimport"):
            if s.kind == "optimport":
                bodies["deps"].append("".join(lines[s.span_first - 1:s.last]))
            continue
        new_text, deps, used = rewrite_statement(s, owner, lines, import_names, report,
                                                 byname=byname)
        bodies[s.module].append(new_text)
        mod_deps[s.module] |= deps
        mod_imports[s.module] |= used
        stats[s.module] += 1
    # the optional imports reference plain modules (os, sys) in their except arms
    for s in stmts:
        if s.kind == "optimport":
            for nm in global_name_refs(s.node):
                if nm.id in import_names and nm.id not in optional_names:
                    mod_imports["deps"].add(nm.id)

    for m, bases in extra_top.items():
        mod_deps[m] |= {b.split(".")[0] for b in bases}

    # module-name collisions with local variables: a function in module A that
    # binds a local named like module B while A references B.<x>
    for s in units:
        if s.module in (None, "__facade__", "deps") or s.kind != "def":
            continue
        for fn in ast.walk(s.node):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                loc = local_bindings(fn)
                clash = loc & mod_deps[s.module]
                for c in clash:
                    refs = {owner.get(n.id) for n in global_name_refs(fn)
                            if owner.get(n.id) != s.module}
                    if c in refs:
                        report.append(f"L{fn.lineno}: local {c!r} in {s.module}."
                                      f"{getattr(fn, 'name', '<lambda>')} shadows module {c} "
                                      f"that the function references")

    # import-time dependencies (module-level statements using other modules)
    import_time = collections.defaultdict(set)

    def def_time_refs(node):
        """Names evaluated when a def/class statement itself executes."""
        exprs = list(getattr(node, "decorator_list", []))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            exprs += [d for d in node.args.defaults + node.args.kw_defaults if d is not None]
            exprs += [a.annotation for a in node.args.posonlyargs + node.args.args
                      + node.args.kwonlyargs if a.annotation is not None]
            if node.returns is not None:
                exprs.append(node.returns)
        elif isinstance(node, ast.ClassDef):
            exprs += node.bases + [k.value for k in node.keywords]
            for b in node.body:
                if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    exprs += def_time_exprs(b)
                else:
                    exprs.append(b)
        out = []
        for e in exprs:
            out += global_name_refs(e)
        return out

    def def_time_exprs(node):
        exprs = list(getattr(node, "decorator_list", []))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            exprs += [d for d in node.args.defaults + node.args.kw_defaults if d is not None]
        return exprs

    # a function CALLED while a module is imported needs, at that moment,
    # every module its body reaches: follow the defs a statement names
    body_refs = {}
    for s in stmts:
        if s.kind == "def" and s.module not in (None, "__facade__"):
            body_refs[s.names[0]] = {nm.id for nm in global_name_refs(s.node)}

    def closure(names):
        seen, todo = set(), list(names)
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            todo.extend(body_refs.get(n, ()))
        return seen

    for m, bases in extra_top.items():
        import_time[m] |= bases
    for m, used in mod_imports.items():
        for n in used:
            if n in byname and byname[n] != m:
                import_time[m].add(f"{byname[n]}.{n}")
    for s in units:
        if s.module in (None, "__facade__", "deps"):
            continue
        refs = def_time_refs(s.node) if s.kind == "def" else global_name_refs(s.node)
        for ident in sorted(closure({nm.id for nm in refs})):
            mod = owner.get(ident)
            if mod and mod not in (s.module, "deps") and ident not in import_names:
                import_time[s.module].add(f"{mod}.{ident}")
        if s.kind != "def" and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                                   and c.func.id == "globals" for c in ast.walk(s.node)):
            report.append(f"IMPORT-TIME globals() at L{s.first}: needs the facade loaded")

    # cycles among import-time deps
    def reaches(a, b, seen=None):
        seen = seen or set()
        for c in {x.split(".")[0] for x in import_time.get(a, ())}:
            if c == b or (c not in seen and reaches(c, b, seen | {c})):
                return True
        return False
    for a, bs in import_time.items():
        for b in {x.split(".")[0] for x in bs}:
            if reaches(b, a):
                report.append(f"IMPORT-TIME CYCLE {a} <-> {b}")

    # The order the package __init__ imports its modules in. Importing any
    # module of a package runs the __init__ first, so this is THE load order,
    # whatever a caller imports. It is a topological order of the import-time
    # needs (map order breaks ties); a module's runtime imports, at its end,
    # may still load a module early, so the whole import is simulated: no
    # module may read another while that one's body has not run.
    # need_of: every module whose body must have run before this one's does;
    # top_of: what the module's head actually imports, in the order it does
    # (`from .wirelog import log` lines, then `from . import a, b`);
    # end_of: the run-time imports at its end
    need_of = {m: sorted({x.split(".")[0] for x in import_time.get(m, ())}) for m in mods}
    top_mods = {m: sorted(set(need_of[m]) & set(mod_deps[m])) for m in mods}
    top_of = {}
    for m in mods:
        homes = sorted({byname[n] for n in mod_imports[m] if n in byname and byname[n] != m})
        top_of[m] = homes + [d for d in top_mods[m] if d not in homes]
    end_of = {m: sorted(set(mod_deps[m]) - set(top_of[m])) for m in mods}
    load_order, placed = [], set()
    pending = (list(first_mods) + ["deps"]
               + [m for m in mods if m not in first_mods and m != "deps"])
    while pending:
        for m in pending:
            if all(d in placed for d in need_of[m]):
                break
        else:
            report.append(f"IMPORT-TIME CYCLE among {pending}")
            break
        pending.remove(m)
        placed.add(m)
        load_order.append(m)

    def simulate(order):
        """The first (reader, module read too early, chain) of an import in
        this order, or None when every module finds what it reads."""
        state = {}
        failures = []

        def load(m, stack):
            state[m] = "top"
            for d in top_of[m]:
                if d not in state:
                    load(d, stack + [m])
            for d in sorted(set(need_of[m]) | set(top_of[m])):
                if state.get(d, "top") == "top":
                    failures.append((m, d, stack + [m]))
            state[m] = "end"
            for d in end_of[m]:
                if d not in state:
                    load(d, stack + [m])
            state[m] = "done"
        for m in order:
            if m not in state:
                load(m, ["__init__"])
        return failures[0] if failures else None

    # a module read too early moves ahead of the __init__ entry whose import
    # chain reached it too late, until an import of the whole package is clean
    for _attempt in range(len(load_order) * 4):
        bad = simulate(load_order)
        if bad is None:
            break
        _reader, early, chain = bad
        at = chain.index(early)
        if at == 1:
            # the module read too early is the __init__ entry itself: what it
            # was importing when the chain came back to it goes first
            mover, before = chain[2], early
        else:
            mover, before = early, chain[1]
        load_order.remove(mover)
        load_order.insert(load_order.index(before), mover)
    if load_order[:len(first_mods)] != list(first_mods):
        report.append(f"LOAD ORDER: the !first modules {first_mods} are no longer first")
    bad = simulate(load_order)
    if bad:
        report.append(f"LOAD ORDER: {bad[0]} reads {bad[1]} before {bad[1]}'s body has run "
                      f"(import chain {' -> '.join(bad[2])})")

    print(f"statements: {len(stmts)}  modules: {len(bodies)}  unmapped: {len(unmapped)}  "
          f"followed the statement above: {len(followed)}")
    for m, c in stats.most_common():
        print(f"  {c:>4}  {m}  (uses: {', '.join(sorted(mod_deps[m]))})")
    if import_time:
        print("import-time dependencies:")
        for a, bs in import_time.items():
            print(f"  {a} -> {', '.join(sorted(bs))}")
    if followed:
        print("unnamed statements kept with the statement above:")
        for s in followed:
            print(f"  L{s.first} -> {s.module}: {first_line_of(s)[:80]}")
    if report:
        print("\nREPORT (%d):" % len(report))
        for r in report:
            print("  " + r)
    if args.check or not args.out:
        return
    if unmapped or any(r.startswith(("IMPORT-TIME CYCLE", "UNMAPPED", "MODULE NAME CLASH",
                                     "UNOWNED", "IMPORT-TIME globals", "SHARED",
                                     "LOAD ORDER", "MIXIN"))
                       or "shadows module" in r or "position mismatch" in r
                       or "cannot rewrite" in r for r in report):
        raise SystemExit("refusing to write: fix the report first")

    os.makedirs(args.out, exist_ok=True)
    for old in os.listdir(args.out):
        if old.endswith(".py"):
            # a module the map no longer names must not survive a regeneration
            os.remove(os.path.join(args.out, old))
    order = [m for m in mods if m != "deps"]
    order.insert(0, "deps")
    for m in order:
        spec = mods[m]
        buf = io.StringIO()
        buf.write(module_docstring(spec["doc"]))
        imp = import_lines_for(mod_imports[m], import_stmts, optional_names, byname)
        if m == "deps":
            buf.write(imp)
            buf.write("".join(bodies["deps"]))
            buf.write(FLAT_GLOBALS.format(facade=facade_name))
        else:
            buf.write(imp)
            now = top_mods[m]
            later = end_of[m]
            if now:
                buf.write(sibling_import(now))
            body = "".join(bodies[m]).strip("\n") + "\n"
            buf.write("\n\n" + body)
            if later:
                buf.write("\n\n# Called at run time only; imported last so that import cycles resolve.\n")
                buf.write(sibling_import(later, "  # noqa: E402"))
        content = buf.getvalue()
        if not content.endswith("\n"):
            content += "\n"
        with open(os.path.join(args.out, m + ".py"), "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    # package init: the layout, in map order, with each module's docstring
    width = max(len(m) for m in order) + 4
    with open(os.path.join(args.out, "__init__.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write('"""' + (args.title or f"The {args.package} package") + ", one module per concern.\n\n")
        for m in order:
            f.write(textwrap.fill(mods[m]["doc"], 96, initial_indent=f"    {m + '.py':<{width}} ",
                                  subsequent_indent=" " * (5 + width))
                    + "\n")
        f.write(f"\n{facade_name}.py (one directory up) is the entry point and the compatibility\n"
                'facade over these modules.\n"""\n')
        f.write("# Every module, in the order that lets each one read another's constants\n"
                "# while it is imported (tools/split/split_fmo.py derives and checks it).\n")
        f.write(sibling_import(load_order))
        f.write("\n__all__ = [\n")
        f.write(textwrap.fill(" ".join(f"{m!r}," for m in order), 96,
                              initial_indent="    ", subsequent_indent="    ") + "\n")
        f.write("]\n")
    # facade
    if args.facade:
        owners_lines = "\n".join(f"    {n!r}: {m!r}," for n, m in sorted(owner.items()))
        doc = "".join(s.text for s in facade_head
                      if isinstance(s.node, ast.Expr) and isinstance(s.node.value, ast.Constant))
        main_block, main_imports = "", set()
        for s in facade_head:
            if isinstance(s.node, ast.Expr) and isinstance(s.node.value, ast.Constant):
                continue
            t, _deps, used = rewrite_statement(
                s, owner, lines, import_names, report,
                qualify=lambda mod, ident: f"_MODULES[{mod!r}].{ident}")
            main_block += "\n\n" + t.lstrip("\n")
            main_imports |= used
        main_imp = import_lines_for(main_imports - {"importlib", "sys", "types"},
                                    import_stmts, optional_names)
        main_imp = "".join(sorted(set(main_imp.splitlines(keepends=True))
                                  | {"import importlib\n", "import sys\n", "import types\n"}))
        facade = FACADE_TEMPLATE.format(
            docstring=doc,
            facade=facade_name,
            package=args.package,
            main_imports=main_imp,
            owners=owners_lines,
            module_names="\n".join(f"    {m!r}," for m in order),
            main_block=main_block,
        )
        with open(args.facade, "w", encoding="utf-8", newline="\n") as f:
            f.write(facade)
    print("written:", args.out, "and", args.facade)


if __name__ == "__main__":
    main()
