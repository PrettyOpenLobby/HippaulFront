#!/usr/bin/env python3
"""fmolayout.py -- FMO's lobby NPC LAYOUT: the file the editor writes and the
world server pops from, plus the floor-plan geometry both lean on.

    python fmolayout.py --selftest

WHY A FILE AND NOT THE ENV. Until now a lobby's cast was one env string per
band (`FMO_UDP_POP_NPC`, `_OCC`, `_FZ`, `_COL`), parsed once at import. Moving
one person meant editing prod's .env and hand-recreating the container. The
web editor (fmodevtool.py) needs somewhere to write that the server reads
back WITHOUT a restart, so: one JSON file on the /data volume, re-read on its
mtime, one band per entry. A band the file does not carry keeps its env-derived
roster, so a fresh deploy with no file changes nothing.

THE GRAMMAR IS STILL THE ENV'S. Every row round-trips to the
`id[:type][@x,y,z,w][#typecode][=Name.Label]` spec the server has always
taken (to_spec / from_roster), so an admin can copy a layout back into
.env, and the editor can import what the env is serving today. The one
difference is the FACING: the file keeps DEGREES (the scripts' convention,
what the editor's compass shows), the wire wants signed radians. That
conversion, sign included, happens in exactly one place (face_to_wire /
face_from_wire) -- FMO_FACE_SIGN is unproved either way, and a layout stored
in degrees survives flipping it.

THE FLOOR. FMO has no minimap; the map container's world-space prop boxes
(tools/fmo_floorplans.py -> fmodata/floorplans/<mapno>.json) are the plan,
and the box under a click is the floor height a plan view otherwise lacks.
ground_at() takes the HIGHEST box top under (x, z) that is no more than
GROUND_ABOVE over the map's known floor -- on map 102 that picks the 3.50
console platform over the 3.0..3.15 floor tiles and ignores the 4.25 desk
tops, which is what a person standing there needs. No box = no ground, and
the editor refuses the spot (FE learned that a spot with no floor is worse
than a wrong one).
"""
import json
import math
import os
import re
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
PLAN_DIR = os.path.join(HERE, "fmodata", "floorplans")
#: `<repo>/data/fmo_npc_layout.json` -- same file on host and in the container
#: (services/ is bind-mounted at /app), same reasoning as fmo._default_store.
DEFAULT_PATH = os.path.join(HERE, os.pardir, "data", "fmo_npc_layout.json")

#: The bands the server pops a cast for, in editor order:
#:   (id, title, zone kinds, catalogue typecode range, description, PLACE KIND)
#: The four LOBBY bands are keyed by zone kind (fmo.roster_for); the five
#: PLACE bands (place kind 1..5 = Room, Briefing Room, Room B, Room C, Hangar,
#: fmo.PLACE_KIND_NAMES) are the side rooms Move -> Change Room / Hangar puts
#: you in. SE's guide says the training sergeant stands in a ROOM and the
#: hangar has its own staff (the event tables carry room_event / hanger_event
#: / tag_training_main keys), and the server popped NOBODY there because no
#: position was ever measured. A place band in the layout file is the first
#: way to put someone there; whether they render and talk in a room is the
#: live test it exists to run.
BANDS = (
    ("hq", "HQ lobby", (1, 3), (100, 137),
     "the zones 1xx (O.C.U.) and 3xx (U.S.N.) -- AI/F00/D87's people", 0),
    ("occ", "Occupation lobby", (2, 4), (100, 137),
     "zones 2xx / 4xx -- D87's people, mission briefings at the counters", 0),
    ("fz", "Frontline lobby", (5,), (0, 41),
     "zones 5xx -- the tutorial script's cast (AI/F00/D07)", 0),
    ("col", "Coliseum", (6,), (200, 235),
     "zone 600 -- the Coliseum's own catalogue (AH/F99/D47)", 0),
    ("room", "Room", (), (100, 137),
     "Change Room kind 1 (FMO_ROOM_MAPS) -- SE: the training sergeant stands in a room", 1),
    ("briefing", "Briefing Room", (), (100, 137),
     "Change Room kind 2 -- the strategy room, Frontline area 10 only", 2),
    ("roomb", "Room B", (), (100, 137), "Change Room kind 3", 3),
    ("roomc", "Room C", (), (100, 137), "Change Room kind 4", 4),
    ("hangar", "Hangar", (), (100, 137),
     "kind 5 -- one private hangar per pilot; hanger_event / wanzer + pilot setup", 5),
)
BAND_IDS = tuple(b[0] for b in BANDS)
LOBBY_BANDS = tuple(b[0] for b in BANDS if b[5] == 0)
#: place kind -> band id
PLACE_BANDS = {b[5]: b[0] for b in BANDS if b[5]}

