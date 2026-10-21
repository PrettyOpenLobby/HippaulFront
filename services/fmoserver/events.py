"""SE's timed events that live in client data: the commemorative service medal."""
import os


# --------------------------------------------------------------------------- #
# THE COMMEMORATIVE SERVICE MEDAL (記念従軍章), topics/060428
# --------------------------------------------------------------------------- #
#: SE, 2006-04-28, for the first anniversary (service began 2005-05-12): an
#: insignia named "2nd Huffman Conflict", one per nation, sold for H$10 in the
#: Squadron Insignia shop list (Hangar > Coloring > Squadron Insignia > R2)
#: from 05-15 to 06-05, to everyone. It goes on a wanzer like any insignia but
#: "cannot be set as the squadron insignia".
#:
#: READ: the records are in the client's own insignia table (D31, our
#: fmo-insignia.tsv): id 142 "O.C.U.軍 第二次ハフマン紛争 従軍章" and id 447 the
#: U.S.N. one, both with nation flag 255 -- which is why the catalogue
#: extraction (fmo-cosmetics.tsv drops flag 255) never offered them. The shop
#: list (0x61032EF3) keeps every kind-4 catalogue row whose D31 record exists
#: and is not owned; it applies no nation test of its own, so the server's
#: catalogue decides, and both ids fit the owned-insignia bitmap (base 101,
#: 0x80 bytes: inventory.OWNED_PAINT_CATS).
#:
#: THE SQUADRON-INSIGNIA REFUSAL (2026-10-08). A squadron insignia is picked
#: from the 0x019F list the server serves (squadron.insignia_for) and
#: registered with 0x01C4 {u64 group, u32 id} (squadron.MSG_INSIGNIA_SET,
#: live 2026-09-09). Both ids carry flag 255, so before 10-08 the picker
#: offered them to either nation; insignia_for now drops them, and a 0x01C4
#: naming one is not stored (session.py).
MEDAL_KIND = 4                         # catalogue kind: insignia
MEDAL_IDS = {1: 142, 2: 447}           # nation -> D31 id
MEDAL_NAME = "2nd Huffman Conflict"
MEDAL_PRICE = 10                       # H$, topics/060428

#: FMO_SERVICE_MEDAL: 'MM-DD..MM-DD' = on sale between those days (UTC) every
#: year, both inclusive; '1' = always; '0' = never. Default (2026-10-08):
#: SE's own window, 05-15..06-05. SE sold it once, in 2006; repeating the
#: window every year is OURS (the service's anniversary falls in it).
MEDAL_DEFAULT = "05-15..06-05"
MEDAL_SPEC = os.environ.get("FMO_SERVICE_MEDAL", "").strip() or MEDAL_DEFAULT


def medal_window(spec, now=None):
    """True when the medal is on sale under `spec` at unix time `now`."""
    import time
    spec = (spec or "").strip()
    if spec in ("", "0"):
        return False
    if spec == "1":
        return True
    try:
        a, b = spec.split("..")
        lo = tuple(int(x) for x in a.split("-"))
        hi = tuple(int(x) for x in b.split("-"))
    except ValueError:
        return False
    t = time.gmtime(time.time() if now is None else now)
    d = (t.tm_mon, t.tm_mday)
    return lo <= d <= hi if lo <= hi else (d >= lo or d <= hi)


def medal_rows(spec=None, now=None):
    """{kind: [(id, nation_flag, english)]} rows to add to the catalogue now
    (cosmetics.COSMETICS shape), empty when the medal is not on sale."""
    if not medal_window(MEDAL_SPEC if spec is None else spec, now):
        return {}
    return {MEDAL_KIND: [(rid, nat, MEDAL_NAME) for nat, rid in sorted(MEDAL_IDS.items())]}


def is_medal(kind, rid):
    return kind == MEDAL_KIND and rid in MEDAL_IDS.values()


def with_medal(catalogue, spec=None, now=None):
    """`catalogue` plus the medal rows when on sale (a new dict; the input is
    not changed). The rows carry the nation as their flag, so cosmetics_for
    and buy_verdict apply their own nation rule to them."""
    extra = medal_rows(spec, now)
    if not extra:
        return catalogue
    out = {k: list(v) for k, v in catalogue.items()}
    for k, rows in extra.items():
        have = {r[0] for r in out.get(k, ())}
        out.setdefault(k, []).extend(r for r in rows if r[0] not in have)
    return out


