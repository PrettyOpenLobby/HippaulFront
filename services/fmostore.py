#!/usr/bin/env python3
"""fmostore.py -- the FMO player database.

WHY THIS FILE EXISTS. Front Mission Online on this server has been a probe
harness: ~188 `FMO_*` environment knobs statically inject what a real server
would compute. Rank is `FMO_RANK`, money is `FMO_STATUS_MONEY`, the tutorial
"pilot registered" marker is a substring of `FMO_STATUS_FLAGS` -- and every one
of those is GLOBAL, so two players share one set of numbers and nothing a
player does changes anything they see next login. `fmo_characters.json` held
identity and garage setups and nothing else.

This is the store those numbers move into: one row per character, per POL
account, in SQLite. The knobs stay, demoted to what they always should have
been -- the SEED for a character that has no stored value, and an admin
override for a probe.

DELIBERATELY ITS OWN FILE, NOT A TABLE IN accounts.db. accounts.db is opened by
every other service on this server and has already been truncated once by a
container restart landing on a schema write.
Adding a table that FMO writes on every sortie to the database that holds every
account is trading a contained risk for an uncontained one. The connection
disciplines below are copied from `accounts.connect()` -- they were each paid
for by a live failure -- but the FILE is separate.

DROP-IN SHAPE. `load_roster` / `save_roster` / `store_accounts` return and take
exactly what the JSON store did: a list of plain dicts, oldest first. Every
call site in fmo.py is unchanged. Keys this schema does not know about are kept
verbatim in an `extra` JSON column, so a decode that grows a field later cannot
silently drop it.

Run standalone:
    python fmostore.py --selftest              # no files touched but a temp one
    python fmostore.py --show [<db>]           # what is on file
    python fmostore.py --import <json> [<db>]  # one-shot migration
    python fmostore.py --seed rank=21 money=12345 mp=67 flags=128=99 \\
                       [--db=<path>] [--account=member:3] [--force]

WARNING: RUN IT INSIDE THE CONTAINER, not from the Windows host:
    docker compose exec fmo python /app/fmostore.py --show /data/fmo.db
Opening a SQLite file on the /data bind mount from the host can leave it in a
journal mode the container cannot then open.
On Git Bash prefix `MSYS_NO_PATHCONV=1` or /app/... is mangled to a Windows path.
"""
import json
import os
import sqlite3
import sys
import threading
import time

# --------------------------------------------------------------------------- #
# where it lives
# --------------------------------------------------------------------------- #
#: Same resolution trick as fmo.py's `_default_store()`: services/ is bind
#: mounted at /app in the container, so `_HERE/../data` is pol-server/data on
#: the host and /data in prod. Probing an absolute `/data` first is wrong on
#: Windows, where it means `<current drive>/data`.
_HERE = os.path.dirname(os.path.abspath(__file__))


def default_db():
    return os.path.join(_HERE, os.pardir, "data", "fmo.db")


#: Empty disables the database completely -- fmo.py then keeps using the JSON
#: store, which is the state every measurement before 2026-09-08 ran against.
DB_PATH = os.environ.get("FMO_DB", default_db())