#: The function behind each SCP entry name the event tables load, in English.
TAG_LABELS = (
    ("tag_mission_clear", "Mission Counter (clear)"),
    ("tag_mission", "Mission Counter"),
    ("tag_scramble", "Scramble Board"),
    ("tag_ranking", "Ranking Board"),
    ("tag_mapslct", "Map Selector"),
    ("tag_parsonnel", "Personnel Officer"),
    ("tag_search", "Search / Sortie Console"),
    ("tag_communty", "Communications Officer"),
    ("tag_debriefing", "Debriefing"),
    ("tag_reflect_win", "Win review"),
    ("tag_training_main", "Training"),
    ("tag_wapsetup", "Wanzer Setup (Hangar Console)"),
    ("tag_pltsetup", "Pilot Setup"),
    ("tag_senior", "Senior Officer"),
    ("tag_secretary", "Secretary"),
    ("tag_registration", "Registration desk"),
    ("tag_colisum", "Coliseum desk"),
    ("tag_kansen", "Spectate"),
    ("tag_battle_gate", "Battle gate"),
    ("tag_battle_ranking", "Battle ranking"),
    ("tag_wapcon", "Wanzer contest"),
    ("nina_event", "the Operator (Nina)"),
    ("ope_event", "the Operator"),
    ("hanger_event", "Hangar staff"),
    ("room_event", "Room staff (the sergeant's room)"),
    ("gunsou1_event", "the Sergeant (Becken)"),
    ("evb04_knass_bme", "cutscene slot"),
    ("Evb_20_MAIN", "cutscene slot"),
)
KEYS_TSV = os.path.join(HERE, "fmodata", "fmo-npc-keys.tsv")

#: KEY: SE'S OWN TERMINAL NAMES -- keys that were bodiless `name1.name2` entities
#: in the original, with the exact label SE's text uses (2026-09-11):
#:   SETUP.CONSOLE  tutorial: "To set up a wanzer, stand in front of the
#:                  SETUP.CONSOLE and press {}" -- in the HANGAR map; also in systext
#:   PILOT.LOCKER   tutorial: "Stand in front of the PILOT.LOCKER and press {} to
#:                  do pilot setup" -- ALSO in the HANGAR (first read as the
#:                  name of the room; corrected 2026-09-11)
#:   SCRAMBLE.BOARD tutorial + the Feb-2005 press shot (floating label, no body)
#:   MAP.SELECTOR   the press shot
#: The editor pre-selects the terminal body and this label for these keys.
TERMINAL_LABELS = {
    0x82080200: "SCRAMBLE.BOARD",
    0x82080201: "SCRAMBLE.BOARD",
    0x82080400: "MAP.SELECTOR",
    0x82080401: "MAP.SELECTOR",
    0x82080C00: "SETUP.CONSOLE",
    0x82080C01: "SETUP.CONSOLE",
    0x82080D00: "PILOT.LOCKER",
    0x82080D01: "PILOT.LOCKER",
}


def label_for(entries):
    seen = []
    for e in entries:
        for tag, lab in TAG_LABELS:
            if e.startswith(tag) and lab not in seen:
                seen.append(lab)
                break
        else:
            if e not in seen:
                seen.append(e)
    return " / ".join(seen)


def _roles_from_tsv(path=KEYS_TSV):
    """ROLES from the shipped key table (tools/fmo_npc_keys.py): every key
    the eight lobby event tables answer to, with the lobbies it exists in.
    Place bands get every key -- the table bank is loaded per ZONE and looked
    up in whatever scene you stand in, so a key answers in a room too."""
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    place_ids = tuple(PLACE_BANDS.values())
    for ln in lines[1:]:
        parts = ln.split("\t")
        if len(parts) < 4:
            continue
        key = int(parts[0], 16)
        bands = tuple(b for b in parts[1].split(",") if b) + place_ids
        entries = [e for e in parts[2].split(",") if e]
        out.append((key, "%s (%s)" % (label_for(entries), ", ".join(entries)[:40]), bands))
    return tuple(out) or None


