"""The sector-win ledger: wins per nation per war-map tile, kept in the database."""
import json
import os
from .deps import fmodb
from .wirelog import log


#: FMO_MSN_ZONES="<row>:<zone>,..." -- the AREA a row is issued in. SE's All
#: Mission List shows "the missions issued for the whole area"
#: (topics/050704/mission-manual.html:86), and a row's +0x1F4 tile is only a
#: sector in ONE zone's grid (0x611C4A70 keeps row and column, nothing else),
#: so an authored row belongs to a zone. Unset (default) = every row in every
#: area, exactly as before; set, the op-9 query's own MapKind filters rows and
#: the accept snapshots the row's zone rather than the pilot's.
MSN_ZONES = os.environ.get("FMO_MSN_ZONES", "").strip()
SECTOR_WINS = {}               #: (zone, tile, nation) -> [unix win times]
#: FMO_SECTOR_WINS -- whether the ledger is kept in the database
#: (fmo_sector_win) so prod's daily 23:50 restart does not wipe a sector
#: mission in progress. '' or 0 = memory only; unset or anything else = the
#: database. Wins older than SECTOR_WINS_KEEP seconds (default two days) are
#: dropped on save.
SECTOR_WINS_STORE = (os.environ.get("FMO_SECTOR_WINS", "").strip() not in ("", "0")
                     if "FMO_SECTOR_WINS" in os.environ else True)
#: The JSON file the ledger was kept in before it moved into the database. It
#: is read once, into an empty table, and never written: an old FMO_SECTOR_WINS
#: path, else fmo_sector_wins.json in FMO_DATA_DIR (/data on prod).
_SW_ENV = os.environ.get("FMO_SECTOR_WINS", "").strip()
SECTOR_WINS_LEGACY = (_SW_ENV if _SW_ENV.endswith(".json") else
                      os.path.join(os.environ.get("FMO_DATA_DIR", "/data"),
                                   "fmo_sector_wins.json"))
SECTOR_WINS_KEEP = int(os.environ.get("FMO_SECTOR_WINS_KEEP", "").strip()
                       or "172800", 0)
_SW_LOADED = [None]            #: how many keys the first load found; None = not yet


def _sw_legacy(path):
    """{(zone, tile, nation): [times]} out of the old JSON file; bad keys are
    skipped, a missing or unreadable file is empty."""
    out = {}
    if not path or not os.path.exists(path):
        return out
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return out
    for k, times in (raw or {}).items() if isinstance(raw, dict) else ():
        try:
            z, t, nat = (int(x) for x in k.split(":"))
            out[(z, t, nat)] = sorted(int(x) for x in times)
        except (ValueError, TypeError, AttributeError):
            continue
    return out


def sector_wins_load(store=None, ledger=None):
    """Fill the ledger from the database. Returns how many keys were loaded;
    no database or an unreadable one is an empty ledger, never an error.
    An empty table is first filled from the old JSON file, once."""
    led = SECTOR_WINS if ledger is None else ledger
    store = SECTOR_WINS_STORE if store is None else store
    if not store:
        return 0
    if fmodb is None:
        log("[fmo] WARNING: sector wins are memory only: polcore (OpenLobby) "
            "is not importable")
        return 0
    try:
        fmodb.ready()
        db = fmodb.db
        with db.transaction(lock="fmo_sector_win") as conn:
            rows = db.query("SELECT zone, tile, nation, won_at FROM fmo_sector_win"
                            " ORDER BY won_at", conn=conn)
            if not rows:
                old = _sw_legacy(SECTOR_WINS_LEGACY)
                if old:
                    _sw_write(db, conn, old)
                    log(f"[fmo] sector wins: imported {len(old)} key(s) from "
                        f"{SECTOR_WINS_LEGACY} (the file is left as it was)")
                    rows = db.query("SELECT zone, tile, nation, won_at"
                                    " FROM fmo_sector_win ORDER BY won_at", conn=conn)
    except fmodb.ERRORS as e:
        log(f"[fmo] WARNING: sector wins not loaded ({e!r})")
        return 0
    got = {}
    for r in rows:
        got.setdefault((int(r["zone"]), int(r["tile"]), int(r["nation"])),
                       []).append(int(r["won_at"]))
    led.update(got)
    return len(got)


def _sw_write(db, conn, ledger):
    """Replace the table with `ledger` (inside the caller's transaction)."""
    db.execute("DELETE FROM fmo_sector_win", conn=conn)
    rows = [(z, t, n, w) for (z, t, n), times in ledger.items() for w in times]
    if rows:
        db.execute_many("INSERT INTO fmo_sector_win (zone, tile, nation, won_at)"
                        " VALUES (%s, %s, %s, %s)", rows, conn=conn)


def sector_wins_save(store=None, ledger=None, now=None):
    """Write the ledger (pruned to SECTOR_WINS_KEEP) in one transaction; False
    when the ledger is memory only or the write failed."""
    led = SECTOR_WINS if ledger is None else ledger
    store = SECTOR_WINS_STORE if store is None else store
    cut = int(servicerecord._now_unix(now)) - SECTOR_WINS_KEEP
    out = {}
    for key, times in list(led.items()):
        keep = [t for t in times if t >= cut]
        led[key] = keep
        if keep:
            out[key] = keep
    if not store or fmodb is None:
        return False
    try:
        fmodb.ready()
        db = fmodb.db
        with db.transaction(lock="fmo_sector_win") as conn:
            _sw_write(db, conn, out)
        return True
    except fmodb.ERRORS as e:
        log(f"[fmo] WARNING: sector wins not saved ({e!r})")
        return False


def _sw_ensure_loaded():
    """Load what the last process recorded, on first use, so a restart keeps
    sector missions (it was a file read at import; a database read waits
    until something needs the ledger)."""
    if _SW_LOADED[0] is None:
        _SW_LOADED[0] = 0               # once, whatever happens
        try:
            _SW_LOADED[0] = sector_wins_load()
        except Exception:                          # pragma: no cover
            pass


def _row_int_map(spec, what):
    """'<row>:<int>,...' -> {row_index: int}; a bad clause is dropped loudly."""
    out = {}
    for clause in (spec or "").split(","):
        clause = clause.strip()
        if not clause:
            continue
        try:
            row, val = clause.split(":", 1)
            out[int(row, 0)] = int(val, 0)
        except ValueError:
            log(f"[fmo] WARNING: {what}: ignoring malformed clause {clause!r} "
                f"(want row:value)")
    return out


def sector_win_record(zone, tile, nation, now=None, ledger=None):
    """One WIN by `nation` on (zone, tile), stamped `now`. Returns the key."""
    if ledger is None:
        _sw_ensure_loaded()
    led = SECTOR_WINS if ledger is None else ledger
    key = (int(zone or 0), int(tile or 0), int(nation or 0))
    led.setdefault(key, []).append(int(servicerecord._now_unix(now)))
    if ledger is None:
        sector_wins_save()
    return key


def sector_wins_between(zone, tile, nation, since, until, ledger=None):
    """How many wins by `nation` on (zone, tile) fell in [since, until] --
    inclusive at both ends: stamps are whole seconds, and a win in the
    accept's own second is after the accept."""
    if ledger is None:
        _sw_ensure_loaded()
    led = SECTOR_WINS if ledger is None else ledger
    key = (int(zone or 0), int(tile or 0), int(nation or 0))
    return sum(1 for t in led.get(key, ()) if int(since) <= t <= int(until))


# Called at run time only; imported last so that import cycles resolve.
from . import servicerecord  # noqa: E402
