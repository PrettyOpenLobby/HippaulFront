#!/usr/bin/env python3
"""Prove that every rebinding of an fmo.py name still reaches the code that reads it.

    python tools/facade_rebind_check.py        # -v lists every name that passes

services/fmo.py was one 32,000-line module; it is now a facade over the
services/fmoserver/ package. A test that patches a name has to land in the
module that owns the name, or the code under test keeps calling the real
thing and the test passes while testing nothing. The patches come in three
shapes, and this tool collects all three from the source:

- `fmo.NAME = value` (or through any alias: `import fmo as F`, or a module
  loaded by hand and registered as `sys.modules["fmo"]`) in tools/ and
  services/;
- `flat_globals()["NAME"] = value`, `.update(NAME=value)` and the like in the
  package: the selftest's old `globals()` patches, which now go through the
  facade;
- nothing else: a `global NAME` the selftest used to rebind is written as
  `<module>.NAME = value` by the split, which is the owner by construction.

For each name it sets a sentinel through the facade (both as an attribute and
through the flat_globals mapping) and reads it back from the owning module and
from every package module that holds a copy of the name. A patched name that
the flat file never defined is listed separately: that write reached nothing
before the split either.
"""
import argparse
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICES = os.path.join(ROOT, "services")
SCAN_DIRS = ("tools", "services", "tests")
FACADE = "fmo"


def py_files():
    for d in SCAN_DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for fn in sorted(filenames):
                if fn.endswith(".py"):
                    path = os.path.join(dirpath, fn)
                    yield os.path.relpath(path, ROOT).replace(os.sep, "/"), path


def facade_aliases(tree):
    """Names bound to the facade module in this file."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == FACADE:
                    out.add(a.asname or a.name)
        # sys.modules["fmo"] = m
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
            for t in node.targets:
                if (isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                        and t.slice.value == FACADE):
                    out.add(node.value.id)
    return out


def is_flat_globals_call(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "flat_globals" and not node.args)


def rebindings():
    """[(file, line, name, how)] for every patch of a facade name."""
    found = []
    for rel, path in py_files():
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except (SyntaxError, UnicodeDecodeError):
            continue
        aliases = facade_aliases(tree)
        # variables holding the flat_globals() mapping: `_g = flat_globals()`
        holders = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and is_flat_globals_call(node.value):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        holders.add(t.id)

        def is_mapping(expr):
            return is_flat_globals_call(expr) or (isinstance(expr, ast.Name)
                                                  and expr.id in holders)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    elts = tgt.elts if isinstance(tgt, (ast.Tuple, ast.List)) else [tgt]
                    for t in elts:
                        if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                                and t.value.id in aliases):
                            found.append((rel, node.lineno, t.attr, "attribute"))
                        if (isinstance(t, ast.Subscript) and is_mapping(t.value)
                                and isinstance(t.slice, ast.Constant)
                                and isinstance(t.slice.value, str)):
                            found.append((rel, node.lineno, t.slice.value, "flat_globals"))
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("update", "setdefault")
                    and is_mapping(node.func.value)):
                for kw in node.keywords:
                    if kw.arg:
                        found.append((rel, node.lineno, kw.arg, "flat_globals"))
                if (node.func.attr == "setdefault" and node.args
                        and isinstance(node.args[0], ast.Constant)):
                    found.append((rel, node.lineno, node.args[0].value, "flat_globals"))
    return found


def forwards(F, name, how):
    """None when a write of `name` through the facade reaches the owning module
    and every module holding a copy; otherwise the reason."""
    owners, modules = F._OWNERS, F._MODULES
    home = owners.get(name)
    if home is None:
        return "unowned"
    saved = {m: vars(mod)[name] for m, mod in modules.items() if name in vars(mod)}
    try:
        for label, write in (("attribute", lambda v: setattr(F, name, v)),
                             ("flat_globals", lambda v: F.FLAT_GLOBALS.__setitem__(name, v))):
            sentinel = object()
            write(sentinel)
            if getattr(modules[home], name, None) is not sentinel:
                return f"a write as {label} did not reach the owner fmoserver.{home}"
            for m in saved:
                if getattr(modules[m], name) is not sentinel:
                    return f"a write as {label} left the old copy in fmoserver.{m}"
            if getattr(F, name) is not sentinel or F.FLAT_GLOBALS[name] is not sentinel:
                return f"a read-back after a write as {label} returned something else"
    finally:
        for m, old in saved.items():
            setattr(modules[m], name, old)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    sys.path.insert(0, SERVICES)
    import fmo as F

    if not hasattr(F, "_OWNERS"):
        print("services/fmo.py is not the facade (no _OWNERS)")
        return 1
    found = rebindings()
    names = sorted({n for _f, _l, n, _h in found})
    print(f"{len(found)} rebinding(s) of {len(names)} name(s) across tools/ and services/")
    failures, dead = [], []
    for name in names:
        sites = [f"{f}:{l}" for f, l, n, _h in found if n == name]
        why = forwards(F, name, None)
        if why == "unowned":
            dead.append((name, sites))
        elif why:
            failures.append((name, why, sites))
        elif args.verbose:
            print(f"  [PASS] {name:<28} -> fmoserver.{F._OWNERS[name]}  ({', '.join(sites)})")
    for name, sites in dead:
        print(f"  [NOTE] {name}: not a name the flat fmo.py defined; the write reached nothing "
              f"before the split either\n         at {', '.join(sites)}")
    for name, why, sites in failures:
        print(f"  [FAIL] {name}: {why}\n         at {', '.join(sites)}")
    if failures:
        print(f"\n{len(failures)} name(s) are patched but not forwarded to the module that reads them")
        return 1
    print("every rebinding is forwarded to the module that owns the name")
    return 0


if __name__ == "__main__":
    sys.exit(main())