#: Shared with accounts.py on purpose: one server, one journal mode. TRUNCATE
#: (not WAL) because /data is a Windows bind mount on the dev box and WAL needs
#: a shared-memory mapping those do not provide -- see accounts-db-wal-hazard.
JOURNAL_MODE = os.environ.get("POL_SQLITE_JOURNAL", "TRUNCATE")

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- THE SQUADRON'S REGISTERED INSIGNIA, keyed by POL GROUP id (an FMO squadron
-- IS a POL group -- the squadron menu drives the POL group calls). Written
-- when the client sends 0x01C4, whose body is
-- {u64 group id @+0x00, u32 insignia id @+0x08} -- measured live 2026-09-09,
-- group 3 / insignia 131 "Wild Apes".
--
-- It is per GROUP, not per character or per account: every member of a
-- squadron sees the same insignia (SE says so in 86:24, "Every member of the
-- squadron may obtain the registered insignia freely"), and SE treats it as
-- one-time -- 86:21, "It cannot be changed afterwards".
CREATE TABLE IF NOT EXISTS squadron_insignia (
    group_id   INTEGER PRIMARY KEY,
    insignia   INTEGER NOT NULL,
    set_by     TEXT,               -- the store key of whoever registered it
    set_at     TEXT
);

-- One pilot. `account` is fmo.py's resolved store key ("member:3", or
-- "addr:<ip>" when no POL session names the box); `id` is the slot number the
-- CLIENT picked, which is what every message refers to a character by.
CREATE TABLE IF NOT EXISTS character (
    account       TEXT    NOT NULL,
    id            INTEGER NOT NULL,
    slot_ord      INTEGER NOT NULL DEFAULT 0,   -- roster order, oldest first

    -- identity, as decoded from the 0x013E creation record
    "first"       TEXT,
    "last"        TEXT,
    nation        INTEGER,      -- the OLD (swapped) key; kept, the wire uses it
    sex           INTEGER,      -- ditto -- see character_from_013e's docstring
    gender        INTEGER,      -- the STATIC names, proven on screen 2026-08-27
    nation_byte   INTEGER,
    personality   INTEGER,
    size          INTEGER,
    build         INTEGER,
    face          INTEGER,
    cls           INTEGER,
    hangar_pw     INTEGER,
    appearance    TEXT,

    -- THE ECONOMY (PLAN 1.5). Served by 0x014A; the pay/promotion leg 0x0175
    -- and the garage acquire 0x0168 are what will move them.
    rank          INTEGER,
    money         INTEGER,
    mp            INTEGER,
    contribution  INTEGER,

    -- PROGRESS. 256 bytes as hex: the kind-11 script flag block the client
    -- keeps at lobby+0xB88 (0x014A payload +0x304). Read as BITS by natives
    -- 0xE066 / the LEV row gate and as BYTE VALUES by 0xE067 -- byte 128 == 99
    -- is SE's "pilot registered", the gate on every counter and the war map.
    flags         TEXT,

    -- WHERE THIS PILOT IS. Written when a grant is served, so a relog can put
    -- the player back where they left instead of wherever a knob points.
    mapno         INTEGER,
    mapkind       INTEGER,
    pos           TEXT,         -- "x,y,z[,w]" in the 0x0153 PilotPos frame

    -- THE RESUME TRIAD (lobby+0x7604 / +0x7608 / +0xFD4). Non-zero means "this
    -- pilot was in a battle when the link died"; the client then runs the
    -- 0x0137 resume handshake instead of a normal world entry.
    resume_w7604  INTEGER,
    resume_w7608  INTEGER,
    resume_wfd4   INTEGER,

    -- garage setups (0x0165/0x0167 round trip) and the owned-parts bitset
    setups        TEXT,         -- hex, exactly as the JSON store held it
    owned_parts   TEXT,         -- hex; deferred -- a set bit CLAIMS a part

    -- what the client actually sent, kept so a partial decode loses nothing
    raw           TEXT,
    raw_0177      TEXT,
    extra         TEXT,         -- JSON: every key this schema does not name

    created_at    TEXT,
    updated_at    TEXT,
    PRIMARY KEY (account, id)
);