#: Entity keys the client's NPC EVENT TABLES (AI/F08/D39..D48) key their
#: counters on, with the function each row speaks as. The KEY is what makes a
#: counter a counter; the PERSON (catalogue typecode) is only its body. Bands:
#: which lobbies the tables carry that key in. A key not listed here is still
#: accepted (the field is free) -- this is the menu, not the law.
#: Generated from the tables when fmodata/fmo-npc-keys.tsv ships; the hand
#: list below is the fallback.
ROLES_FALLBACK = (
    (0x82080100, "Mission.Counter", ("hq", "occ", "fz", "col")),
    (0x82080110, "tag_senior (the senior officer)", ("hq", "occ", "fz", "col")),
    (0x82080120, "tag_secretary", ("fz",)),
    (0x82080200, "Scramble.Board", ("hq", "occ", "fz")),
    (0x82080400, "Map.Selector", ("hq", "occ", "fz")),
    (0x82080500, "Personnel.Officer", ("hq", "occ", "fz")),
    (0x82080600, "Sortie.Console", ("hq", "occ", "fz")),
    (0x82080C00, "Hangar.Console", ("hq", "occ", "fz")),
    (0x82081010, "nina_event / ope_event (the Operator)", ("hq", "occ", "fz")),
    (0x82081020, "gunsou1_event (the sergeant) / Mission.Briefing", ("hq", "occ")),
    (0x82080710, "tag_debriefing", ("col",)),
    (0x82080711, "tag_debriefing (2)", ("col",)),
    (0x82080900, "tag_registration", ("col",)),
    (0x82080901, "tag_registration (2)", ("col",)),
    (0x82080910, "tag_colisum (Coliseum desk)", ("col",)),
    (0x82080920, "tag_kansen (spectate)", ("col",)),
    (0x82080930, "tag_battle_gate", ("col",)),
    (0x82080940, "tag_battle_ranking", ("col",)),
    (0x82080950, "tag_wapcon", ("col",)),
)
ROLES = _roles_from_tsv() or ROLES_FALLBACK


def band_info(band):
    """The BANDS row for a band id, or None."""
    return next((b for b in BANDS if b[0] == band), None)

#: a box top more than this above the map's floor is furniture, not ground
GROUND_ABOVE = 1.0
#: and this far below it is a pit / the void under a raised hall
GROUND_BELOW = 1.5
#: a box top this close to a height somebody stood at counts as that floor
LEVEL_TOL = 0.15

_LOCK = threading.RLock()
_PLANS = {}


# --------------------------------------------------------------------------- #
# facing
# --------------------------------------------------------------------------- #
def face_to_wire(deg, sign=-1):
    """Degrees (script convention, 0..360) -> the wire's 4th POP component,
    signed radians wrapped to +-pi. None (no facing) -> 0.0. Pure."""
    if deg is None:
        return 0.0
    r = math.radians(float(deg) % 360.0)
    if r > math.pi:
        r -= 2 * math.pi
    return round(sign * r, 3)


def face_from_wire(w, sign=-1):
    """The inverse: wire radians -> degrees 0..360, to the nearest degree."""
    if w is None:
        return None
    deg = math.degrees(sign * float(w)) % 360.0
    return round(deg) % 360


# --------------------------------------------------------------------------- #
# floor plans
# --------------------------------------------------------------------------- #
def plan_path(mapno):
    return os.path.join(PLAN_DIR, "%d.json" % int(mapno))


def load_plan(mapno):
    """The shipped floor plan for a map -- {"boxes": [[x0,y0,z0,x1,y1,z1]..],
    ...} -- or None if none ships. Cached; the files are build artefacts."""
    mapno = int(mapno)
    with _LOCK:
        if mapno in _PLANS:
            return _PLANS[mapno]
        p = plan_path(mapno)
        plan = None
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                plan = json.load(fh)
        _PLANS[mapno] = plan
        return plan


def plans_available():
    try:
        names = os.listdir(PLAN_DIR)
    except OSError:
        return []
    return sorted(int(n[:-5]) for n in names
                  if n.endswith(".json") and n[:-5].isdigit())


def ground_at(mapno, x, z, floor_y, plan=None, levels=None):
    """(y, box) of the ground under (x, z), or None.

    The highest box top under the point that lies within [floor_y -
    GROUND_BELOW, floor_y + GROUND_ABOVE]. `floor_y` is the map's known floor.

    WARNING: `levels` (the heights players have actually STOOD at on this map,
    WalkedGround.levels): when given, a box counts only if its top is within
    LEVEL_TOL of one of them. Live 2026-09-11 on hangar 141: every walked
    reading is y 0.00, but the hall is covered by 0.9 m slabs (54 x 55 m, top
    0.88) that are volumes, not floor -- the old rule stood an Operator NPC and
    a locker 0.8 m in the air on them. On map 102 players DO stand at 3.50 on
    the console platforms, so those still count."""
    plan = plan or load_plan(mapno)
    if not plan:
        return None
    lo, hi = floor_y - GROUND_BELOW, floor_y + GROUND_ABOVE
    best = None
    for b in plan["boxes"]:
        if b[0] <= x <= b[3] and b[2] <= z <= b[5] and lo <= b[4] <= hi:
            if levels and not any(abs(b[4] - lv) <= LEVEL_TOL for lv in levels):
                continue
            if best is None or b[4] > best[4]:
                best = b
    if best is None:
        return None
    return round(best[4], 2), best


