"""The sector-win ledger: wins per nation per war-map tile, kept on disk."""
import json
import os
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
#: FMO_SECTOR_WINS -- where the ledger is kept so prod's daily 23:50 restart
#: does not wipe a sector mission in progress. '' = memory only. Default:
#: fmo_sector_wins.json beside the character DB (/data on prod). Wins older
#: than SECTOR_WINS_KEEP seconds (default two days) are dropped on save.
SECTOR_WINS_PATH = (os.environ.get("FMO_SECTOR_WINS", "").strip()
                    if "FMO_SECTOR_WINS" in os.environ else
                    os.path.join(os.environ.get("FMO_DATA_DIR", "/data"),
                                 "fmo_sector_wins.json"))
SECTOR_WINS_KEEP = int(os.environ.get("FMO_SECTOR_WINS_KEEP", "").strip()
                       or "172800", 0)


def sector_wins_load(path=None, ledger=None):
    """Fill the ledger from its file. Returns how many keys were loaded; a
    missing or unreadable file is an empty ledger, never an error."""
    led = SECTOR_WINS if ledger is None else ledger
    path = SECTOR_WINS_PATH if path is None else path
    if not path or not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return 0
    n = 0
    for k, times in (raw or {}).items():
        try:
            z, t, nat = (int(x) for x in k.split(":"))
            led[(z, t, nat)] = sorted(int(x) for x in times)
            n += 1
        except (ValueError, TypeError):
            continue
    return n


def sector_wins_save(path=None, ledger=None, now=None):
    """Write the ledger (pruned to SECTOR_WINS_KEEP) atomically; False when
    there is no path or the write failed."""
    led = SECTOR_WINS if ledger is None else ledger
    path = SECTOR_WINS_PATH if path is None else path
    if not path:
        return False
    cut = int(servicerecord._now_unix(now)) - SECTOR_WINS_KEEP
    out = {}
    for key, times in list(led.items()):
        keep = [t for t in times if t >= cut]
        led[key] = keep
        if keep:
            out["%d:%d:%d" % key] = keep
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(out, fh, sort_keys=True)
        os.replace(path + ".tmp", path)
        return True
    except OSError:
        return False


#: Load what the last process recorded, so a restart keeps sector missions.
try:
    _SW_LOADED = sector_wins_load()
except Exception:                                  # pragma: no cover
    _SW_LOADED = 0


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
    led = SECTOR_WINS if ledger is None else ledger
    key = (int(zone or 0), int(tile or 0), int(nation or 0))
    return sum(1 for t in led.get(key, ()) if int(since) <= t <= int(until))


# Called at run time only; imported last so that import cycles resolve.
from . import servicerecord  # noqa: E402
