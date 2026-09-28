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
account. The knobs stay, demoted to what they always should have been -- the
SEED for a character that has no stored value, and an admin override for a
probe.

WHERE IT LIVES. The stack's PostgreSQL database (POL_DATABASE_URL), in the
`fmo_character` and `fmo_squadron_insignia` tables. Their schema is this
repository's migration set (services/fmo_migrations/, see fmodb.py), applied
once per process on first use and again by the game service at start. Until
2026-09 this was a SQLite file of its own, fmo.db, kept apart from accounts.db
so a schema write landing on a container restart could not take the accounts
with it; PostgreSQL removes that hazard, and the tables keep their `fmo_`
names so they stay apart from OpenLobby's.

DROP-IN SHAPE. `load_roster` / `save_roster` / `store_accounts` return and take
exactly what the JSON store did: a list of plain dicts, oldest first. Every
call site is unchanged. Keys the table does not know about are kept verbatim
in an `extra` JSON column, so a decode that grows a field later cannot
silently drop it.

Run standalone (POL_DATABASE_URL names the database):
    python fmostore.py --selftest              # a throwaway database (pgtest)
    python fmostore.py --show                  # what is on file
    python fmostore.py --import <json>         # one-shot migration
    python fmostore.py --seed rank=21 money=12345 mp=67 flags=128=99 \\
                       [--account=member:3] [--force]

In the container: `docker compose exec fmo python /app/fmostore.py --show`.
"""
import json
import os
import sys
import threading
import time

import fmodb

db = fmodb.db

#: Kept for the tools that print where the pilots are.
DB_PATH = "the fmo_character table (POL_DATABASE_URL)"

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

#: The TEXT columns; every other column in COLUMNS is BIGINT.
TEXT_COLUMNS = frozenset((
    "first", "last", "appearance", "flags", "pos", "setups", "owned_parts",
    "raw", "raw_0177",
))

#: Columns the loader must NOT hand back as record keys.
_INTERNAL = ("account", "slot_ord", "extra", "created_at", "updated_at")

_INT64 = (-(1 << 63), (1 << 63) - 1)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------- #
# the connection
# --------------------------------------------------------------------------- #
_WRITE_LOCK = threading.Lock()


def ready():
    """The tables exist (this repository's migrations applied, once per
    process). Raises when the database cannot be used."""
    fmodb.ready()


# --------------------------------------------------------------------------- #
# record <-> row
# --------------------------------------------------------------------------- #
def _storable(k, v):
    """True when column `k` holds `v` and hands back the same value.

    Booleans are excluded ON PURPOSE: they would come back as 1/0, and a
    caller testing `is True` would break. They ride in `extra`, which is JSON
    and round-trips them exactly. So does any value whose type is not the
    column's (a str in an integer column, a number in a text column, a float,
    an integer past 64 bits): SQLite would have kept or converted those, and
    in `extra` they come back exactly as they were written.
    """
    if v is None:
        return True
    if isinstance(v, bool):
        return False
    if k in TEXT_COLUMNS:
        return isinstance(v, str)
    return isinstance(v, int) and _INT64[0] <= v <= _INT64[1]


def record_to_row(rec, account, slot_ord):
    """(column dict, extra dict) for one character record."""
    cols, extra = {}, {}
    for k, v in rec.items():
        if k in COLUMNS and _storable(k, v):
            cols[k] = v
        else:
            extra[k] = v
    cols["account"] = account
    cols["slot_ord"] = slot_ord
    cols["extra"] = json.dumps(extra, sort_keys=True) if extra else None
    return cols, extra


def row_to_record(row):
    """One row back to the plain dict the rest of the server expects.

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
# the API the game server calls
# --------------------------------------------------------------------------- #
def load_roster(account):
    """This account's characters, oldest first. [] when there are none."""
    try:
        ready()
        rows = db.query(
            "SELECT * FROM fmo_character WHERE account = %s"
            " ORDER BY slot_ord, id", (account,))
    except fmodb.ERRORS as e:
        print(f"[fmostore] load_roster({account!r}) failed: {e!r}")
        return []
    return [row_to_record(r) for r in rows]