CREATE INDEX IF NOT EXISTS character_account ON character (account, slot_ord);
"""

#: Keys that get their own column. Everything else in a record rides in `extra`.
#: Order is the column order used by the writer.
COLUMNS = (
    "id", "first", "last", "nation", "sex", "gender", "nation_byte",
    "personality", "size", "build", "face", "cls", "hangar_pw", "appearance",
    "rank", "money", "mp", "contribution", "flags",
    "mapno", "mapkind", "pos",
    "resume_w7604", "resume_w7608", "resume_wfd4",
    "setups", "owned_parts", "raw", "raw_0177",
)

#: Columns the loader must NOT hand back as record keys.
_INTERNAL = ("account", "slot_ord", "extra", "created_at", "updated_at")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------- #
# the connection
# --------------------------------------------------------------------------- #
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = set()
_JOURNAL_WARNED = set()
_WRITE_LOCK = threading.Lock()


def connect(path=None):
    """Open (creating if needed) the FMO database with the schema applied.

    The three disciplines here are lifted from `accounts.connect()` and each
    one is a live failure someone already paid for:

    * THE SCHEMA IS APPLIED ONCE PER PROCESS. `CREATE TABLE IF NOT EXISTS`
      takes a write lock even when every statement is a no-op, so applying it
      per connection makes every reader contend with every other reader.
    * THE JOURNAL MODE IS SET PER CONNECTION. For the rollback modes it is a
      property of the CONNECTION, not the file, and sqlite opens every new one
      in DELETE -- mixing DELETE and TRUNCATE on one file raises `disk I/O
      error` on a Windows bind mount (sqlite-journal-mode-is-per-connection).
    * THE OPEN IS RETRIED. On that same bind mount an ordinary open comes back
      `unable to open database file` every so often.

    No pool: FMO opens a connection per store call, a handful per login, not
    the 7-9 per serve that made pooling worth it for the lobby.
    """
    path = path or DB_PATH
    key = os.path.abspath(path)
    parent = os.path.dirname(key)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = None
    for attempt in range(3):
        try:
            conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
            break
        except sqlite3.OperationalError:
            if attempt == 2:
                raise
            time.sleep(0.1 * (attempt + 1))
    conn.row_factory = sqlite3.Row
    got = conn.execute(f"PRAGMA journal_mode = {JOURNAL_MODE}").fetchone()
    got = (got[0] if got else "?").lower()
    if got != JOURNAL_MODE.lower() and key not in _JOURNAL_WARNED:
        _JOURNAL_WARNED.add(key)
        print(f"[fmostore] journal_mode is {got!r}, not the requested "
              f"{JOURNAL_MODE.lower()!r} -- another connection holds {path}")
    with _SCHEMA_LOCK:
        if key not in _SCHEMA_READY:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema', ?)",
                (str(SCHEMA_VERSION),))
            conn.commit()
            _SCHEMA_READY.add(key)      # only after it is genuinely ready
    return conn


def forget_schema(path=None):
    """Drop the once-per-process memo -- for tests that delete the file."""
    with _SCHEMA_LOCK:
        _SCHEMA_READY.discard(os.path.abspath(path or DB_PATH))


# --------------------------------------------------------------------------- #
# record <-> row
# --------------------------------------------------------------------------- #
def _storable(v):
    """True when sqlite can hold `v` in a column without changing it.

    Booleans are excluded ON PURPOSE: sqlite stores True as 1 and hands it back
    as 1, so a `True` written here would come back as an int and a caller
    testing `is True` would break. They ride in `extra`, which is JSON and
    round-trips them exactly.
    """
    return v is None or (isinstance(v, (int, float, str))
                         and not isinstance(v, bool))


def record_to_row(rec, account, slot_ord):
    """(column dict, extra dict) for one character record."""
    cols, extra = {}, {}
    for k, v in rec.items():
        if k in COLUMNS and _storable(v):
            cols[k] = v
        else:
            extra[k] = v
    cols["account"] = account
    cols["slot_ord"] = slot_ord
    cols["extra"] = json.dumps(extra, sort_keys=True) if extra else None
    return cols, extra


def row_to_record(row):
    """One sqlite row back to the plain dict the rest of fmo.py expects.

    A NULL column is a key the record did not have -- NOT a zero. That
    distinction is load-bearing: `_econ_value` treats a MISSING `money` as
    "fall back to the knob" and a stored 0 as "this pilot is broke".
    """
    rec = {}
    for k in row.keys():
        if k in _INTERNAL:
            continue
        v = row[k]
        if v is not None:
            rec[k] = v
    blob = row["extra"]
    if blob:
        try:
            rec.update(json.loads(blob))
        except ValueError:
            pass
    return rec


# --------------------------------------------------------------------------- #
# the API fmo.py calls
# --------------------------------------------------------------------------- #
def load_roster(account, path=None):
    """This account's characters, oldest first. [] when there are none."""
    try:
        conn = connect(path)
    except sqlite3.Error as e:
        print(f"[fmostore] load_roster({account!r}) failed to open the "
              f"database: {e!r}")
        return []
    try:
        rows = conn.execute(
            'SELECT * FROM character WHERE account = ?'
            ' ORDER BY slot_ord, id', (account,)).fetchall()
        return [row_to_record(r) for r in rows]
    except sqlite3.Error as e:
        print(f"[fmostore] load_roster({account!r}) failed: {e!r}")
        return []
    finally:
        conn.close()


