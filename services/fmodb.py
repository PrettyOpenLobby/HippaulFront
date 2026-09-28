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
    python fmodb.py import fmo_db FILE [--merge] [--dry-run]
    python fmodb.py import board_state FILE|DIR [--merge] [--dry-run]

`import` moves what an earlier release kept in files into the tables:
`fmo_db` reads fmo.db, the SQLite pilot database, into fmo_character and
fmo_squadron_insignia; `board_state` reads the City Control board's
<name>_discord.json and discord_channels.json (a file, or every such file in
a directory) into fmo_board_state. The source is only read (SQLite in
read-only mode). The import runs in one transaction and refuses a table that
already holds rows (exit 2) unless --merge is given, which adds only the keys
the table lacks. A second run finds nothing to add. --dry-run prints the same
report and writes nothing. Rows it cannot map are listed and skipped.

fmo_characters.json, fmowar.json and fmo_sector_wins.json need no command:
the fmo service imports each the first time it finds its table empty. Import
fmo.db before the service first starts, or the older fmo_characters.json
fills fmo_character first and fmo.db is refused.
"""
import contextlib
import datetime
import importlib
import json
import os
import pathlib
import sqlite3
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


# --------------------------------------------------------------------------- #
# importing the files an earlier release kept
# --------------------------------------------------------------------------- #
class SourceError(RuntimeError):
    """The source cannot be read as the file it should be."""


class _Rollback(Exception):
    pass


class Plan:
    """One target table of an import: the rows read, and what became of them."""

    def __init__(self, table, key, cols, rows, jsonb=(), compare_skip=()):
        self.table, self.key, self.cols = table, tuple(key), tuple(cols)
        self.rows, self.jsonb = rows, set(jsonb)
        self.compare_skip = set(compare_skip)
        self.target = 0
        self.exists = False
        self.new, self.same, self.differs, self.dups = [], 0, [], 0
        self.inserted = 0

    def keyof(self, row):
        return tuple(row[c] for c in self.key)


def open_sqlite_readonly(path):
    """A read-only connection: `mode=ro` refuses every write and never makes
    a journal beside the file."""
    if not os.path.isfile(path):
        raise SourceError(f"{path} does not exist")
    uri = pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("PRAGMA query_only = ON")
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error as exc:
        raise SourceError(f"cannot open {path} read-only: {exc}") from None
    conn.row_factory = sqlite3.Row
    return conn


def _as_int(value, what):
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value.strip())
        else:
            raise ValueError(f"{what} is {value!r}, not a whole number")
    if not -(1 << 63) <= value < (1 << 63):
        raise ValueError(f"{what} {value} does not fit a BIGINT")
    return value


def _as_text(value, what, null=True):
    if value is None and null:
        return None
    if isinstance(value, (bytes, memoryview)):
        raise ValueError(f"{what} holds bytes")
    if value is None:
        raise ValueError(f"{what} is empty")
    value = value if isinstance(value, str) else str(value)
    if "\x00" in value:
        raise ValueError(f"{what} holds a NUL character")
    return value


def read_old_fmo_db(path):
    """([Plan, Plan], skipped) from fmo.db, the SQLite pilot database
    (fmostore.py before the move, schema version 1).

    The columns are the old ones, table for table. SQLite kept any value in
    any column; a value the new column cannot hold as it was written (a
    float, text in a number column, a boolean) goes into `extra`, where the
    loader has always merged it back into the record, exactly as fmostore's
    writer does for a new record."""
    import fmostore
    conn = open_sqlite_readonly(path)
    skipped = []
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        chars, ins = [], []
        if "character" in tables:
            for r in conn.execute("SELECT rowid AS _rowid, * FROM character"
                                  " ORDER BY account, slot_ord, id"):
                r = dict(r)
                rowid = r.pop("_rowid")
                try:
                    row = {"account": _as_text(r.get("account"), "account", null=False),
                           "id": _as_int(r.get("id"), "id"),
                           "slot_ord": _as_int(r.get("slot_ord") or 0, "slot_ord"),
                           "created_at": _as_text(r.get("created_at"), "created_at"),
                           "updated_at": _as_text(r.get("updated_at"), "updated_at")}
                    moved = {}
                    for c in fmostore.COLUMNS[1:]:
                        v = r.get(c)
                        if isinstance(v, (bytes, memoryview)):
                            raise ValueError(f"{c} holds bytes")
                        if isinstance(v, str) and "\x00" in v:
                            raise ValueError(f"{c} holds a NUL character")
                        if fmostore._storable(c, v):
                            row[c] = v
                        else:
                            row[c] = None
                            moved[c] = v
                    extra = r.get("extra")
                    if moved:
                        base = {}
                        if extra:
                            base = json.loads(extra)
                            if not isinstance(base, dict):
                                raise ValueError("extra is not a JSON object")
                        base.update(moved)
                        extra = json.dumps(base, sort_keys=True)
                    row["extra"] = _as_text(extra, "extra")
                except ValueError as exc:
                    skipped.append((f"character rowid {rowid}", str(exc)))
                    continue
                chars.append(row)
        if "squadron_insignia" in tables:
            for r in conn.execute("SELECT rowid AS _rowid, * FROM squadron_insignia"
                                  " ORDER BY group_id"):
                r = dict(r)
                try:
                    ins.append({"group_id": _as_int(r.get("group_id"), "group_id"),
                                "insignia": _as_int(r.get("insignia"), "insignia"),
                                "set_by": _as_text(r.get("set_by"), "set_by"),
                                "set_at": _as_text(r.get("set_at"), "set_at")})
                except ValueError as exc:
                    skipped.append((f"squadron_insignia rowid {r['_rowid']}", str(exc)))
        if "character" not in tables and "squadron_insignia" not in tables:
            raise SourceError(f"{path} has neither a character nor a "
                              "squadron_insignia table; is it fmo.db?")
    except sqlite3.Error as exc:
        raise SourceError(f"cannot read {path}: {exc}") from None
    finally:
        conn.close()
    char_cols = ("account", "id", "slot_ord") + fmostore.COLUMNS[1:] + (
        "extra", "created_at", "updated_at")
    return [Plan("fmo_squadron_insignia", ("group_id",),
                 ("group_id", "insignia", "set_by", "set_at"), ins),
            Plan("fmo_character", ("account", "id"), char_cols, chars)], skipped


def read_old_board_state(path, table):
    """([Plan], skipped) from a board's old Discord state: one
    <name>_discord.json or discord_channels.json, or every such file directly
    in a directory. The row is named for the file without its extension, as
    polboards names it, and updated_at is the file's modification time."""
    if os.path.isdir(path):
        names = sorted(os.listdir(path))
        files = [os.path.join(path, n) for n in names]
    elif os.path.isfile(path):
        files = [path]
    else:
        raise SourceError(f"{path} does not exist")
    rows, skipped = [], []
    for f in files:
        n = os.path.basename(f)
        if not os.path.isfile(f):
            continue
        if not (n.endswith("_discord.json") or n == "discord_channels.json"):
            skipped.append((n, "not a board state file (<name>_discord.json, "
                               "discord_channels.json)"))
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError(f"holds a {type(data).__name__}, not an object")
        except (OSError, ValueError) as exc:
            skipped.append((n, str(exc)))
            continue
        rows.append({"name": n[:-len(".json")], "data": data,
                     "updated_at": datetime.datetime.fromtimestamp(
                         os.path.getmtime(f), datetime.timezone.utc)})
    return [Plan(table, ("name",), ("name", "data", "updated_at"), rows,
                 jsonb=("data",), compare_skip=("updated_at",))], skipped


