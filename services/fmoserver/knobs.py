"""Reading an environment knob, where an empty value counts as unset."""
import os


def _env_int(name, default, base=0):
    """int() of an env knob, treating an EMPTY value as UNSET.

    WARNING: `os.environ.get(name, default)` returns "" -- never the default -- when
    the variable IS set and empty, which is exactly what a bare `FMO_X=` in
    .env or a `${FMO_X:-}` compose line hands us; and `int("", 0)` raises at
    IMPORT, which is a crash-looping container (the empty-env trap, the outage
    that took fmo down for ~10 min on 2026-08-27, and the 3rd instance of the
    same trap in this project). Every knob that had a `${X:-}` compose default
    was already guarded by hand with `.strip() or "d"`; the rest were safe
    ONLY because their compose defaults happened to be non-empty. This makes
    the guard live in the code, where it cannot drift out from under a .env
    edit. `base` is preserved from each call site (0 = int literal syntax,
    hex allowed; 10 = decimal only), so no value that parsed before parses
    differently now -- the ONLY behaviour change is empty -> default instead
    of empty -> crash."""
    return int(os.environ.get(name, "").strip() or str(default), base)


def _env_float(name, default):
    """float() of an env knob, treating an EMPTY value as UNSET (see _env_int)."""
    return float(os.environ.get(name, "").strip() or str(default))