def save_roster(account, roster, path=None):
    """Replace this account's list.

    Whole-list replace, like the JSON store it stands in for -- the callers
    mutate their in-memory roster and then commit it, and matching that
    contract exactly is what let this swap in without touching a call site.
    It is a DELETE + INSERT inside one transaction scoped to ONE account, so
    unlike the JSON store two accounts cannot clobber each other even in
    principle, and a crash mid-write leaves the previous state intact.
    """
    with _WRITE_LOCK:
        try:
            conn = connect(path)
        except sqlite3.Error as e:
            print(f"[fmostore] save_roster({account!r}) failed to open the "
                  f"database: {e!r}")
            return False
        try:
            now = _now()
            born = {r["id"]: r["created_at"] for r in conn.execute(
                "SELECT id, created_at FROM character WHERE account = ?",
                (account,))}
            with conn:
                conn.execute("DELETE FROM character WHERE account = ?",
                             (account,))
                for n, rec in enumerate(roster):
                    cols, _ = record_to_row(rec, account, n)
                    cols["created_at"] = born.get(cols.get("id")) or now
                    cols["updated_at"] = now
                    names = list(cols)
                    conn.execute(
                        "INSERT INTO character (%s) VALUES (%s)"
                        % (",".join('"%s"' % c for c in names),
                           ",".join("?" for _ in names)),
                        [cols[c] for c in names])
            return True
        except sqlite3.Error as e:
            print(f"[fmostore] save_roster({account!r}) failed: {e!r} -- "
                  f"{len(roster)} character(s) NOT written")
            return False
        finally:
            conn.close()



def squadron_insignia(group_id, path=None):
    """The insignia id registered for a POL group, or 0. Never raises."""
    if not group_id:
        return 0
    try:
        conn = connect(path)
        try:
            row = conn.execute(
                "SELECT insignia FROM squadron_insignia WHERE group_id = ?",
                (int(group_id),)).fetchone()
        finally:
            conn.close()
    except Exception:
        return 0
    return int(row[0]) if row else 0


def set_squadron_insignia(group_id, insignia, who=None, path=None):
    """Register a group's insignia. Returns (stored, previous).

    WARNING: SE treats this as ONE-TIME (86:21, "It cannot be changed afterwards"),
    and the client greys the menu row once it is set -- so a second attempt is
    the client disagreeing with us about state, not a normal edit. This keeps
    the FIRST value and reports the conflict rather than silently overwriting
    somebody's squadron emblem."""
    prev = squadron_insignia(group_id, path)
    if prev:
        return prev, prev
    conn = connect(path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO squadron_insignia"
            " (group_id, insignia, set_by, set_at) VALUES (?, ?, ?, ?)",
            (int(group_id), int(insignia), who, _now()))
        conn.commit()
    finally:
        conn.close()
    return int(insignia), 0


def store_accounts(path=None):
    """Every account key with at least one character."""
    try:
        conn = connect(path)
    except sqlite3.Error:
        return []
    try:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT account FROM character ORDER BY account")]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def count(path=None):
    """How many characters are on file, across all accounts."""
    try:
        conn = connect(path)
    except sqlite3.Error:
        return 0
    try:
        return conn.execute("SELECT COUNT(*) FROM character").fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# the one-shot migration off the JSON store
# --------------------------------------------------------------------------- #
def import_json(json_path, path=None, force=False):
    """Copy `fmo_characters.json` in. Returns (accounts, characters).

    REFUSES a database that already holds characters unless `force` -- the
    import runs automatically on first use, and an import that overwrote live
    rows with a stale file would be indistinguishable from data loss. The JSON
    file is never modified or deleted: it stays as the backup.
    """
    if not json_path or not os.path.exists(json_path):
        return 0, 0
    if not force and count(path):
        return 0, 0
    try:
        with open(json_path, encoding="utf-8") as fh:
            everyone = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"[fmostore] cannot read {json_path}: {e!r}")
        return 0, 0
    if not isinstance(everyone, dict):
        return 0, 0
    accounts = chars = 0
    for account, roster in sorted(everyone.items()):
        if not roster:
            continue
        if save_roster(account, roster, path):
            accounts += 1
            chars += len(roster)
    return accounts, chars


# --------------------------------------------------------------------------- #
# the flag block -- 256 bytes, bits AND byte values
# --------------------------------------------------------------------------- #
FLAGS_LEN = 0x100