def save_roster(account, roster):
    """Replace this account's list.

    Whole-list replace, like the JSON store it stands in for -- the callers
    mutate their in-memory roster and then commit it, and matching that
    contract exactly is what let this swap in without touching a call site.
    It is a DELETE + INSERT inside one transaction scoped to ONE account, so
    two accounts cannot clobber each other, and a crash mid-write leaves the
    previous state intact. The transaction holds an advisory lock named for
    the account: SQLite ran one writer at a time, and two processes saving
    the same account must still take turns.
    """
    with _WRITE_LOCK:
        try:
            ready()
            now = _now()
            with db.transaction(lock="fmostore.roster:" + account) as conn:
                born = {r["id"]: r["created_at"] for r in db.query(
                    "SELECT id, created_at FROM fmo_character WHERE account = %s",
                    (account,), conn=conn)}
                db.execute("DELETE FROM fmo_character WHERE account = %s",
                           (account,), conn=conn)
                for n, rec in enumerate(roster):
                    cols, _ = record_to_row(rec, account, n)
                    cols["created_at"] = born.get(cols.get("id")) or now
                    cols["updated_at"] = now
                    names = list(cols)
                    db.execute(
                        "INSERT INTO fmo_character (%s) VALUES (%s)"
                        % (",".join('"%s"' % c for c in names),
                           ",".join("%s" for _ in names)),
                        [cols[c] for c in names], conn=conn)
            return True
        except fmodb.ERRORS as e:
            print(f"[fmostore] save_roster({account!r}) failed: {e!r} -- "
                  f"{len(roster)} character(s) NOT written")
            return False


def squadron_insignia(group_id):
    """The insignia id registered for a POL group, or 0. Never raises."""
    if not group_id:
        return 0
    try:
        ready()
        row = db.query_one(
            "SELECT insignia FROM fmo_squadron_insignia WHERE group_id = %s",
            (int(group_id),))
    except Exception:
        return 0
    return int(row["insignia"]) if row else 0


def set_squadron_insignia(group_id, insignia, who=None):
    """Register a group's insignia. Returns (stored, previous).

    WARNING: SE treats this as ONE-TIME (86:21, "It cannot be changed afterwards"),
    and the client greys the menu row once it is set -- so a second attempt is
    the client disagreeing with us about state, not a normal edit. This keeps
    the FIRST value and reports the conflict rather than silently overwriting
    somebody's squadron emblem."""
    prev = squadron_insignia(group_id)
    if prev:
        return prev, prev
    ready()
    db.upsert("fmo_squadron_insignia",
              {"group_id": int(group_id), "insignia": int(insignia),
               "set_by": who, "set_at": _now()}, key="group_id")
    return int(insignia), 0


def store_accounts():
    """Every account key with at least one character."""
    try:
        ready()
        return [r["account"] for r in db.query(
            "SELECT DISTINCT account FROM fmo_character ORDER BY account")]
    except fmodb.ERRORS:
        return []


def count():
    """How many characters are on file, across all accounts."""
    try:
        ready()
        return db.query_one("SELECT COUNT(*) AS n FROM fmo_character")["n"]
    except fmodb.ERRORS:
        return 0


# --------------------------------------------------------------------------- #
# the one-shot migration off the JSON store
# --------------------------------------------------------------------------- #
def import_json(json_path, force=False):
    """Copy `fmo_characters.json` in. Returns (accounts, characters).

    REFUSES a database that already holds characters unless `force` -- the
    import runs automatically on first use, and an import that overwrote live
    rows with a stale file would be indistinguishable from data loss. The JSON
    file is never modified or deleted: it stays as the backup.
    """
    if not json_path or not os.path.exists(json_path):
        return 0, 0
    if not force and count():
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
        if save_roster(account, roster):
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


def seed_missing(values, account=None, overwrite=False):
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
    for acct in ([account] if account else store_accounts()):
        roster = load_roster(acct)
        touched = False
        for c in roster:
            for k, v in values.items():
                if overwrite or k not in c:
                    c[k] = v
                    touched = True
        if touched:
            save_roster(acct, roster)
            changed += len(roster)
    return changed


def _seed_cli(args):
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
    n = seed_missing(values, account, overwrite)
    # The flag block is 512 characters of hex; echoing it back would bury the
    # numbers that matter. Show it the way --show does: the bytes that are set.
    shown = dict(values)
    if "flags" in shown:
        shown["flags"] = ",".join(
            "%d=%d" % (i, v) for i, v in enumerate(flags_bytes(shown["flags"]))
            if v) or "(all zero)"
    print(f"seeded {n} character(s) in {DB_PATH} with "
          + ", ".join(f"{k}={v}" for k, v in sorted(shown.items()))
          + ("" if overwrite else " (existing values left alone; --force "
                                 "overwrites)"))


def _show():
    print(f"database: {DB_PATH}")
    for account in store_accounts():
        print(f"  {account}")
        for c in load_roster(account):
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
    with fmodb.test_database() as url:
        if url is None:
            print("fmostore selftest: SKIP (no test database)")
            return 0
        return _selftest_on(tempfile.mkdtemp(prefix="fmostore"))