# --------------------------------------------------------------------------- #
# EVENT BATTLES (SE's timed sector events), fmodata/fmo-event-battles.tsv
# --------------------------------------------------------------------------- #
#: KEY: WHAT SE'S EVENTS WERE (the archive, polnews 6291, topics 050630 and
#: 060308): staff-run battles on named sectors for a window of days, the same
#: for both nations, e.g. OPERATION HOLY NIGHT (2005-12-22 12:00 to 12-28
#: 12:00 JST): a big-machine hunt on Control Zone 01 (O.C.U. sector 20, U.S.N.
#: sector 23) and a special-forces challenge on Control Zone 10 at NPC rank 2
#: and 3, paid with event-only items at Lv10 / Lv20. The 2005-06-28 update put
#: an event-battle icon on the map selector; the 2005-09-06 "Special Duty
#: List" is NOT an event list: it is the client's own Status tab (82:4, button
#: 0x1004 -> 0x610F6ED0(0xFFFF0000, kind 6) -> the zone's
#: tag_evb_accept_list0 script, SCP 0x804C / 0x804D), which reads the story
#: mission flag bytes the server already serves and sends nothing.
#:
#: THE TABLE: services/fmodata/fmo-event-battles.tsv, UTF-8, tab-separated.
#: The first line names the columns (any order; load_event_battles reads
#: them by name), then one row per (nation, sector):
#:   name           the event's name; the part before a ':' is what the
#:                  announce shows, so 'Holy Night:hunt' and 'Holy Night:sf'
#:                  are one event with two rows
#:   nation         0 = both nations, else the one nation it is for
#:   selector, row  the map selector and the ARE sector row on it;
#:                  fmosectors resolves the tile
#:   start, end     'MM-DD HH:MM' JST, every year (the year is OURS: SE ran
#:                  each once); an end before the start wraps over New Year
#:   enemy_level    optional, 0 when blank
#:   boss           optional, 1 = the large mobile weapon (only with
#:                  FMO_ENEMY_BOSS, since its frame is unread)
#:   bonus_hs, bonus_contrib
#:                  optional, paid on a WIN (OURS: SE's event items have no
#:                  id in our tables)
#:   note           optional free text
#: A row missing a required column or holding a bad number is skipped. The
#: file is not generated and not shipped; an admin writes it, e.g. (tabs
#: shown as <TAB>):
#:   name<TAB>nation<TAB>selector<TAB>row<TAB>start<TAB>end<TAB>enemy_level<TAB>boss<TAB>bonus_hs<TAB>bonus_contrib<TAB>note
#:   Holy Night:hunt<TAB>0<TAB>1<TAB>20<TAB>12-22 12:00<TAB>12-28 12:00<TAB>10<TAB>1<TAB>500<TAB>50<TAB>big-machine hunt
#: and restarts fmo. FMO_EVENT_BATTLES=0 turns the table off, a path names
#: another file. While an event is on, the 0x014B announce names it at world
#: entry.
EVENT_TSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "fmodata", "fmo-event-battles.tsv")
EVENT_SPEC = os.environ.get("FMO_EVENT_BATTLES", "").strip()
JST = 9 * 3600


def _mmdd(text):
    """'12-22 12:00' -> (12, 22, 12, 0)."""
    d, _, t = (text or "").strip().partition(" ")
    mo, dd = (int(x) for x in d.split("-"))
    hh, mi = (int(x) for x in (t or "00:00").split(":"))
    return mo, dd, hh, mi


def load_event_battles(path=None):
    """[row] from the event table; [] when off or absent. A bad row is
    skipped (and named in the row list's "errors")."""
    if EVENT_SPEC == "0" and path is None:
        return []
    p = path or EVENT_SPEC or EVENT_TSV
    try:
        f = open(p, encoding="utf-8")
    except OSError:
        return []
    out = []
    with f:
        cols = f.readline().rstrip("\r\n").split("\t")
        for line in f:
            r = dict(zip(cols, line.rstrip("\r\n").split("\t")))
            try:
                out.append({"name": r["name"], "nation": int(r["nation"]),
                            "selector": int(r["selector"]), "row": int(r["row"]),
                            "start": _mmdd(r["start"]), "end": _mmdd(r["end"]),
                            "enemy_level": int(r.get("enemy_level") or 0),
                            "boss": int(r.get("boss") or 0) != 0,
                            "bonus_hs": int(r.get("bonus_hs") or 0),
                            "bonus_contrib": int(r.get("bonus_contrib") or 0),
                            "note": r.get("note", "")})
            except (KeyError, ValueError):
                continue
    return out


EVENT_BATTLES = load_event_battles()


def event_on(ev, now=None):
    """True when event row `ev` is running at unix `now` (its window, JST,
    in the current year; a window across New Year wraps)."""
    import time
    t = time.gmtime((time.time() if now is None else float(now)) + JST)
    cur = (t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min)
    lo, hi = tuple(ev["start"]), tuple(ev["end"])
    return lo <= cur < hi if lo <= hi else (cur >= lo or cur < hi)


def event_tile(ev, sectors=None):
    """The tile of event row `ev`'s (selector, row), or None."""
    if sectors is None:
        try:
            import fmosectors
            sectors = fmosectors.SECTORS
        except ImportError:
            return None
    for tile, v in (sectors.get(ev["selector"]) or {}).items():
        if v[0] == ev["row"]:
            return int(tile)
    return None


def event_for(tile, nation=None, now=None, rows=None, sectors=None):
    """The running event battle on `tile` for `nation` (None = either), or None."""
    if tile is None:
        return None
    for ev in (EVENT_BATTLES if rows is None else rows):
        if nation is not None and ev["nation"] not in (0, nation):
            continue
        if event_on(ev, now) and event_tile(ev, sectors) == int(tile):
            return ev
    return None


def running_events(now=None, rows=None):
    """The names of the events running at `now`, once each, in table order."""
    out = []
    for ev in (EVENT_BATTLES if rows is None else rows):
        if event_on(ev, now) and ev["name"].split(":")[0] not in out:
            out.append(ev["name"].split(":")[0])
    return out