def parse_flag_spec(spec):
    """'8,9,128=99' -> ([8, 9], {128: 99}).

    THE ONE GRAMMAR for the progress block, shared with fmo.py's
    FMO_STATUS_FLAGS so the knob and this CLI cannot drift apart. Plain ids set
    a BIT (natives 0xE066 / the LEV row gate read bits); `idx=val` sets a whole
    BYTE (0xE067 returns the byte, and SE's "done" marker is the value 99).
    Pure, and it raises ValueError -- the caller decides whether that is a
    SystemExit or a usage message.
    """
    bits, bytes_ = [], {}
    for e in (spec or "").replace(" ", "").split(","):
        if not e:
            continue
        if "=" in e:
            k, _, v = e.partition("=")
            idx, val = int(k, 0), int(v, 0)
            if not 0 <= idx < FLAGS_LEN or not 0 <= val <= 255:
                raise ValueError(f"flag entry {e!r}: the byte index must be "
                                 f"0..255 and the value 0..255")
            bytes_[idx] = val
        else:
            bits.append(int(e, 0))
    return bits, bytes_


def flags_block(bits=(), byte_values=None, base=None):
    """Compile bit ids and `index=value` bytes into the 256-byte block.

    `base` (hex or bytes) is the block to start from, so this doubles as the
    writer: `flags_block(bits=[9], base=stored)` sets one more bit without
    disturbing what is already there. A byte VALUE wins over bits aimed at the
    same byte -- that is the precedence status_fields() already logs, kept here
    so the compiled block and the knob path cannot disagree.
    """
    if isinstance(base, str):
        base = bytes.fromhex(base)
    b = bytearray(base or bytes(FLAGS_LEN))
    if len(b) < FLAGS_LEN:
        b.extend(bytes(FLAGS_LEN - len(b)))
    del b[FLAGS_LEN:]
    for fid in bits or ():
        if not 0 <= fid < FLAGS_LEN * 8:
            raise ValueError("script flag id %d is outside the 256-byte "
                             "kind-11 bitmap (0..2047)" % fid)
        b[fid >> 3] |= 1 << (fid & 7)
    for idx, val in sorted((byte_values or {}).items()):
        if not 0 <= idx < FLAGS_LEN or not 0 <= val <= 255:
            raise ValueError("script flag byte %r=%r: the index must be "
                             "0..255 and the value 0..255" % (idx, val))
        b[idx] = val
    return bytes(b)


def flags_hex(bits=(), byte_values=None, base=None):
    return flags_block(bits, byte_values, base).hex()


def flags_bytes(hexstr):
    """A stored `flags` hex string back to 256 bytes; b'' when unusable."""
    if not hexstr:
        return b""
    try:
        b = bytes.fromhex(hexstr)
    except ValueError:
        return b""
    return b[:FLAGS_LEN].ljust(FLAGS_LEN, b"\0") if b else b""


def set_flag_byte(rec, index, value):
    """Set one byte of a character's flag block IN THE RECORD. The caller
    still has to commit the roster -- this is the mutation, not the write."""
    rec["flags"] = flags_hex(byte_values={index: value},
                             base=rec.get("flags"))
    return rec["flags"]


def set_flag_bit(rec, flag_id):
    """Set one BIT of a character's flag block in the record."""
    rec["flags"] = flags_hex(bits=[flag_id], base=rec.get("flags"))
    return rec["flags"]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
#: The keys `--seed` will fill. `flags` takes either raw hex or the
#: FMO_STATUS_FLAGS grammar above.
SEEDABLE = ("rank", "money", "mp", "contribution", "flags")


def seed_missing(values, path=None, account=None, overwrite=False):
    """Fill missing economy/progress keys on characters already on file.

    THE MIGRATION FOR PILOTS WHO PREDATE THE DATABASE. A character created
    before 2026-09-08 carries no economy keys, so it still falls back to the
    global knobs -- correct, but it does not yet OWN its numbers, which is the
    point of the change. This gives it a starting row.

    `overwrite=False` (the default) never touches a value that is already
    there: a pilot who has earned money must not be reset by an admin
    re-running this. Returns the number of characters changed.
    """
    changed = 0
    for acct in ([account] if account else store_accounts(path)):
        roster = load_roster(acct, path)
        touched = False
        for c in roster:
            for k, v in values.items():
                if overwrite or k not in c:
                    c[k] = v
                    touched = True
        if touched:
            save_roster(acct, roster, path)
            changed += len(roster)
    return changed