IMPORTS = {
    "fmo_db": read_old_fmo_db,
    "board_state": lambda path: read_old_board_state(path, "fmo_board_state"),
}


def import_source(store, path, merge=False, dry_run=False, out=print):
    """Import one old source. Returns the exit status: 0 done, nothing to do
    or dry run; 1 the source or the database failed; 2 refused, a table
    already holds rows and the source has rows it lacks (without --merge)."""
    try:
        plans, skipped = IMPORTS[store](path)
    except SourceError as exc:
        out(f"error: {exc}")
        return 1
    out(f"import {store}: {path}")
    for what, why in skipped:
        out(f"  skipped {what}: {why}")
    if not dry_run:
        ready()
    status = None
    try:
        with db.transaction(lock="crystalfront.import") as conn:
            for p in plans:
                p.exists = conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ok",
                                        (p.table,)).fetchone()["ok"]
                if not p.exists and not dry_run:
                    raise RuntimeError(f"{p.table} does not exist after the migrations")
                have = {}
                if p.exists:
                    q = 'SELECT %s FROM %s' % (", ".join('"%s"' % c for c in p.cols), p.table)
                    have = {p.keyof(r): r for r in conn.execute(q)}
                p.target = len(have)
                seen = set()
                for r in p.rows:
                    k = p.keyof(r)
                    if k in seen:
                        p.dups += 1
                        continue
                    seen.add(k)
                    if k not in have:
                        p.new.append(r)
                    elif all(have[k][c] == r[c] for c in p.cols if c not in p.compare_skip):
                        p.same += 1
                    else:
                        p.differs.append(k)
            for p in plans:
                out(f"  {p.table}: {len(p.rows) + p.dups} read, {p.target} in the "
                    f"table{'' if p.exists else ' (not created yet)'}, "
                    f"{p.same + len(p.differs)} already there, {len(p.new)} to insert")
                if p.dups:
                    out(f"    skipped, key repeated: {p.dups}")
                for k in p.differs:
                    out(f"    kept the table's row, the source's differs: {k}")
            if any(p.new and p.target for p in plans) and not merge:
                status = "refused"
                raise _Rollback()
            if dry_run:
                status = "dry-run"
                raise _Rollback()
            for p in plans:
                sql = 'INSERT INTO %s (%s) VALUES (%s) ON CONFLICT DO NOTHING' % (
                    p.table, ", ".join('"%s"' % c for c in p.cols),
                    ", ".join("%s::jsonb" if c in p.jsonb else "%s" for c in p.cols))
                for r in p.new:
                    p.inserted += conn.execute(sql, [
                        json.dumps(r[c], sort_keys=True) if c in p.jsonb else r[c]
                        for c in p.cols]).rowcount
            status = "done" if any(p.inserted for p in plans) else "nothing"
    except _Rollback:
        pass
    except ERRORS as exc:
        out(f"FAILED, rolled back: {exc}")
        return 1
    if status == "refused":
        busy = ", ".join(p.table for p in plans if p.new and p.target)
        out(f"REFUSED: {busy} already holds rows. Nothing was written. Run "
            "again with --merge to add only the keys it lacks.")
        return 2
    if status == "dry-run":
        out("Dry run: nothing was written.")
    elif status == "nothing":
        out("Nothing to import: the tables already hold every row. "
            "Nothing was changed.")
    else:
        for p in plans:
            out(f"  {p.table}: {p.inserted} inserted")
        out(f"Done: {sum(p.inserted for p in plans)} row(s) written.")
    return 0


def _import_main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="python fmodb.py import",
                                 description="Import what an earlier release "
                                 "kept in files (uses POL_DATABASE_URL).")
    ap.add_argument("store", choices=sorted(IMPORTS))
    ap.add_argument("source")
    ap.add_argument("--merge", action="store_true",
                    help="add only the keys the tables lack")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be imported; write nothing")
    args = ap.parse_args(argv)
    try:
        return import_source(args.store, args.source, merge=args.merge,
                             dry_run=args.dry_run)
    except ERRORS as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def _main(argv):
    if argv and argv[0] == "import":
        return _import_main(argv[1:])
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
    # fmostore imports this module as `fmodb`; one copy, so the migrations
    # it applies are the ones this run knows about
    sys.modules.setdefault("fmodb", sys.modules[__name__])
    sys.exit(_main(sys.argv[1:]))
