#!/usr/bin/env python3
"""fmo_import_test.py -- `python fmodb.py import fmo_db FILE` and
`import board_state FILE|DIR`: what an earlier release kept in files, into
PostgreSQL.

    python tools/fmo_import_test.py

The sources are written by the code that wrote them, taken from git
(OLD_COMMIT, the last commit before the move to PostgreSQL) and run from a
temporary directory: the SQLite fmostore.py builds fmo.db through its own
save_roster() and set_squadron_insignia(), and the file-based polboards.py
writes fmo_discord.json and discord_channels.json through its Discord class
and _note_channel(). SQLite kept whatever a record held, so the pilots carry
a float, text in a number column and keys with no column, and a row no
version could map is added by hand, as a hand edit could leave one.

Checked on a throwaway database: the counts, every pilot read back through
the new fmostore.load_roster() equal to what the old one read, the insignia,
the board rows, a second run that changes nothing, --dry-run, the refusal on
a table that already holds rows, --merge, and a source that is byte for byte
what it was.

SKIPs without a test database (Docker or POL_TEST_DATABASE_URL);
POL_TEST_REQUIRE_DB=1 makes that a failure.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir))
SERVICES = os.path.join(ROOT, "services")
sys.path.insert(0, SERVICES)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

#: The last commit whose pilot database was SQLite and whose board kept its
#: Discord state in files (the `game-split` branch).
OLD_COMMIT = "7e2ffb63bd670f72e934945a16447cb1a0ce61e1"
OLD_FILES = ("services/fmostore.py", "services/polboards.py",
             "services/polgateway.py")

bad = []


def check(label, cond, detail=""):
    print("  %-72s %s%s" % (label, "PASS" if cond else "FAIL",
                            ("  " + str(detail)[:600]) if detail and not cond else ""),
          flush=True)
    if not cond:
        bad.append(label)


FIXTURE = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
dbpath, state = sys.argv[2], sys.argv[3]
os.environ["POL_BOARDS_STATE_DIR"] = state
import fmostore as S
FLAGS = "00" * 128 + "63" + "00" * 127
S.save_roster("member:3", [
    {"id": 1, "first": "Dan", "last": "Kaul", "nation": 1, "sex": 0, "gender": 0,
     "cls": 2, "rank": 21, "money": 12345, "mp": 67, "contribution": 0,
     "flags": FLAGS, "mapno": 3, "mapkind": 1, "pos": "10,20,30,0",
     "resume_w7604": 0, "setups": "abcd", "raw": "0102",
     "hangar_colour": [1, 2, 3], "tutorial_done": True},
    {"id": 4, "first": "Lyn", "last": "Tan", "money": 12.5, "rank": "none yet",
     "appearance": "ff00"},
], dbpath)
S.save_roster("addr:198.51.100.7", [{"id": 2, "first": "Ghost", "money": 0}], dbpath)
S.set_squadron_insignia(3, 131, who="member:3", path=dbpath)
S.set_squadron_insignia(9, 7, who=None, path=dbpath)
out = {a: S.load_roster(a, dbpath) for a in S.store_accounts(dbpath)}

import polboards as B
class Net:
    def __call__(self, req, timeout=None):
        class R:
            status = 200
            def read(self, *a): return b'{"id": "777"}'
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return R()
d = B.Discord("fmo", "https://discord.com/api/webhooks/1/x",
              os.path.join(state, "fmo_discord.json"), opener=Net())
d.tick("s1", lambda: ({"content": "board"}, []), now=1000.0)
B._note_channel("chosen", "fmo", "4242", guild="99")
print(json.dumps(out))
'''


def old_sources(base):
    probe = subprocess.run(["git", "cat-file", "-e", OLD_COMMIT + "^{commit}"],
                           cwd=ROOT, capture_output=True)
    if probe.returncode != 0:
        print("FAIL: this suite needs the pre-PostgreSQL code at %s (a shallow "
              "clone lacks it: git fetch --unshallow)" % OLD_COMMIT)
        sys.exit(1)
    code = os.path.join(base, "old")
    os.makedirs(code)
    for f in OLD_FILES:
        src = subprocess.run(["git", "show", "%s:%s" % (OLD_COMMIT, f)], cwd=ROOT,
                             capture_output=True, check=True).stdout
        with open(os.path.join(code, os.path.basename(f)), "wb") as fh:
            fh.write(src)
    with open(os.path.join(base, "fixture.py"), "w", encoding="utf-8") as fh:
        fh.write(FIXTURE)
    dbpath = os.path.join(base, "data", "fmo.db")
    state = os.path.join(base, "state")
    os.makedirs(os.path.dirname(dbpath))
    env = {k: v for k, v in os.environ.items() if not k.startswith("POL_DATABASE")}
    p = subprocess.run([sys.executable, os.path.join(base, "fixture.py"), code,
                        dbpath, state], env=env, capture_output=True, text=True,
                       encoding="utf-8")
    if p.returncode != 0:
        print(p.stdout + p.stderr)
        print("FAIL: the old code could not write its files")
        sys.exit(1)
    rosters = json.loads(p.stdout.strip().splitlines()[-1])
    # what no version could map: bytes in a text column, and a value that
    # must move into an `extra` that is not a JSON object
    c = sqlite3.connect(dbpath)
    c.execute("INSERT INTO character (account, id, slot_ord, \"first\", created_at,"
              " updated_at) VALUES ('member:8', 1, 0, X'00FF', 'x', 'x')")
    c.execute("INSERT INTO character (account, id, slot_ord, money, extra, created_at,"
              " updated_at) VALUES ('member:8', 2, 1, 1.5, '[1]', 'x', 'x')")
    c.commit()
    c.close()
    with open(os.path.join(state, "notes.txt"), "w") as fh:
        fh.write("not state")
    with open(os.path.join(state, "old_discord.json"), "w") as fh:
        fh.write("[1, 2]")
    return dbpath, state, rosters


def digest(root):
    out = {}
    for d, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = (hashlib.sha256(fh.read()).hexdigest(),
                                                 os.stat(p).st_mtime_ns)
    return out


def run(url, *args):
    p = subprocess.run([sys.executable, os.path.join(SERVICES, "fmodb.py"), "import"]
                       + list(args), cwd=SERVICES,
                       env=dict(os.environ, POL_DATABASE_URL=url),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout + p.stderr


def main():
    import fmodb
    with fmodb.test_database() as url:
        if url is None:
            print("[fmo_import_test] SKIP -- no test database")
            return 0
        base = tempfile.mkdtemp(prefix="fmo-import-")
        try:
            _main(fmodb, url, base)
        finally:
            shutil.rmtree(base, ignore_errors=True)
    print("[fmo_import_test] %s" % ("FAIL: %d check(s)" % len(bad) if bad else "OK"))
    return 1 if bad else 0


def fingerprint(db):
    out = {}
    for t in ("fmo_character", "fmo_squadron_insignia", "fmo_board_state"):
        out[t] = sorted(repr(sorted((k, str(v)) for k, v in r.items()))
                        for r in db.query("SELECT * FROM %s" % t))
    return out


def _main(fmodb, url, base):
    import fmostore
    db = fmodb.db
    dbpath, state, rosters = old_sources(base)
    before = digest(base)
    count = lambda t: db.query_one("SELECT count(*) AS n FROM %s" % t)["n"]  # noqa: E731

    print("--dry-run: a report and nothing written")
    code, out = run(url, "fmo_db", dbpath, "--dry-run")
    check("exit 0", code == 0, out)
    check("says so", "Dry run: nothing was written." in out, out)
    check("plans three pilots and two insignia",
          "3 read, 0 in the table, 0 already there, 3 to insert" in out
          and "2 read, 0 in the table, 0 already there, 2 to insert" in out, out)
    check("nothing written", count("fmo_character") == 0
          and count("fmo_squadron_insignia") == 0)

    print("import fmo_db")
    code, out = run(url, "fmo_db", dbpath)
    check("exit 0", code == 0, out)
    check("the rows no version could map are named and skipped",
          "skipped character rowid" in out and "first holds bytes" in out
          and "extra is not a JSON object" in out, out)
    check("three pilots, two insignia",
          (count("fmo_character"), count("fmo_squadron_insignia")) == (3, 2))
    for acct, roster in sorted(rosters.items()):
        got = json.loads(json.dumps(fmostore.load_roster(acct)))
        check("%s: the roster reads back as the old code read it" % acct,
              got == roster, "%r != %r" % (got, roster))
    lyn = db.query_one("SELECT money, rank, extra, slot_ord FROM fmo_character"
                       " WHERE account = 'member:3' AND id = 4")
    check("a float and text in number columns ride in extra",
          lyn["money"] is None and lyn["rank"] is None
          and json.loads(lyn["extra"]) == {"money": 12.5, "rank": "none yet"}, lyn)
    check("roster order kept (slot_ord)", lyn["slot_ord"] == 1)
    dan = db.query_one("SELECT * FROM fmo_character WHERE account = 'member:3' AND id = 1")
    check("columns copied", (dan["first"], dan["money"], dan["flags"][256:258], dan["pos"])
          == ("Dan", 12345, "63", "10,20,30,0"), dan)
    check("created_at and updated_at carried over",
          dan["created_at"].endswith("Z") and dan["updated_at"].endswith("Z"), dan)
    check("the insignia", fmostore.squadron_insignia(3) == 131
          and db.query_one("SELECT set_by FROM fmo_squadron_insignia WHERE group_id = 3")
          ["set_by"] == "member:3")

    print("import board_state")
    code, out = run(url, "board_state", state)
    check("exit 0", code == 0, out)
    rows = {r["name"]: r["data"] for r in db.query("SELECT name, data FROM fmo_board_state")}
    check("fmo_discord and discord_channels rows", sorted(rows) ==
          ["discord_channels", "fmo_discord"], sorted(rows))
    check("the message id is the one the old board posted",
          rows.get("fmo_discord", {}).get("message_id") == "777", rows)
    check("the chosen channel, per guild",
          rows.get("discord_channels", {}).get("chosen") == {"fmo": {"99": "4242"}}, rows)
    check("a file that is not board state, and one that is not an object, are skipped",
          "skipped notes.txt" in out and "skipped old_discord.json" in out, out)
    import polboards
    os.environ["POL_DATABASE_URL"] = url
    try:
        check("the board reads the imported row",
              polboards.Discord("fmo", "https://discord.com/api/webhooks/1/x",
                                "db:fmo_discord").msg_id == "777")
        check("and the imported channels", polboards.bot_channels()["chosen"]
              == {"fmo": {"99": "4242"}})
    finally:
        os.environ.pop("POL_DATABASE_URL", None)

    print("a second run changes nothing")
    fp = fingerprint(db)
    for args in (("fmo_db", dbpath), ("board_state", state)):
        code, out = run(url, *args)
        check("%s: exit 0, nothing to import" % args[0],
              code == 0 and "Nothing to import" in out, out)
        code, out = run(url, *(args + ("--merge",)))
        check("%s --merge: nothing either" % args[0],
              code == 0 and "Nothing to import" in out, out)
    check("every row as it was", fingerprint(db) == fp)

    print("a table that already holds rows: refused, then --merge")
    db2 = os.path.join(base, "fmo2.db")
    shutil.copyfile(dbpath, db2)
    c = sqlite3.connect(db2)
    c.execute("INSERT INTO character (account, id, slot_ord, \"first\", money,"
              " created_at, updated_at) VALUES ('member:5', 1, 0, 'New', 5, 'a', 'b')")
    c.execute("UPDATE character SET money = 1 WHERE account = 'member:3' AND id = 1")
    c.commit()
    c.close()
    code, out = run(url, "fmo_db", db2)
    check("refused: exit 2", code == 2, out)
    check("says why", "REFUSED: fmo_character" in out and "--merge" in out, out)
    check("nothing written", fingerprint(db) == fp)
    code, out = run(url, "fmo_db", db2, "--merge", "--dry-run")
    check("--merge --dry-run writes nothing", code == 0 and fingerprint(db) == fp, out)
    code, out = run(url, "fmo_db", db2, "--merge")
    check("--merge: exit 0, one pilot", code == 0 and "Done: 1 row(s) written." in out, out)
    check("the new pilot is in", [r["first"] for r in fmostore.load_roster("member:5")] == ["New"])
    check("a pilot in both keeps the table's row", db.query_one(
        "SELECT money FROM fmo_character WHERE account = 'member:3' AND id = 1")["money"] == 12345)
    check("and the report names it",
          "kept the table's row, the source's differs: ('member:3', 1)" in out, out)

    print("bad sources")
    code, out = run(url, "fmo_db", os.path.join(base, "missing.db"))
    check("a missing file: exit 1", code == 1 and "does not exist" in out, out)
    other = os.path.join(base, "other.db")
    sqlite3.connect(other).execute("CREATE TABLE t (x)").connection.close()
    code, out = run(url, "fmo_db", other)
    check("a SQLite file that is not fmo.db: exit 1", code == 1 and "is it fmo.db" in out, out)
    os.remove(other)
    os.remove(db2)
    check("the sources are byte for byte what they were, and no journal was made",
          digest(base) == before, sorted(set(digest(base)) ^ set(before)))


if __name__ == "__main__":
    sys.exit(main())