def prop_near(mapno, x, z, radius=2.0, plan=None):
    """(cx, bottom_y, cz, box) of the nearest standing PROP to (x, z) within
    `radius` metres, or None. A prop = a small box (footprint <= 4 m, 0.8-3 m
    tall). KEY: Why: a terminal's MODEL is already in the map (seen in a live
    session 2026-09-11: the locker and the console were there before any NPC), and a
    type-30 entity loads the same model by its key -- SE's server put the
    entity exactly ON the prop, so the two coincide and the prop becomes
    selectable. WARNING: Boxes centred on the ORIGIN are the models' own local-space
    boxes (every hangar model's box appears once at (0, 0)), never placements,
    and are skipped."""
    plan = plan or load_plan(mapno)
    if not plan:
        return None
    best, bd = None, radius
    for b in plan["boxes"]:
        w, h, d = b[3] - b[0], b[4] - b[1], b[5] - b[2]
        if max(w, d) > 4.2 or not 0.8 <= h <= 3.0:
            continue
        cx, cz = (b[0] + b[3]) / 2, (b[2] + b[5]) / 2
        if abs(cx) < 0.15 and abs(cz) < 0.15:
            continue
        dd = math.hypot(cx - x, cz - z)
        if dd < bd:
            bd, best = dd, (round(cx, 2), round(b[1], 2), round(cz, 2), b)
    return best


def focus_extent(mapno, floor_y, plan=None, pad=3.0):
    """[x0, z0, x1, z1] framing the walkable floor: the boxes whose top is
    within the ground band. None when nothing is."""
    plan = plan or load_plan(mapno)
    if not plan:
        return None
    lo, hi = floor_y - GROUND_BELOW, floor_y + GROUND_ABOVE
    sel = [b for b in plan["boxes"] if lo <= b[4] <= hi]
    if not sel:
        return None
    return [min(b[0] for b in sel) - pad, min(b[2] for b in sel) - pad,
            max(b[3] for b in sel) + pad, max(b[5] for b in sel) + pad]


# --------------------------------------------------------------------------- #
# SE's script marks (tools/fmo_script_marks.py -> fmodata/fmo-script-marks.json)
# --------------------------------------------------------------------------- #
MARKS_JSON = os.path.join(HERE, "fmodata", "fmo-script-marks.json")
_MARKS = {}


def load_marks(band):
    """SE's own 0xE285 placement marks for the script a band runs:
    [{"sid", "key", "x", "y", "z", "face", "created", "places"}], parked
    off-map marks (|coord| > 300) left out. [] when nothing ships."""
    with _LOCK:
        if not _MARKS:
            try:
                with open(MARKS_JSON, encoding="utf-8") as fh:
                    _MARKS.update(json.load(fh))
            except OSError:
                _MARKS["_none"] = True
        d = _MARKS.get(band)
    if not d:
        return []
    return [m for m in d["marks"]
            if max(abs(m["x"]), abs(m["y"]), abs(m["z"])) <= 300]


# --------------------------------------------------------------------------- #
# walked ground: the client's own position stream, as floor
# --------------------------------------------------------------------------- #
_POS_RE = re.compile(r"POSITION MapNo=(\d+) \((-?[\d.]+), (-?[\d.]+), (-?[\d.]+)\)")


