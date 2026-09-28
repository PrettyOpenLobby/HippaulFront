"""fmodb.py -- Front Mission Online's tables in the stack's PostgreSQL database.

The pilots, the squadron insignia, the war state and the sector-win ledger
live in the database the OpenLobby core runs (POL_DATABASE_URL), in tables
whose names start with `fmo_`. Their schema is this repository's own set of
migrations, `services/fmo_migrations/`, applied with OpenLobby's runner
(polcore.db.migrate). Versions 2001-2999 of the shared schema_migrations
table belong to CrystalFront; OpenLobby and the other titles number theirs
in their own ranges, so the sets never collide.

polcore comes with the OpenLobby image this service is built on. Outside the
image (the self-tests on a checkout) it is found through OPENLOBBY_SERVICES,
OPENLOBBY_DIR/services, or an `openlobby` checkout beside this repository.

    python fmodb.py migrate      apply what is pending (uses POL_DATABASE_URL)
    python fmodb.py status       list this repository's migrations
"""
import contextlib
import importlib
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
MIGRATIONS_DIR = os.path.join(HERE, "fmo_migrations")
#: The schema_migrations versions this repository owns.
VERSION_RANGE = (2001, 2999)


def _openlobby_candidates():
    """Directories that may hold OpenLobby's services/ (with polcore/)."""
    out = []
    if os.environ.get("OPENLOBBY_SERVICES"):
        out.append(os.environ["OPENLOBBY_SERVICES"])
    if os.environ.get("OPENLOBBY_DIR"):
        out.append(os.path.join(os.environ["OPENLOBBY_DIR"], "services"))
    out.append(os.path.join(HERE, os.pardir, os.pardir, "openlobby", "services"))
    return [os.path.abspath(p) for p in out]


def _find_polcore():
    try:
        return importlib.import_module("polcore.db")
    except ImportError:
        pass
    for cand in _openlobby_candidates():
        if os.path.isfile(os.path.join(cand, "polcore", "db.py")):
            if cand not in sys.path:
                sys.path.append(cand)
            return importlib.import_module("polcore.db")
    raise ImportError("polcore (OpenLobby's services/polcore) was not found; "
                      "set OPENLOBBY_SERVICES or OPENLOBBY_DIR")


db = _find_polcore()

#: What a failed database call raises, for callers that degrade rather than
#: fail (a login must survive a database fault; it is logged instead).
ERRORS = tuple(e for e in (
    db.DatabaseNotConfigured, db.MigrationError, RuntimeError,
    getattr(getattr(db, "psycopg", None), "Error", None)) if e is not None)


class DatabaseDisabled(RuntimeError):
    """This process was told not to touch any database (a self-test with no
    throwaway server: it must never fall through to POL_DATABASE_URL)."""


_lock = threading.Lock()
_ready = set()
_disabled = [False]


def migration_files():
    """polcore's listing of this repository's migrations, checked against the
    version range so a misnumbered file cannot land in another set's slot."""
    files = db.migration_files(MIGRATIONS_DIR)
    lo, hi = VERSION_RANGE
    bad = [name for version, name, _ in files if not lo <= version <= hi]
    if bad:
        raise db.MigrationError(f"{', '.join(bad)}: CrystalFront's migrations "
                                f"are numbered {lo}-{hi}")
    return files


def ready():
    """Apply this repository's pending migrations, once per process and
    database. Raises when the database cannot be used; callers that must not
    fail catch ERRORS (DatabaseDisabled is one of them, a RuntimeError)."""
    if _disabled[0]:
        raise DatabaseDisabled("the database is switched off in this process")
    url = db.database_url()
    if url in _ready:
        return
    with _lock:
        if url in _ready:
            return
        migration_files()
        db.migrate(directory=MIGRATIONS_DIR,
                   log=lambda msg: print(f"[fmodb] {msg}", file=sys.stderr))
        _ready.add(url)                 # only once it is genuinely ready


def configure(url=None, disabled=False):
    """Point this process at `url` (tests), or switch the database off.
    `url=None` and not disabled goes back to POL_DATABASE_URL."""
    with _lock:
        _disabled[0] = bool(disabled)
        _ready.clear()
    db.configure(None if disabled else url)


def enabled():
    """True when a database is configured and not switched off."""
    if _disabled[0]:
        return False
    try:
        db.database_url()
        return True
    except db.DatabaseNotConfigured:
        return False


# --------------------------------------------------------------------------- #
# throwaway databases for the self-tests
# --------------------------------------------------------------------------- #
def _pgtest():
    try:
        return importlib.import_module("pgtest")
    except ImportError:
        pass
    for cand in _openlobby_candidates():
        tools = os.path.join(os.path.dirname(cand), "tools")
        if os.path.isfile(os.path.join(tools, "pgtest.py")):
            if tools not in sys.path:
                sys.path.append(tools)
            return importlib.import_module("pgtest")
    return None


@contextlib.contextmanager
def test_database():
    """A fresh, empty database for a self-test, with this process pointed at
    it and the migrations applied; yields its URL.

    Uses OpenLobby's tools/pgtest.py (a throwaway postgres container, or
    POL_TEST_DATABASE_URL). With neither Docker nor that variable it yields
    None and leaves the database SWITCHED OFF, so a self-test run inside a
    deployed container can never reach the real one through
    POL_DATABASE_URL. POL_TEST_REQUIRE_DB=1 turns that case into an error.
    """
    required = os.environ.get("POL_TEST_REQUIRE_DB", "") == "1"
    pg = _pgtest()
    url = None
    if pg is not None:
        try:
            url = pg.create_database()
        except Exception as exc:        # no Docker, no server
            if required:
                raise
            print(f"[fmodb] no test database ({exc}); database checks SKIP")
    elif required:
        raise RuntimeError("POL_TEST_REQUIRE_DB=1 but OpenLobby's tools/pgtest.py "
                           "was not found (OPENLOBBY_DIR)")
    else:
        print("[fmodb] OpenLobby's tools/pgtest.py not found; database checks SKIP")
    if url is None:
        configure(disabled=True)
        try:
            yield None
        finally:
            configure(disabled=False)
        return
    configure(url)
    try:
        ready()
        yield url
    finally:
        db.close()
        with contextlib.suppress(Exception):
            pg.drop_database(url)
        configure(disabled=False)


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="python fmodb.py",
                                 description="CrystalFront's migrations "
                                 "(uses POL_DATABASE_URL).")
    ap.add_argument("cmd", choices=("migrate", "status"))
    args = ap.parse_args(argv)
    try:
        if args.cmd == "migrate":
            before = set(db.applied_migrations())
            ready()
            done = [name for version, name, _ in migration_files()
                    if version not in before]
            print("applied: " + ", ".join(done) if done else "up to date")
        else:
            have = db.applied_migrations()
            for version, name, _path in migration_files():
                row = have.get(version)
                state = (f"applied {row['applied_at']:%Y-%m-%d %H:%M}"
                         if row else "pending")
                print(f"{name:<32} {state}")
        return 0
    except ERRORS as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