def _seed_cli(args, path):
    values, overwrite = {}, False
    account = None
    for a in args:
        if a == "--force":
            overwrite = True
            continue
        if a.startswith("--account="):
            account = a.split("=", 1)[1]
            continue
        k, _, v = a.partition("=")
        if k not in SEEDABLE:
            raise SystemExit(f"--seed: {k!r} is not seedable; try "
                             f"{', '.join(SEEDABLE)}")
        if k == "flags":
            try:
                values[k] = (v if all(ch in "0123456789abcdefABCDEF" for ch in v)
                             and len(v) >= 16
                             else flags_hex(*parse_flag_spec(v)))
            except ValueError as e:
                raise SystemExit(f"--seed: {e}")
        else:
            values[k] = int(v, 0)
    if not values:
        raise SystemExit("--seed wants at least one `<key>=<value>`, e.g. "
                         "`--seed rank=21 money=12345 flags=128=99`")
    n = seed_missing(values, path, account, overwrite)
    # The flag block is 512 characters of hex; echoing it back would bury the
    # numbers that matter. Show it the way --show does: the bytes that are set.
    shown = dict(values)
    if "flags" in shown:
        shown["flags"] = ",".join(
            "%d=%d" % (i, v) for i, v in enumerate(flags_bytes(shown["flags"]))
            if v) or "(all zero)"
    print(f"seeded {n} character(s) in {path} with "
          + ", ".join(f"{k}={v}" for k, v in sorted(shown.items()))
          + ("" if overwrite else " (existing values left alone; --force "
                                 "overwrites)"))


def _show(path):
    print(f"database: {os.path.abspath(path)}")
    if not os.path.exists(path):
        print("  (does not exist yet)")
        return
    for account in store_accounts(path):
        print(f"  {account}")
        for c in load_roster(account, path):
            fl = flags_bytes(c.get("flags"))
            marks = ",".join("%d=%d" % (i, v) for i, v in enumerate(fl) if v)
            print("    id %-3s %-17s %-17s rank=%-4s H$=%-9s MP=%-6s "
                  "contrib=%-6s map=%s"
                  % (c.get("id"), c.get("first", ""), c.get("last", ""),
                     c.get("rank", "-"), c.get("money", "-"),
                     c.get("mp", "-"), c.get("contribution", "-"),
                     c.get("mapno", "-")))
            if marks:
                print(f"         flags: {marks}")