def _selftest_on(tmp):
    ok = True

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
    check("empty roster", load_roster("member:3") == [])
    check("save", save_roster("member:3", [rec]))
    got = load_roster("member:3")
    check("round trip is byte-identical", got == [rec])
    check("no cross-account bleed", load_roster("member:9") == [])
    check("store_accounts", store_accounts() == ["member:3"])

    # a value whose type is not its column's comes back exactly as written
    odd = dict(rec, id=4, money="12", first=7, rank=1.5, mp=1 << 70, cls=False)
    save_roster("member:4", [odd])
    check("a mistyped value survives via `extra`", load_roster("member:4") == [odd])
    save_roster("member:4", [])

    # a stored ZERO must not read back as absent -- _econ_value depends on it
    rec2 = dict(rec, money=0)
    save_roster("member:3", [rec2])
    check("a stored 0 survives as 0", load_roster("member:3")[0]["money"] == 0)
    rec3 = dict(rec)
    save_roster("member:3", [rec3])
    check("an ABSENT key stays absent",
          "money" not in load_roster("member:3")[0])

    # order is the roster order, not the id order
    save_roster("member:3", [dict(rec, id=7), dict(rec, id=2)])
    check("roster order is preserved",
          [c["id"] for c in load_roster("member:3")] == [7, 2])
    save_roster("member:3", [])
    check("empty save clears the account", load_roster("member:3") == [])
    check("and takes it out of store_accounts", store_accounts() == [])

    # created_at survives a rewrite; updated_at moves
    save_roster("member:3", [rec])
    born = db.query_one("SELECT created_at FROM fmo_character")["created_at"]
    time.sleep(1.1)                 # the stamps are whole seconds
    save_roster("member:3", [dict(rec, first="Renamed")])
    row = db.query_one("SELECT created_at, updated_at FROM fmo_character")
    check("created_at survives a rewrite", row["created_at"] == born)
    check("updated_at moves", row["updated_at"] != born)

    # two writers on one account take turns: the roster ends as one of them,
    # never as both (a duplicate key) or neither
    errs = []

    def writer(tag):
        for i in range(10):
            if not save_roster("member:5", [{"id": 1, "first": tag},
                                            {"id": 2, "first": tag}]):
                errs.append(tag)
    ts = [threading.Thread(target=writer, args=(t,)) for t in ("A", "B")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    final = load_roster("member:5")
    check("concurrent saves of one account serialise",
          not errs and len(final) == 2 and final[0]["first"] == final[1]["first"])
    save_roster("member:5", [])

    # the squadron insignia is set once
    a, b0 = set_squadron_insignia(3, 131, "selftest")
    c, d = set_squadron_insignia(3, 999, "selftest")
    check("an insignia is registered once and a second one is refused",
          (a, b0, c, d) == (131, 0, 131, 131) and squadron_insignia(3) == 131
          and squadron_insignia(45) == 0)

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
    save_roster("member:3", [])             # start from a clean account set
    save_roster("member:8", [{"id": 1, "first": "Old", "money": 42},
                             {"id": 2, "first": "New"}])
    save_roster("member:9", [{"id": 1, "first": "Other"}])
    n = seed_missing({"rank": 21, "money": 500}, account="member:8")
    got = load_roster("member:8")
    check("--seed fills only what is MISSING",
          n == 2 and got[0]["money"] == 42 and got[1]["money"] == 500
          and got[0]["rank"] == 21 and got[1]["rank"] == 21)
    check("an --account seed touches only that account",
          "rank" not in load_roster("member:9")[0])
    seed_missing({"money": 500}, account="member:8", overwrite=True)
    check("--force overwrites",
          load_roster("member:8")[0]["money"] == 500)
    check("and with no --account it covers every account on file",
          seed_missing({"contribution": 0}) == 3)
    save_roster("member:8", [])
    save_roster("member:9", [])

    # the JSON import
    jp = os.path.join(tmp, "chars.json")
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump({"member:5": [{"id": 1, "first": "Old", "last": "Save"}],
                   "member:6": []}, fh)
    save_roster("member:3", [])
    n_a, n_c = import_json(jp)
    check("import brings the JSON store in", (n_a, n_c) == (1, 1))
    check("and skips accounts with no characters",
          store_accounts() == ["member:5"])
    check("a second import is refused (rows exist)", import_json(jp) == (0, 0))
    check("the JSON file is left alone", os.path.exists(jp))

    # a process with the database switched off degrades, it does not raise
    fmodb.configure(disabled=True)
    try:
        check("with no database: an empty roster, a refused save, no accounts",
              load_roster("member:5") == [] and save_roster("member:5", [rec]) is False
              and store_accounts() == [] and count() == 0
              and squadron_insignia(3) == 0)
    finally:
        fmodb.configure(disabled=False)

    print("ALL OK" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
    elif args[0] == "--selftest":
        raise SystemExit(_selftest())
    elif args[0] == "--show":
        _show()
    elif args[0] == "--import":
        if len(args) < 2:
            raise SystemExit("--import wants the fmo_characters.json path")
        a, c = import_json(args[1], force="--force" in args)
        print(f"imported {c} character(s) for {a} account(s) into {DB_PATH}"
              if c else "nothing imported (the database already holds "
                        "characters, or the file is empty) -- --force overrides")
    elif args[0] == "--seed":
        _seed_cli(args[1:])
    else:
        raise SystemExit(f"unknown option {args[0]!r}; try --help")