class WalkedGround:
    """Every spot a real player has stood on, per map, from fmo.py's own
    `POSITION MapNo=` log lines -- binned to CELL metres with a count and the
    mean y. A cell somebody stood in is ground by definition, which is truer
    than any box, and it is the only floor the rooms have until someone walks
    them. The log is read incrementally (byte offset kept; a truncated or
    rotated log starts over), so a poll costs the new lines only.

    Lines tagged SPAWN are skipped: that readout is where the scene PUT the
    player before the floor placed them (y 0.00 on a 3.11 floor)."""
    CELL = 0.5

    def __init__(self, path):
        self.path = path
        self._off = 0
        self._cells = {}          # mapno -> {(ix, iz): [n, ysum]}
        self._n = 0

    def _refresh(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size < self._off:
            self._off, self._cells, self._n = 0, {}, 0
        if size == self._off:
            return
        with open(self.path, "rb") as fh:
            fh.seek(self._off)
            chunk = fh.read()
        # keep a partial last line for next time
        cut = chunk.rfind(b"\n") + 1
        self._off += cut
        for ln in chunk[:cut].decode("utf-8", "replace").splitlines():
            if "POSITION MapNo=" not in ln or "SPAWN" in ln:
                continue
            m = _POS_RE.search(ln)
            if not m:
                continue
            mapno = int(m.group(1))
            x, y, z = (float(m.group(i)) for i in (2, 3, 4))
            if max(abs(x), abs(z)) > 300:
                continue
            c = self._cells.setdefault(mapno, {})
            k = (int(math.floor(x / self.CELL)), int(math.floor(z / self.CELL)))
            e = c.setdefault(k, [0, 0.0])
            e[0] += 1
            e[1] += y
            self._n += 1

    def cells(self, mapno):
        """[[x0, z0, n, mean_y], ...] for a map; x0/z0 = the cell's corner."""
        with _LOCK:
            self._refresh()
            c = self._cells.get(int(mapno), {})
            return [[k[0] * self.CELL, k[1] * self.CELL, e[0], round(e[1] / e[0], 2)]
                    for k, e in sorted(c.items())]

    def count(self, mapno=None):
        with _LOCK:
            self._refresh()
            if mapno is None:
                return self._n
            return sum(e[0] for e in self._cells.get(int(mapno), {}).values())

    def levels(self, mapno, min_n=2, res=0.05):
        """The heights players have stood at on a map, rounded to `res`, each
        seen at least `min_n` times (one stray reading is not a floor)."""
        with _LOCK:
            self._refresh()
            c = self._cells.get(int(mapno), {})
            hist = {}
            for e in c.values():
                lv = round(round((e[1] / e[0]) / res) * res, 2)
                hist[lv] = hist.get(lv, 0) + e[0]
        return sorted(lv for lv, n in hist.items() if n >= min_n)

    def floor_mode(self, mapno, min_total=10, res=0.05):
        """The height most readings on a map stand at, or None below
        `min_total` readings -- the map's real floor, where the served spawn
        height (FMO_UDP_POP_POS_MAP) can differ (141: spawn 0.50, floor 0.00)."""
        with _LOCK:
            self._refresh()
            c = self._cells.get(int(mapno), {})
            hist, tot = {}, 0
            for e in c.values():
                lv = round(round((e[1] / e[0]) / res) * res, 2)
                hist[lv] = hist.get(lv, 0) + e[0]
                tot += e[0]
        if tot < min_total or not hist:
            return None
        return max(hist.items(), key=lambda kv: kv[1])[0]

    def near_level(self, mapno, x, z, radius=8.0):
        """The height of the nearest walked cell within `radius` metres, or
        None -- floors are flat locally, so a spot nobody stood on still gets
        the level of the floor around it."""
        with _LOCK:
            self._refresh()
            c = self._cells.get(int(mapno))
        if not c:
            return None
        best, bd = None, radius
        for (ix, iz), e in c.items():
            d = math.hypot((ix + 0.5) * self.CELL - x, (iz + 0.5) * self.CELL - z)
            if d < bd:
                bd, best = d, e
        return None if best is None else round(best[1] / best[0], 2)

    def ground_at(self, mapno, x, z, radius=0.75):
        """Mean y of the walked cell under (x, z), or of the nearest within
        `radius` metres, or None."""
        with _LOCK:
            self._refresh()
            c = self._cells.get(int(mapno))
        if not c:
            return None
        best, bd = None, radius
        for (ix, iz), e in c.items():
            cx, cz = (ix + 0.5) * self.CELL, (iz + 0.5) * self.CELL
            d = math.hypot(cx - x, cz - z)
            if d < bd:
                bd, best = d, e
        return None if best is None else round(best[1] / best[0], 2)


# --------------------------------------------------------------------------- #
# the layout file
# --------------------------------------------------------------------------- #
class Layout:
    """The file: {"version": 1, "bands": {band: {"mapno": N, "npcs": [row..]}}}.

    A row: {"key": int, "type": int, "x", "y", "z": float, "face": deg|None,
            "cat": typecode|None, "label": "Name.Label"|None,
            "ckind": 0..3|None}.  ckind = the pop body's client_kind (body+0x00,
    fmoworld.record_pop): None = the server's FMO_UDP_POP_NPC_CLIENT_KIND (1 on
    prod = no peer object); 0 = a peer, what every NPC was sent as before
    2026-09-05 and the 'does a name tag appear' experiment.

    Re-read on mtime so an edit from the panel's thread is what the UDP loop
    pops next; every write is atomic (temp + replace) and bumps the cache."""

    def __init__(self, path=None):
        self.path = path or os.environ.get("FMO_NPC_LAYOUT", "").strip() \
            or DEFAULT_PATH
        self._mtime = None
        self._data = {"version": 1, "bands": {}}

    # -- reading ---------------------------------------------------------- #
    def _refresh(self):
        try:
            m = os.path.getmtime(self.path)
        except OSError:
            self._mtime = None
            self._data = {"version": 1, "bands": {}}
            return
        if m == self._mtime:
            return
        with open(self.path, encoding="utf-8") as fh:
            d = json.load(fh)
        if not isinstance(d, dict) or not isinstance(d.get("bands"), dict):
            raise ValueError("%s is not a layout file" % self.path)
        self._mtime = m
        self._data = d

    def data(self):
        with _LOCK:
            self._refresh()
            return self._data

    def has(self, band):
        return band in self.data()["bands"]

    def bands(self):
        return [b for b in BAND_IDS if b in self.data()["bands"]]

    def band(self, band):
        """{"mapno", "npcs"} for a band, or None if the file has no entry."""
        b = self.data()["bands"].get(band)
        if b is None:
            return None
        return {"mapno": b.get("mapno"), "npcs": [dict(r) for r in b.get("npcs", [])]}

    def ckinds(self, band):
        """{key: client_kind} overrides for a band, {} when none."""
        b = self.band(band)
        return rows_ckinds(b["npcs"]) if b else {}

    def roster(self, band, sign=-1):
        """(roster, names) in the server's own shape -- [(uid, utype,
        (x, y, z, w), cat)], {uid: (name1, name2)} -- or None."""
        b = self.band(band)
        if b is None:
            return None
        return rows_to_roster(b["npcs"], sign)

    # -- writing ---------------------------------------------------------- #
    def _write(self, d):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=1, sort_keys=True)
        os.replace(tmp, self.path)
        self._mtime = os.path.getmtime(self.path)
        self._data = d

    def set_band(self, band, mapno, rows):
        if band not in BAND_IDS:
            raise ValueError("no band %r" % band)
        with _LOCK:
            self._refresh()
            d = json.loads(json.dumps(self._data))
            d["bands"][band] = {"mapno": int(mapno),
                                "npcs": [validate_row(r) for r in rows]}
            self._write(d)

    def drop_band(self, band):
        with _LOCK:
            self._refresh()
            d = json.loads(json.dumps(self._data))
            if d["bands"].pop(band, None) is not None:
                self._write(d)
                return True
            return False