def _selftest():
    import tempfile
    ok = True
    db = os.path.join(tempfile.mkdtemp(prefix="fmostore"), "fmo.db")

    def check(label, cond):
        nonlocal ok
        print(f"  {label}: {'OK' if cond else 'FAIL'}")
        ok &= bool(cond)

    print("fmostore selftest")
    rec = {"id": 1, "first": "Lex", "last": "Arden", "nation": 2, "sex": 1,
           "cls": 3, "hangar_pw": 1111, "appearance": "03056e00",
           "gender": 2, "nation_byte": 2, "personality": 4, "size": 3,
           "build": 5, "face": 110, "raw": "aabb",
           # keys with no column, and a bool: these must survive via `extra`
           "nickname": "Lex", "tutorial_seen": True, "notes": [1, 2, 3]}
    check("empty roster", load_roster("member:3", db) == [])
    check("save", save_roster("member:3", [rec], db))
    got = load_roster("member:3", db)
    check("round trip is byte-identical", got == [rec])
    check("no cross-account bleed", load_roster("member:9", db) == [])
    check("store_accounts", store_accounts(db) == ["member:3"])

    # a stored ZERO must not read back as absent -- _econ_value depends on it
    rec2 = dict(rec, money=0)
    save_roster("member:3", [rec2], db)
    check("a stored 0 survives as 0", load_roster("member:3", db)[0]["money"] == 0)
    rec3 = dict(rec)
    save_roster("member:3", [rec3], db)
    check("an ABSENT key stays absent",
          "money" not in load_roster("member:3", db)[0])

    # order is the roster order, not the id order
    save_roster("member:3", [dict(rec, id=7), dict(rec, id=2)], db)
    check("roster order is preserved",
          [c["id"] for c in load_roster("member:3", db)] == [7, 2])
    save_roster("member:3", [], db)
    check("empty save clears the account", load_roster("member:3", db) == [])
    check("and takes it out of store_accounts", store_accounts(db) == [])

    # created_at survives a rewrite; updated_at moves
    save_roster("member:3", [rec], db)
    conn = connect(db)
    born = conn.execute("SELECT created_at FROM character").fetchone()[0]
    conn.close()
    time.sleep(0.01)
    save_roster("member:3", [dict(rec, first="Renamed")], db)
    conn = connect(db)
    row = conn.execute("SELECT created_at, updated_at FROM character").fetchone()
    conn.close()
    check("created_at survives a rewrite", row[0] == born)

    # the flag block
    b = flags_block(bits=[173, 183])
    check("flag bits 173/183 -> byte 0x15 bit 5, byte 0x16 bit 7",
          b[0x15] == 0x20 and b[0x16] == 0x80)
    b = flags_block(bits=[8], byte_values={128: 99})
    check("byte 128 = 99 (SE's 'pilot registered')", b[128] == 99 and b[1] == 1)
    b = flags_block(byte_values={1: 5}, base=flags_hex(bits=[8]))
    check("a byte value overrides a bit in the same byte", b[1] == 5)
    b = flags_block(bits=[9], base=b.hex())
    check("a later bit does not disturb the rest", b[1] == 5 | 2)
    r = {}
    set_flag_byte(r, 128, 99)
    set_flag_bit(r, 8)
    check("set_flag_* compose in a record",
          flags_bytes(r["flags"])[128] == 99 and flags_bytes(r["flags"])[1] == 1)
    try:
        flags_block(bits=[9999])
        check("an out-of-range flag id raises", False)
    except ValueError:
        check("an out-of-range flag id raises", True)

    check("parse_flag_spec is the one grammar",
          parse_flag_spec("8, 9,128=99") == ([8, 9], {128: 99}))
    try:
        parse_flag_spec("300=1")
        check("an out-of-range flag byte raises", False)
    except ValueError:
        check("an out-of-range flag byte raises", True)

    # the seed migration for pilots who predate the database
    save_roster("member:3", [], db)             # start from a clean account set
    save_roster("member:8", [{"id": 1, "first": "Old", "money": 42},
                             {"id": 2, "first": "New"}], db)
    save_roster("member:9", [{"id": 1, "first": "Other"}], db)
    n = seed_missing({"rank": 21, "money": 500}, db, account="member:8")
    got = load_roster("member:8", db)
    check("--seed fills only what is MISSING",
          n == 2 and got[0]["money"] == 42 and got[1]["money"] == 500
          and got[0]["rank"] == 21 and got[1]["rank"] == 21)
    check("an --account seed touches only that account",
          "rank" not in load_roster("member:9", db)[0])
    seed_missing({"money": 500}, db, account="member:8", overwrite=True)
    check("--force overwrites",
          load_roster("member:8", db)[0]["money"] == 500)
    check("and with no --account it covers every account on file",
          seed_missing({"contribution": 0}, db) == 3)
    save_roster("member:8", [], db)
    save_roster("member:9", [], db)

    # the JSON import
    jp = os.path.join(os.path.dirname(db), "chars.json")
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump({"member:5": [{"id": 1, "first": "Old", "last": "Save"}],
                   "member:6": []}, fh)
    save_roster("member:3", [], db)
    n_a, n_c = import_json(jp, db)
    check("import brings the JSON store in", (n_a, n_c) == (1, 1))
    check("and skips accounts with no characters",
          store_accounts(db) == ["member:5"])
    check("a second import is refused (rows exist)", import_json(jp, db) == (0, 0))
    check("the JSON file is left alone", os.path.exists(jp))

    print("ALL OK" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
    elif args[0] == "--selftest":
        raise SystemExit(_selftest())
    elif args[0] == "--show":
        _show(args[1] if len(args) > 1 else DB_PATH)
    elif args[0] == "--import":
        if len(args) < 2:
            raise SystemExit("--import wants the fmo_characters.json path")
        db = args[2] if len(args) > 2 and not args[2].startswith("-") else DB_PATH
        a, c = import_json(args[1], db, force="--force" in args)
        print(f"imported {c} character(s) for {a} account(s) into {db}"
              if c else "nothing imported (the database already holds "
                        "characters, or the file is empty) -- --force overrides")
    elif args[0] == "--seed":
        rest, db = [], DB_PATH
        for a in args[1:]:
            if a.startswith("--db="):
                db = a.split("=", 1)[1]
            else:
                rest.append(a)
        _seed_cli(rest, db)
    else:
        raise SystemExit(f"unknown option {args[0]!r}; try --help")