def validate_row(r):
    out = {"key": int(r["key"]), "type": int(r.get("type", 4) or 4),
           "x": round(float(r["x"]), 2), "y": round(float(r["y"]), 2),
           "z": round(float(r["z"]), 2),
           "face": None if r.get("face") is None else round(float(r["face"]) % 360.0, 1),
           "cat": None if r.get("cat") is None else int(r["cat"]),
           "label": (str(r["label"]) if r.get("label") else None),
           "ckind": None if r.get("ckind") in (None, "") else int(r["ckind"])}
    if out["ckind"] is not None and out["ckind"] not in (0, 1, 2, 3):
        raise ValueError("ckind wants 0..3 (fmoworld.record_pop refuses the rest)")
    if not out["key"]:
        raise ValueError("a row needs an entity key")
    if out["label"] is not None:
        n1, _, n2 = out["label"].partition(".")
        if not n1 or len(n1) > 16 or len(n2) > 16:
            raise ValueError("label wants Name.Label, 1..16 chars each")
    for k in ("x", "y", "z"):
        if not -327.67 <= out[k] <= 327.67:
            raise ValueError("%s=%s is outside what the wire carries" % (k, out[k]))
    return out


# --------------------------------------------------------------------------- #
# to and from the server's roster shape / the env grammar
# --------------------------------------------------------------------------- #
def rows_to_roster(rows, sign=-1):
    roster, names = [], {}
    for r in rows:
        r = validate_row(r)
        pos = (r["x"], r["y"], r["z"], face_to_wire(r["face"], sign))
        roster.append((r["key"], r["type"], pos, r["cat"]))
        if r["label"]:
            n1, _, n2 = r["label"].partition(".")
            names[r["key"]] = (n1, n2)
    return roster, names


def rows_ckinds(rows):
    """{key: client_kind} for the rows that override it. Pure."""
    return {int(r["key"]): int(r["ckind"]) for r in rows if r.get("ckind") is not None}


def roster_to_rows(roster, names, sign=-1):
    """The inverse -- what `import the served roster` writes. A roster entry
    with no position (pos None = 'the self-POP spot') gets no row: the editor
    cannot draw a place it does not know."""
    rows = []
    for uid, utype, pos, cat in roster:
        if pos is None:
            continue
        w = pos[3] if len(pos) > 3 else 0.0
        label = None
        if uid in names:
            label = "%s.%s" % tuple(names[uid])
        rows.append(validate_row({
            "key": uid, "type": utype, "x": pos[0], "y": pos[1], "z": pos[2],
            "face": None if not w else face_from_wire(w, sign),
            "cat": cat, "label": label}))
    return rows


def to_spec(rows, sign=-1):
    """The `FMO_UDP_POP_NPC` grammar for these rows -- what an admin pastes
    into .env to make a layout survive without the file."""
    parts = []
    for r in rows:
        r = validate_row(r)
        s = "0x%08x" % r["key"]
        if r["type"] != 4:
            s += ":%d" % r["type"]
        s += "@%g,%g,%g" % (r["x"], r["y"], r["z"])
        if r["face"] is not None:
            s += ",%g" % face_to_wire(r["face"], sign)
        if r["cat"] is not None:
            s += "#%d" % r["cat"]
        if r["label"]:
            s += "=" + r["label"]
        parts.append(s)
    return ";".join(parts)


# --------------------------------------------------------------------------- #
def selftest():
    import tempfile
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print("  %s: %s%s" % (name, "OK" if cond else "FAIL", (" " + detail) if detail and not cond else ""))
        ok &= bool(cond)

    # facing round-trips at both signs, and 0 stays 0
    for sign in (-1, 1):
        rt = all(face_from_wire(face_to_wire(d, sign), sign) == d for d in range(0, 360, 15))
        check("facing round-trips (sign %d)" % sign, rt)
    check("no facing -> wire 0", face_to_wire(None) == 0.0)
    check("270 deg at sign -1 is +1.571 (prod's coliseum rows)",
          face_to_wire(270, -1) == 1.571 and face_to_wire(90, -1) == -1.571)

    # the store
    tmp = tempfile.mkdtemp(prefix="fmolayout-")
    L = Layout(os.path.join(tmp, "layout.json"))
    check("empty file = no bands", L.bands() == [] and L.band("hq") is None)
    rows = [{"key": 0x82080200, "x": -1.35, "y": 3.5, "z": 2.74, "face": None,
             "cat": 113, "label": "Scramble.Board"},
            {"key": 0x82081020, "x": 10.61, "y": 2.99, "z": 7.88, "face": 225,
             "cat": 102, "label": "Henry.Viduka"}]
    L.set_band("hq", 102, rows)
    check("set_band round-trips", L.band("hq")["npcs"][1]["face"] == 225.0
          and L.band("hq")["mapno"] == 102)
    r, n = L.roster("hq")
    check("roster shape", r[0] == (0x82080200, 4, (-1.35, 3.5, 2.74, 0.0), 113)
          and n[0x82081020] == ("Henry", "Viduka"), str(r))
    check("facing reaches the wire signed", r[1][2][3] == face_to_wire(225, -1))
    # a second instance sees the write (mtime), and an external rewrite too
    L2 = Layout(L.path)
    check("second reader sees it", L2.bands() == ["hq"])
    import time
    time.sleep(0.02)
    with open(L.path, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "bands": {}}, fh)
    os.utime(L.path, None)
    check("external truncation is seen on mtime", L.bands() == [])
    # the env grammar, both ways
    spec = to_spec(rows)
    check("to_spec", spec == "0x82080200@-1.35,3.5,2.74#113=Scramble.Board;"
          "0x82081020@10.61,2.99,7.88,2.356#102=Henry.Viduka", spec)
    back = roster_to_rows(*rows_to_roster(rows))
    check("roster_to_rows inverts rows_to_roster",
          [(b["key"], b["face"], b["label"]) for b in back]
          == [(0x82080200, None, "Scramble.Board"), (0x82081020, 225.0, "Henry.Viduka")])
    check("a roster entry with no position gets no row",
          roster_to_rows([(1, 4, None, None)], {}) == [])
    check("ckind rides a row and is collected per band",
          validate_row({"key": 5, "x": 0, "y": 0, "z": 0, "ckind": 0})["ckind"] == 0
          and rows_ckinds([{"key": 5, "ckind": 0}, {"key": 6}, {"key": 7, "ckind": None}]) == {5: 0})
    bad2 = False
    try:
        validate_row({"key": 1, "x": 0, "y": 0, "z": 0, "ckind": 7})
    except ValueError:
        bad2 = True
    check("ckind outside 0..3 refused", bad2)
    bad = False
    try:
        validate_row({"key": 1, "x": 999, "y": 0, "z": 0})
    except ValueError:
        bad = True
    check("out-of-wire position refused", bad)

    # the floor plans, if they ship beside us
    plans = plans_available()
    if 102 not in plans:
        print("  SKIP  the floor plans (needs services/fmodata/floorplans built from "
              "your client, see tools/fmodata_build.py)")
    else:
        g = ground_at(102, 3.29, 8.59, 3.11)
        check("map 102 floor under Kwangsu Son is a tile at ~3.1",
              g is not None and 2.9 <= g[0] <= 3.2, str(g))
        g = ground_at(102, -1.35, 2.74, 3.11)
        check("map 102 console platform under Scramble.Board is 3.50",
              g is not None and g[0] == 3.5, str(g))
        check("nothing under the void", ground_at(102, 200, 200, 3.11) is None)
        ext = focus_extent(102, 3.11)
        check("focus extent frames the hall", ext is not None and ext[0] < 0 < ext[2]
              and ext[1] < 0 < ext[3], str(ext))
    # SE's marks ship and read
    hm = load_marks("hq")
    if not hm:
        print("  SKIP  the script marks (needs services/fmodata/fmo-script-marks.json "
              "built from your client, see tools/fmodata_build.py)")
    else:
        check("hq script marks ship (D87), server slots among them",
              len(hm) > 20 and any(not m["created"] and m["x"] == 0.45 and m["face"] == 270 for m in hm), str(len(hm)))
        check("parked off-map marks are dropped", all(abs(m["x"]) <= 300 for m in hm))
    # walked ground from a log
    lp = os.path.join(tmp, "fmo.log")
    with open(lp, "w", encoding="utf-8") as fh:
        fh.write("x [udp 1.2.3.4:1] POSITION MapNo=102 (1.93, 0.00, -1.12) rot +2.549 flags 0x6  <- SPAWN: no\n"
                 "x [udp 1.2.3.4:1] POSITION MapNo=102 (3.29, 3.11, 8.59) rot +0.1 flags 0x6\n"
                 "x [udp 1.2.3.4:1] POSITION MapNo=102 (3.31, 3.13, 8.61) rot +0.1 flags 0x6\n"
                 "x [udp 1.2.3.4:1] POSITION MapNo=124 (-0.46, 0.00, 16.69) rot +0.0 flags 0x6\n"
                 "x partial line without newline")
    W = WalkedGround(lp)
    c = W.cells(102)
    check("walked cells: one cell, two readings, mean y, the SPAWN line skipped",
          c == [[3.0, 8.5, 2, 3.12]] and W.count(102) == 2, str(c))
    check("walked ground_at hits the cell and misses the void",
          W.ground_at(102, 3.4, 8.7) == 3.12 and W.ground_at(102, 9, 9) is None)
    with open(lp, "a", encoding="utf-8") as fh:
        fh.write("\nx [udp 1.2.3.4:1] POSITION MapNo=124 (-0.40, 0.00, 16.60) rot +0.0 flags 0x6\n")
    check("the log is read incrementally", W.count(124) == 2, str(W.cells(124)))
    # the hangar's 0.88 slabs are not floor when players only ever stood at 0.00
    if os.path.exists(plan_path(141)) and os.path.exists(plan_path(102)):
        _g = ground_at(141, -3.24, 8.28, 0.0, levels=[0.0])
        check("a box top nobody stood at is not floor (141's 0.88 slabs lose to the 0.00 floor)",
              (_g is None or abs(_g[0]) <= LEVEL_TOL) and ground_at(141, -3.24, 8.28, 0.0)[0] == 0.88, str(_g))
        check("a box top players stand at still counts (102's 3.50 platform)",
              ground_at(102, -1.35, 2.74, 3.11, levels=[3.1, 3.5])[0] == 3.5)
    else:
        print("  SKIP  the floor-plan checks (needs services/fmodata/floorplans built "
              "from your client, see tools/fmodata_build.py)")
    W2 = WalkedGround(lp)
    check("walked levels / floor mode / near level",
          W2.levels(102) == [3.1] and W2.floor_mode(102, min_total=2) == 3.1
          and W2.near_level(102, 5.0, 8.6) == 3.12 and W2.near_level(102, 60, 60) is None,
          str((W2.levels(102), W2.floor_mode(102, min_total=2), W2.near_level(102, 5.0, 8.6))))
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(selftest() if "--selftest" in sys.argv else selftest())
