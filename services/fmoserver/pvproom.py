"""Frontline matching battles: WAITING FOR OPPONENTS, the start, the room judge and the war settle."""
import math
import os
import struct
import threading
import time
from .deps import fmowar, fmoworld
from .knobs import _env_int
from .wirelog import hexdump, log


# --------------------------------------------------------------------------- #
# THE MATCHING SORTIE (static 2026-10-07, FrontMissionOnline.dll.unpacked)
# --------------------------------------------------------------------------- #
# SE (topics/060804): on a PvP map the battle map that a sortie CREATES waits
# up to 20 minutes for an enemy battle group ("WAITING FOR OPPONENTS", units
# frozen); it starts when an enemy group arrives, when a member picks "Start
# Battle", or when the wait runs out (then the NPCs fight). Joining stays open
# for 5 minutes after the start. Read in the client:
#
#   block+0x7C (lobby+0x5CFA) bit 0x200 = WAITING, bit 0x400 = STARTED.
#   0x61004300 "is the battle running": bit 0x200 clear -> yes; set -> no while
#     0x61001EE0 (a countdown of block+0x7D8 seconds from block+0x48, only when
#     both bits are set) is above 0, then bit 0x400. We serve +0x7D8 = 0, so a
#     started battle runs at once. The overlay 0x61237AE3 and the battle menu
#     (0x6115D71F: only the 0x613953E0 table, "Start Battle", while it is not
#     running) key off the same call.
#   cmd 138 (arm 0x611EFB11): +0x00 -> block+0x48, +0x04 reason -> the banner
#     (1 = 79:30 enemy forces arrived, 2 = 79:29 Start Battle was chosen, 3 =
#     79:31 the standby time ended), +0x08 -> 0x61001F40, which sets bit 0x400.
#     It does NOT clear 0x200; 0x61004300 then runs the battle.
#   cmd 148 (arm 0x611EFF04), the late joiner's resync, 0xB4 bytes:
#     +0x00 bit 0 -> block+0x7C bit 0x200, bit 1 -> bit 0x400
#     +0x04 -> block+0x48 (the start time)
#     +0x08 + 4*i, for i < RectDataCnt (block+0x30): rect i bytes +0x00/+0x01
#           copied, bits 0..1 of +0x02 copied (block+0x418, stride 0x1C)
#     +0x88 12 bytes -> block+0x798
#     +0x94 four {u32, u32} pairs -> 0x61002160, which CLEARS the four-slot
#           holder table at globals+0x218 and refills it (the container and
#           beacon holders of cmd 140/141); zeros = nobody holds anything
#   "Start Battle" (menu item 0:130, command 0x1055 in table 0x613953E0) ->
#     jump table 0x61160310 index 9 -> 0x611601C6 -> 0x611D36B0, which sends
#     BM cmd 137 (0x89) with 64 zero bytes, and only while 0x61004300 says the
#     battle is NOT running. That is its only caller, so cmd 137 is the client's
#     Start Battle request, not a second Emergency Escape.
#   block+0x50 (lobby+0x5CCE, u32) = the objective kind; 5 = Team Deathmatch
#     (79:25). The win radio line is 79:17 when it is nonzero.
#
# WARNING: what the client's AI does while frozen is NOT read. The server
# keeps the NPC squad out of a waiting room instead (squad_allowed), so the
# question does not come up.

#: FMO_PVP_MATCHING (P1, default 1): a sortie onto a PvP selector's battle map
#: is served WAITING (block+0x7C 0x200) and the room starts on the first
#: hostile pilot, Start Battle (cmd 137) or the wait running out. 0 = the old
#: immediate start (FMO_BATTLE_START) everywhere.
MATCHING = _env_int("FMO_PVP_MATCHING", "1") != 0
#: FMO_PVP_SELECTORS: the selectors (MapKinds) whose battle maps match. The
#: Frontline is 505/509/513; SE also matched on "some" 2xx/4xx Occupied areas,
#: which ones is not on any page we hold, so they are opt-in here.
PVP_SELECTORS_SPEC = os.environ.get("FMO_PVP_SELECTORS", "").strip() or "505,509,513"
#: FMO_PVP_WAIT: seconds a waiting room waits for an enemy (SE: 20 minutes).
PVP_WAIT = _env_int("FMO_PVP_WAIT", "1200")
#: FMO_PVP_NPC_VS_PILOTS: 0 (default) = a room started by an enemy pilot gets
#: no NPC squad (SE: the NPCs fight when nobody came); 1 = it gets one anyway.
NPC_VS_PILOTS = _env_int("FMO_PVP_NPC_VS_PILOTS", "0") != 0
#: FMO_PVP_JUDGE (P2, default 1): the room judges the battle for every pilot
#: in it (judge_counts) and block+0x50 is served FMO_PVP_OBJECTIVE_KIND.
JUDGE = _env_int("FMO_PVP_JUDGE", "1") != 0
OBJECTIVE_KIND = _env_int("FMO_PVP_OBJECTIVE_KIND", "5")
#: FMO_PVP_DEATH_WAIT: a destroyed or ejected pilot waits this many seconds for
#: the room's verdict, then ends with a loss (0 = wait for the verdict however
#: long). OURS: the client offers no escape once destroyed (live 2026-09-27).
DEATH_WAIT = _env_int("FMO_PVP_DEATH_WAIT", "300")
#: FMO_PVP_WAR (P3, default 1): the room settles the war ONCE with its verdict
#: for every side that had pilots in it; the per-pilot war_settle stands down.
WAR = _env_int("FMO_PVP_WAR", "1") != 0

# --------------------------------------------------------------------------- #
# P4: CAPTURE & HOLD (static 2026-10-07; SE's rules, topics 2006-07-28)
# --------------------------------------------------------------------------- #
# SE: three target areas on the battle map, neutral at the start, the same
# for both armies, shown on the private and share radar with their owner. A
# wanzer takes one by CROUCHING inside it for 20 s; knocked down or destroyed
# inside those 20 s, the capture fails; while an enemy's capture is running
# nobody else can start one there. A taken area stays taken when its captor
# leaves, until the enemy takes it back. The battle group that holds ALL the
# areas wins; disabling every enemy also wins.
#
# What the client does (FrontMissionOnline.dll, read 2026-10-07):
#   * the areas are the block's RECTS (missionblock.rect_record: id, owner,
#     drawn flag, min/max box). Ids 10, 11, 12 are drawn as A, B, C, in the
#     owner's colour (blue = the local unit's nation, red = the other, grey =
#     nobody).
#   * the server authors every capture: cmd 133 (fmoworld.record_zone_state)
#     sets a rect's owner through a rect GROUP of type 5 and can queue a HUD
#     line from BB/F14/D24.DAT ("戦域Aの制圧開始", the 30/15/10/5..1 s
#     countdown, "戦域Aを制圧しました"). The client sends no capture record
#     of its own (no sender of cmd 133 in the image).
#   * a late joiner's cmd 148 carries every rect's id/owner/flags.
#   * block+0x50 = 3 opens the battle with 79:14 "There is a target area.
#     Check the share radar." (0x610E692A, kinds 1..5 only). WARNING: which
#     kind SE served for Capture & Hold is NOT read; 3 is the one whose line
#     fits. 79:26..28 (Safeguard / Territorial Conquest / Capture the Flag)
#     have no reader in the code at all.
#
# OURS / GUESSED:
#   * CROUCHING IS NOT VISIBLE TO THE SERVER (the motion state the server
#     reads carries position, heading and aim, no posture we have decoded).
#     A capture here is: stand inside the area, alive, and move no more than
#     FMO_PVP_CH_STILL world units from where the capture began, for
#     FMO_PVP_CH_HOLD seconds. A crouched wanzer cannot walk, so "still" is
#     the nearest thing the wire shows; 0 turns the stillness test off.
#     "Knocked down" (転倒) is not visible either; destroyed / ejected /
#     dropped is (member_out).
#   * where the areas are: SE's mission data came from the battle server and
#     is not in any file we hold. fmodata/fmo-capture-zones.tsv
#     (tools/fmodatagen/fmozones.py) puts them on open ground at 1/4, 1/2 and
#     3/4 of the way from side A's spawn to side B's; a map with no row
#     takes those three points straight off its spawn row.
#   * the area size (FMO_PVP_CH_HALF, half the side of a square) and the
#     time-limit rule (more areas held wins; a tie goes to the units-standing
#     rule) are OURS.
#   * NPC wanzers do not capture here (SE: they could).

#: FMO_PVP_CH_MAPS: the OLD switch for Capture & Hold alone. '' (default) =
#: FMO_PVP_MODES decides every map's objective (the mode rotation below);
#: 'all' = Capture & Hold in every matching room on a map with zone geometry;
#: a list of battle map numbers, e.g. '471,418' = Capture & Hold there and
#: Team Deathmatch everywhere else; 'off' = Team Deathmatch everywhere.
CH_MAPS_SPEC = os.environ.get("FMO_PVP_CH_MAPS", "").strip()
#: block+0x50 served to a Capture & Hold room (3 = target area, 79:14)
CH_KIND = _env_int("FMO_PVP_CH_KIND", "3")
#: seconds a capture takes (SE: 20)
CH_HOLD = _env_int("FMO_PVP_CH_HOLD", "20")
#: half the side of an area, world units (OURS; a wanzer is about 7 wide)
CH_HALF = _env_int("FMO_PVP_CH_HALF", "40")
#: a capturing wanzer may drift this far from where it began (0 = off)
CH_STILL = _env_int("FMO_PVP_CH_STILL", "12")
CH_GROUP = 1                           # the one rect group, type 5
CH_FIRST_ID = 10                       # rect ids 10, 11, 12 -> labels A, B, C
CH_Y_BELOW, CH_Y_ABOVE = 8.0, 16.0     # the box round the ground (draw band ~ +7.8)
CH_ZONES_TSV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fmodata",
    "fmo-capture-zones.tsv")
CH_TARGETS = (0.25, 0.5, 0.75)


def parse_ch_maps(spec):
    """'' -> set() (none), 'all' -> 'all', '471,418' -> {471, 418}. Pure."""
    s = (spec or "").strip().lower()
    if not s or s in ("0", "off", "none"):
        return set()
    if s in ("all", "1", "on"):
        return "all"
    return {int(p, 0) for p in s.split(",") if p.strip()}


try:
    # None = not set: the mode rotation (FMO_PVP_MODES) decides
    CH_MAPS = parse_ch_maps(CH_MAPS_SPEC) if CH_MAPS_SPEC else None
except ValueError:
    CH_MAPS = set()
    log(f"WARNING: FMO_PVP_CH_MAPS={CH_MAPS_SPEC!r} is not 'all' or a list of "
        f"maps -- Capture & Hold is off")


def load_ch_zones(path=None):
    """{mapno: [(x, y, z), (x, y, z), (x, y, z)]} from fmo-capture-zones.tsv;
    {} when the file is absent. A malformed row is skipped."""
    rows = {}
    try:
        with open(path or CH_ZONES_TSV, encoding="utf-8") as f:
            cols = f.readline().rstrip("\r\n").split("\t")
            for line in f:
                r = dict(zip(cols, line.rstrip("\r\n").split("\t")))
                try:
                    pts = [tuple(float(v) for v in tok.split(":"))
                           for tok in r["zones"].split()]
                    if pts and all(len(p) == 3 for p in pts):
                        rows[int(r["map"])] = pts
                except (KeyError, ValueError):
                    continue
    except OSError:
        return {}
    return rows


CH_ZONE_ROWS = load_ch_zones()


def ch_zone_points(mapno, rows=None, spawn=None):
    """([(x, ground y, z)] A..C, source) for battle map `mapno`; ([], why)
    when there is neither a zone row nor a spawn row."""
    rows = CH_ZONE_ROWS if rows is None else rows
    try:
        m = int(mapno)
    except (TypeError, ValueError):
        return [], "no map"
    if m in rows:
        return list(rows[m]), "fmo-capture-zones.tsv"
    if spawn is None:
        from . import battlepop
        spawn = battlepop.spawn_row(m)
    if not spawn:
        return [], f"map {m} has no zone row and no spawn row"
    (ax, az), (bx, bz), y = spawn["a"], spawn["b"], spawn["y"]
    # the spawn row's y is its lowest slot's ground minus 4
    return ([(round(ax + t * (bx - ax), 1), y + 4.0, round(az + t * (bz - az), 1))
             for t in CH_TARGETS],
            "straight off the spawn row (ground under the points NOT checked)")


def ch_zones(mapno, half=None, rows=None, spawn=None):
    """The zone states a Capture & Hold room starts with: [{id, center, lo,
    hi, owner 0, cap None}], all neutral. [] when the map has no geometry."""
    h = float(CH_HALF if half is None else half)
    pts, _src = ch_zone_points(mapno, rows, spawn)
    out = []
    for i, (x, y, z) in enumerate(pts[:5]):
        out.append({"index": i, "id": CH_FIRST_ID + i, "center": (x, y, z),
                    "lo": (x - h, y - CH_Y_BELOW, z - h),
                    "hi": (x + h, y + CH_Y_ABOVE, z + h),
                    "owner": 0, "cap": None})
    return out


# --------------------------------------------------------------------------- #
# WHICH OBJECTIVE A MAP RUNS (2026-10-08)
# --------------------------------------------------------------------------- #
# SE named the modes (Capture the Flag and Capture & Hold from the 2006-08
# update, Demolition and Beacon Delivery from 2006-10) and said a battle group
# sortieing to a "target sector" was given one; the Demolition page adds that
# the map selector showed the operation's name per battle map. Which sectors
# ran which mode is on no page we hold, so the assignment is OURS: the
# Frontline battle maps (fmosectors, selectors 505/509/513) in map order take
# the modes of DEFAULT_ROTATION in turn, so every mode runs somewhere and
# about one map in five stays Team Deathmatch. A map without the geometry its
# mode needs falls back to Team Deathmatch (logged at the room's creation).
#: FMO_PVP_MODES: '' (default) = the rotation below; a comma list of mode
#: names = that rotation instead ('tdm' alone or 'off' = Team Deathmatch
#: everywhere); 'map:mode' items pin one map, e.g. 'ch,ctf,471:demo'.
#: Modes: tdm, ch (Capture & Hold), ctf, beacon, demo.
MODES_SPEC = os.environ.get("FMO_PVP_MODES", "").strip()
MODE_NAMES = ("tdm", "ch", "ctf", "beacon", "demo")
DEFAULT_ROTATION = ("ch", "ctf", "tdm", "demo", "beacon")
_DEFAULT = object()


def parse_modes(spec):
    """{'rotation': (mode, ...), 'maps': {map: mode}}. Pure."""
    s = (spec or "").strip().lower()
    rot, maps = [], {}
    if s in ("", "default"):
        return {"rotation": DEFAULT_ROTATION, "maps": {}}
    if s in ("off", "0", "none"):
        return {"rotation": ("tdm",), "maps": {}}
    for tok in s.split(","):
        tok = tok.strip()
        if not tok:
            continue
        m, sep, mode = tok.partition(":")
        if sep:
            if mode not in MODE_NAMES:
                raise ValueError(f"{tok!r}: mode {mode!r} is not one of {MODE_NAMES}")
            maps[int(m, 0)] = mode
        elif tok in MODE_NAMES:
            rot.append(tok)
        else:
            raise ValueError(f"{tok!r} is not a mode ({MODE_NAMES}) or map:mode")
    return {"rotation": tuple(rot) or DEFAULT_ROTATION, "maps": maps}


try:
    MODES = parse_modes(MODES_SPEC)
except ValueError as _e:
    MODES = parse_modes("")
    log(f"WARNING: FMO_PVP_MODES={MODES_SPEC!r}: {_e} -- the default rotation runs")
_PVP_MAPS = []


def pvp_maps():
    """The Frontline battle maps in map order (fmosectors, cached)."""
    if not _PVP_MAPS:
        from .deps import fmosectors
        got = set()
        for sel in sorted(PVP_SELECTORS):
            for _row, mapno in (getattr(fmosectors, "SECTORS", {}) or {}).get(sel, {}).values():
                got.add(int(mapno))
        _PVP_MAPS.extend(sorted(got))
    return _PVP_MAPS


def assigned_mode(mapno, modes=None, maps=None):
    """The mode FMO_PVP_MODES gives battle map `mapno` (no geometry check). Pure
    given `maps` (the ordered map list)."""
    modes = MODES if modes is None else modes
    m = int(mapno)
    if m in modes["maps"]:
        return modes["maps"][m]
    rot = modes["rotation"]
    order = pvp_maps() if maps is None else maps
    i = order.index(m) if m in order else m
    return rot[i % len(rot)]


def mode_for(mapno, maps=_DEFAULT, modes=None):
    """The objective of a room on battle map `mapno`: 'ch', 'ctf', 'beacon',
    'demo' or 'tdm'. FMO_PVP_CH_MAPS, when set, is the old switch (Capture &
    Hold on its maps, Team Deathmatch elsewhere); otherwise FMO_PVP_MODES.
    Capture & Hold needs zone geometry here; the other modes are checked when
    the room is made (frontobj.new_state) and fall back to 'tdm' there."""
    maps = CH_MAPS if maps is _DEFAULT else maps
    try:
        m = int(mapno)
    except (TypeError, ValueError):
        return "tdm"
    if not JUDGE:
        return "tdm"
    if maps is not None:
        if not (maps == "all" or (isinstance(maps, set) and m in maps)):
            return "tdm"
        return "ch" if ch_zone_points(m)[0] else "tdm"
    want = assigned_mode(m, modes)
    if want == "ch":
        return "ch" if ch_zone_points(m)[0] else "tdm"
    return want


def defender_for(tile, creator, state=None):
    """Demolition's defending nation: the one holding the sector (fmowar) when
    the room is made, else the side that did not create it. OURS."""
    try:
        if state is None:
            from . import warstate
            state = warstate.war_state() if warstate.WAR != "0" else None
        if state is not None and tile is not None:
            s = state.data.get("sectors", {}).get(str(int(tile))) or {}
            if s.get("nation") in (1, 2):
                return s["nation"], f"sector {tile} is held by nation {s['nation']}"
    except Exception as e:          # the war file must never cost a sortie
        log(f"WARNING: demolition defender: war state unreadable ({e!r})")
    d = 3 - creator if creator in (1, 2) else 2
    return d, f"sector {tile} has no holder on file: the room's creator attacks"


def new_obj(mode, mapno, tile=None, creator=None, state=None):
    """(objective state, why) for a room of an objective mode, or (None, why)."""
    from . import frontobj
    if mode not in frontobj.MODES:
        return None, "not an objective mode"
    d = None
    if mode == "demo":
        d, why = defender_for(tile, creator, state)
    st, err = frontobj.new_state(mode, mapno, d)
    if st is not None and mode == "demo":
        st["defender_why"] = why
    return st, err


def _new_ch(mapno):
    return {"zones": ch_zones(mapno), "outbox": {}, "group": CH_GROUP}


MB_MATCH_FLAGS = 0x7C                  # u32 -> lobby+0x5CFA
FLAG_WAITING = 0x200
FLAG_STARTED = 0x400
MB_OBJECTIVE_KIND = 0x50               # u32 -> lobby+0x5CCE
CMD_BM_BATTLE_START = 138
CMD_BM_RESYNC = 148
RESYNC_LEN = 0xB4
CLI_START_BATTLE = 0x89                # 137, 0x611D36B0
START_REASONS = {1: "an enemy pilot entered the battlefield (79:30)",
                 2: "Start Battle was chosen (79:29)",
                 3: "the standby time ended (79:31)"}
#: Battle commands a client is known to send (the prod log's census, 2026-10-07,
#: plus the named ones); anything else on a battle channel is logged whole.
KNOWN_BATTLE_CMDS = frozenset({1, 3, 14, 15, 16, 21, 23, 24, 29, 30, 42, 43, 44, 100,
                               102, 111, 112, 115, 121, 122, 128, 129, 135, 136,
                               CLI_START_BATTLE, 139, 240, 300})


def parse_selectors(spec):
    """'505,509,513' -> {505, 509, 513}. Pure."""
    out = set()
    for piece in (spec or "").split(","):
        piece = piece.strip()
        if piece:
            out.add(int(piece, 0))
    return out


try:
    PVP_SELECTORS = parse_selectors(PVP_SELECTORS_SPEC)
except ValueError:
    PVP_SELECTORS = {505, 509, 513}
    log(f"WARNING: FMO_PVP_SELECTORS={PVP_SELECTORS_SPEC!r} is not a list of "
        f"selectors -- using 505,509,513")

_LOCK = threading.RLock()
#: room id -> room; OPEN (zone, mapno) -> the id of the room a sortie joins;
#: PILOT_ROOM battle key -> room id.
ROOMS = {}
OPEN = {}
PILOT_ROOM = {}
_NEXT = [1]


def active(zone):
    """Does a sortie in selector `zone` go into a judged/matching room?"""
    try:
        z = int(zone)
    except (TypeError, ValueError):
        return False
    return z in PVP_SELECTORS and (MATCHING or JUDGE or WAR)


def _open_room(zone, mapno):
    rid = OPEN.get((int(zone), int(mapno)))
    room = ROOMS.get(rid) if rid is not None else None
    if room is None or room.get("verdict") is not None:
        return None
    return room


def peek(zone, mapno, now=None):
    """(role, room) a sortie onto `mapno` in selector `zone` would take now:
    'create' (with a plan of the room it would make: its mode and areas),
    'wait' (the room is waiting), 'late' (it started),
    or (None, None) outside the matching selectors. Changes nothing."""
    if mapno is None or not active(zone):
        return None, None
    with _LOCK:
        room = _open_room(zone, mapno)
    if room is None:
        # the room this sortie would create, as far as its block needs it
        mode = mode_for(mapno)
        return "create", {"plan": True, "zone": int(zone), "mapno": int(mapno),
                          "started": None, "mode": mode,
                          "ch": _new_ch(mapno) if mode == "ch" else None}
    return ("wait" if room["started"] is None else "late"), room


def block_knobs(role, room=None, side=None, tile=None):
    """The mission_fields() keywords a sortie with `role` is served: the
    block+0x7C bits, the objective kind and, in a Capture & Hold room, the
    areas with their current owners; in a Capture the Flag / Beacon /
    Demolition room its points (frontobj.block_knobs; Demolition's kind is
    per side, so `side` is the pilot's nation and `tile` its sector, which
    decide a room this sortie is about to create). {} outside a room."""
    if role is None:
        return {}
    out = {}
    from . import frontobj
    if (room or {}).get("mode") in frontobj.MODES:
        st = room.get("obj")
        if st is None and room.get("plan"):
            st = new_obj(room["mode"], room["mapno"], tile, side)[0]
        if st is not None:
            if MATCHING:
                out["match_flags"] = (FLAG_WAITING | FLAG_STARTED) if role == "late" else FLAG_WAITING
            out.update(frontobj.block_knobs(st, side))
            return out
    if MATCHING:
        out["match_flags"] = (FLAG_WAITING | FLAG_STARTED) if role == "late" else FLAG_WAITING
    if JUDGE and OBJECTIVE_KIND:
        out["objective_kind"] = OBJECTIVE_KIND
    ch = (room or {}).get("ch") if (room or {}).get("mode") == "ch" else None
    if ch and ch["zones"]:
        from . import missionblock
        if CH_KIND:
            out["objective_kind"] = CH_KIND
        out["rects"] = [missionblock.rect_record(z["id"], z["lo"], z["hi"], z["owner"])
                        for z in ch["zones"]]
        out["rect_groups"] = [missionblock.rect_group_record(ch["group"])]
    return out


def start_stamp(role, room=None):
    """block+0x48 for a late joiner: the room's own start, so its HUD clock and
    Battle Review agree with everyone else's. None = the sortie's own stamp."""
    if role == "late" and room is not None and room.get("started"):
        return int(room["started"])
    return None


def join_verdict(zone, mapno, nation, gid, now=None, cap=None, window=None):
    """The join-in rule for a matching room: (code, why, pct), or None when the
    sortie is not into one (sortie.join_verdict decides then). While the room
    waits anyone may join (10 a side); after the start the 5-minute window
    runs from the START, not from the creation (SE 060804)."""
    from . import sortie, warmap
    if not MATCHING or mapno is None or not active(zone):
        return None
    now = time.time() if now is None else now
    cap = warmap.S15F_SIDE_CAP if cap is None else cap
    window = sortie.JOIN_WINDOW if window is None else window
    with _LOCK:
        room = _open_room(zone, mapno)
        if room is None:
            return None, "creates the battle map (WAITING FOR OPPONENTS)", None
        mine = [m for m in room["members"].values()
                if m["side"] == nation and not m.get("out")]
        creator = nation == room["creator_side"]
        if cap > 0 and len(mine) >= cap:
            return sortie.JOIN_CODE_SIDE_FULL, (f"nation {nation} already has "
                                                f"{len(mine)} of {cap} in this room"), None
        if gid and any(m.get("gid") == gid for m in room["members"].values()):
            return None, "joins its own battle group in the room", None
        if room["started"] is None:
            return None, ("joins the waiting room" + ("" if creator else
                          " as the opponent: the battle starts when it pops in")), \
                (None if creator else 100)
        elapsed = now - room["started"]
        if window <= 0 or elapsed <= window:
            return None, (f"joins {int(elapsed)} s after the start"), \
                (None if creator else sortie.join_pct(elapsed, window))
        return sortie.JOIN_CODE_LATE, (f"{int(elapsed)} s since the battle started, "
                                       f"the join-in window is {window} s"), None


def join(bkey, account, zone, mapno, tile, nation, now=None, gid=None):
    """Put the pilot `bkey` into the room for (zone, mapno), creating it.
    Returns (role, room), or (None, None) outside the matching selectors."""
    if mapno is None or not active(zone):
        return None, None
    now = time.time() if now is None else now
    with _LOCK:
        _leave(bkey)
        room = _open_room(zone, mapno)
        role = "create"
        if room is None:
            rid = _NEXT[0]
            _NEXT[0] += 1
            room = {"id": rid, "zone": int(zone), "mapno": int(mapno), "tile": tile,
                    "created": now, "started": None if MATCHING else int(now),
                    "reason": None if MATCHING else 0, "npc": not MATCHING,
                    "creator_side": nation, "members": {}, "verdict": None,
                    "fielded": set(), "war_done": False, "pvp": False,
                    "mode": mode_for(mapno), "ch": None, "obj": None}
            if room["mode"] not in ("ch", "tdm"):
                room["obj"], _why = new_obj(room["mode"], mapno, tile, nation)
                if room["obj"] is None:
                    log(f"PVP ROOM {rid}: {room['mode']} on map {mapno} has no geometry "
                        f"({_why}): Team Deathmatch")
                    room["mode"] = "tdm"
                else:
                    from . import frontobj
                    log(f"PVP ROOM {rid}: {room['mode'].upper()} on map {mapno}: "
                        + frontobj.describe(room)
                        + (f"; defender nation {room['obj']['defender']} "
                           f"({room['obj'].get('defender_why')}), areas from "
                           f"{room['obj'].get('src')}" if room["mode"] == "demo" else ""))
            if room["mode"] == "ch":
                room["ch"] = _new_ch(mapno)
                log(f"PVP ROOM {rid}: CAPTURE & HOLD on map {mapno}: "
                    + "; ".join(f"{chr(65 + z['index'])} at ({z['center'][0]:g}, "
                                f"{z['center'][2]:g}) y {z['center'][1]:g}"
                                for z in room["ch"]["zones"])
                    + f" ({ch_zone_points(mapno)[1]}); hold {CH_HOLD} s, half-size "
                    f"{CH_HALF}, still {CH_STILL or 'off'}")
            ROOMS[rid] = room
            OPEN[(int(zone), int(mapno))] = rid
        else:
            role = "wait" if room["started"] is None else "late"
        room["members"][bkey] = {"account": account, "side": nation, "role": role,
                                 "joined": now, "gid": gid, "out": None,
                                 "out_at": None, "start_sent": False,
                                 "popped": None, "delivered": False}
        PILOT_ROOM[bkey] = room["id"]
        if len({m["side"] for m in room["members"].values()}) >= 2:
            room["pvp"] = True
    log(f"PVP ROOM {room['id']}: {account or bkey} (nation {nation}) {role}s the "
        f"room on selector {zone} map {mapno} (tile {tile}); "
        + ("WAITING FOR OPPONENTS, block+0x7C = 0x200" if role != "late" and MATCHING
           else f"started {int(now - room['started'])} s ago: block+0x7C = 0x600, "
                f"cmd 148 resync after the self-POP" if role == "late"
           else "FMO_PVP_MATCHING=0: no waiting")
        + f"; {len(room['members'])} pilot(s) in it")
    return role, room


def _leave(bkey):
    rid = PILOT_ROOM.pop(bkey, None)
    room = ROOMS.get(rid)
    if room is not None and bkey in room["members"]:
        m = room["members"][bkey]
        if not m["delivered"]:
            m["out"] = m["out"] or "sortied again"
            m["out_at"] = m["out_at"] or time.time()
            m["delivered"] = True
        _gc(room)


def _gc(room):
    """Forget a room once it has a verdict and every member has had it."""
    if room["verdict"] is not None and all(m["delivered"] for m in room["members"].values()):
        ROOMS.pop(room["id"], None)
        for k, rid in list(PILOT_ROOM.items()):
            if rid == room["id"]:
                PILOT_ROOM.pop(k, None)


def room_of(bkey):
    with _LOCK:
        rid = PILOT_ROOM.get(bkey)
        return ROOMS.get(rid) if rid is not None else None


def _chan_room(chan):
    from . import referee
    room = room_of(referee.chan_bkey(chan))
    if room is None:
        room = room_of(chan.addr[0])
    return room


def _chan_member(chan, room):
    from . import referee
    for k in (referee.chan_bkey(chan), chan.addr[0]):
        if k in room["members"]:
            return k, room["members"][k]
    acct = getattr(chan, "account", None)
    for k, m in room["members"].items():
        if acct and m.get("account") == acct:
            return k, m
    return None, None


def in_matching_room(chan):
    """True when this battle channel's start belongs to a matching room (the
    generic FMO_BATTLE_START stands down)."""
    return MATCHING and _chan_room(chan) is not None


def squad_allowed(chan):
    """May the NPC squad pop on this battle channel now? Never while the room
    waits; after the start only when the room fights NPCs (reason 2/3, or
    FMO_PVP_NPC_VS_PILOTS)."""
    if not MATCHING:
        return True
    room = _chan_room(chan)
    if room is None:
        return True
    return room["started"] is not None and room["npc"]


def start(room, reason, now=None, why=""):
    """Start a waiting room (once). Every member's channel is sent cmd 138 on
    its next datagram (tick_chan); a late joiner gets cmd 148 instead."""
    now = time.time() if now is None else now
    with _LOCK:
        if room["started"] is not None or room["verdict"] is not None:
            return False
        room["started"] = int(now)
        room["reason"] = reason
        room["npc"] = reason in (2, 3) or NPC_VS_PILOTS
    from . import referee
    for k in list(room["members"]):
        st = referee.BATTLE_STATE.get(k)
        if isinstance(st, dict):
            st["started_at"] = room["started"]
    log(f"PVP ROOM {room['id']}: BATTLE START, reason {reason} = "
        f"{START_REASONS.get(reason, '?')}{' -- ' + why if why else ''}; "
        f"{int(now - room['created'])} s after the room was created; NPC squad "
        f"{'pops now' if room['npc'] else 'stays out (pilots only)'}; cmd 138 "
        f"goes to every member's battle channel on its next datagram")
    return True


RESYNC_RECTS = 0x08                    # +0x08 + 4*i: rect id, owner, flags (bits 0..1)
RESYNC_RECT_MAX = (0x88 - RESYNC_RECTS) // 4


RESYNC_HOLDERS = 0x94                  # four {object, holder} pairs -> 0x61002160


def resync_record(started, flags=0x3, rects=(), holders=()):
    """cmd 148, 0xB4 bytes: the bits, the start time, each rect's (id,
    owner, flags), zeros for +0x798, and the holder table (who carries the
    container / a beacon: (object, holder UnitID) pairs, each in slot
    object & 3; none = nobody holds anything)."""
    body = bytearray(RESYNC_LEN)
    struct.pack_into("<II", body, 0, flags & 0x3, int(started) & 0xFFFFFFFF)
    for i, (rid, owner, rflags) in enumerate(list(rects)[:RESYNC_RECT_MAX]):
        at = RESYNC_RECTS + 4 * i
        body[at], body[at + 1], body[at + 2] = rid & 0xFF, owner & 0xFF, rflags & 0x3
    for obj, holder in holders:
        if obj and holder:
            struct.pack_into("<II", body, RESYNC_HOLDERS + 8 * (obj & 3),
                             obj & 0xFFFFFFFF, holder & 0xFFFFFFFF)
    return fmoworld.record(CMD_BM_RESYNC, bytes(body))


def _ch_rects(room):
    from . import missionblock
    if room.get("obj"):
        from . import frontobj
        return frontobj.resync_rects(room["obj"])
    ch = room.get("ch") if room.get("mode") == "ch" else None
    return [(z["id"], z["owner"], missionblock.RECT_FLAG_DRAWN)
            for z in (ch or {}).get("zones", ())]


def tick_chan(chan, addr, now=None):
    """On every battle-channel datagram once the self-POP is out: start the
    room on a hostile pilot's arrival or the wait running out, and queue this
    channel's cmd 138 (or cmd 148 for a late joiner) once. In a Capture &
    Hold room: note where this pilot is, run the areas, hand it its cmd 133s."""
    if not getattr(chan, "popped", False):
        return
    room = _chan_room(chan)
    if room is None:
        return
    now = time.time() if now is None else now
    k, m = _chan_member(chan, room)
    if m is None:
        return
    if MATCHING:
        _tick_start(chan, addr, room, k, m, now)
    if room.get("mode") == "ch" and room.get("ch"):
        from . import referee
        if getattr(chan, "pos_src", None) in referee.BATTLE_POS_SRCS and \
                getattr(chan, "pos", None) is not None:
            m["pos"] = tuple(chan.pos[:3])
        ch_tick(room, now)
        box = room["ch"]["outbox"].pop(k, None)
        if box:
            chan.pending.extend(box)
    if room.get("obj"):
        _tick_obj_chan(chan, room, k, m, now)


def _tick_obj_chan(chan, room, k, m, now):
    """A Capture the Flag / Beacon / Demolition room, on one pilot's datagram:
    note where it stands (and the ids its client knows it by), POP the
    objects to it once, run the rules, hand it its records."""
    from . import frontobj, referee
    if getattr(chan, "pos_src", None) in referee.BATTLE_POS_SRCS and \
            getattr(chan, "pos", None) is not None:
        m["pos"] = tuple(chan.pos[:3])
    m["addr"] = getattr(chan, "addr", None)
    try:
        m["uid"] = chan.self_unit()
    except Exception:
        m["uid"] = m.get("uid")
    pops = frontobj.pops_for(room, k)
    if pops:
        chan.pending.extend(pops)
        log(f"[udp {chan.addr[0]}:{chan.addr[1]}] -> PVP ROOM {room['id']}: POP "
            f"{len(pops)} objective object(s) (model class 6 sub "
            f"{frontobj.OBJ_MODEL_SUB}, body+0x8E 0x58): "
            + ", ".join(f"{o['kind']} {o['id']:#x}" for o in room["obj"]["objects"].values()))
    frontobj.tick(room, now)
    for ev in room["obj"]["outbox"].pop(k, None) or ():
        chan.pending.append(frontobj.render(room, ev, chan))


def _tick_start(chan, addr, room, k, m, now):
    if m["popped"] is None:
        m["popped"] = now
    if room["started"] is None:
        if m["side"] != room["creator_side"]:
            start(room, 1, now, why=f"{m.get('account') or k} (nation {m['side']}) popped in")
        elif PVP_WAIT > 0 and now - room["created"] >= PVP_WAIT:
            start(room, 3, now, why=f"{PVP_WAIT} s without an opponent")
    if room["started"] is None or m["start_sent"]:
        return
    m["start_sent"] = True
    if m["role"] == "late":
        _rr = _ch_rects(room)
        _hp = []
        if room.get("obj"):
            from . import frontobj
            m["addr"] = getattr(chan, "addr", None)
            _hp = frontobj.holder_pairs(room, chan)
        chan.pending.append(resync_record(room["started"], rects=_rr, holders=_hp))
        log(f"[udp {addr[0]}:{addr[1]}] -> PVP ROOM {room['id']}: cmd {CMD_BM_RESYNC} "
            f"LATE-JOINER RESYNC (0x611EFF04): bits 0x3 -> block+0x7C 0x600, start "
            f"{room['started']} -> block+0x48, {RESYNC_LEN}B; joined "
            f"{int(m['joined'] - room['started'])} s after the start"
            + (f"; area owners {[o for _i, o, _f in _rr]}" if _rr else ""))
        return
    try:
        rec = fmoworld.record_battle_start(room["reason"], start_gametime=room["started"])
    except ValueError as e:
        log(f"[udp {addr[0]}:{addr[1]}] WARNING: PVP ROOM {room['id']}: cmd 138 refused: {e}")
        return
    chan.pending.append(rec)
    log(f"[udp {addr[0]}:{addr[1]}] -> PVP ROOM {room['id']}: BM cmd 138 BATTLE START, "
        f"reason {room['reason']} ({START_REASONS.get(room['reason'])}), start "
        f"{room['started']} -> block+0x48; 0x61001F40 sets block+0x7C 0x400 and the "
        f"waiting overlay should go")


def note_cmd(chan, addr, cmd, body, now=None):
    """Every record a battle channel carries: cmd 137 = Start Battle; any
    command not in KNOWN_BATTLE_CMDS is logged whole, once per channel."""
    if cmd == CLI_START_BATTLE:
        room = _chan_room(chan)
        if room is None or not MATCHING:
            if not getattr(chan, "_pvp_137_said", False):
                chan._pvp_137_said = True
                log(f"[udp {addr[0]}:{addr[1]}]   cmd 137 = START BATTLE (menu 0:130, "
                    f"0x611D36B0) outside a matching room -- nothing to start")
            return
        if room["started"] is None:
            log(f"[udp {addr[0]}:{addr[1]}]   cmd 137 = START BATTLE chosen in "
                f"PVP ROOM {room['id']}")
            start(room, 2, now, why="a member chose Start Battle (cmd 137)")
        return
    if cmd in KNOWN_BATTLE_CMDS:
        return
    seen = getattr(chan, "_pvp_unknown", None)
    if seen is None:
        seen = chan._pvp_unknown = set()
    if cmd in seen:
        return
    seen.add(cmd)
    room = _chan_room(chan)
    log(f"[udp {addr[0]}:{addr[1]}]   UNKNOWN BATTLE CMD {cmd} ({len(body)}B)"
        + (f" in PVP ROOM {room['id']} ({'waiting' if room['started'] is None else 'started'})"
           if room else "") + ", first time on this channel:" + os.linesep
        + hexdump(bytes(body), indent="      "))


# --------------------------------------------------------------------------- #
# P2: THE ROOM JUDGE (lifted from coliseum.Coliseum.judge)
# --------------------------------------------------------------------------- #
def judge_counts(counts, fielded, started, now, limit):
    """(winner, why) once a started room is decided, else None. `counts` =
    {nation: units standing}, `fielded` = the nations that ever had a unit in
    it. winner 0 = a draw. Pure.
    A side that has fielded units and has none standing loses. At the time
    limit the side with more units standing wins and a tie is a draw.
    WARNING: the time-limit rule (more standing wins, a tie draws) is OURS, a
    guess carried over from the Coliseum judge; SE only says a side wins by
    disabling every enemy."""
    if started is None:
        return None
    a, b = counts.get(1, 0), counts.get(2, 0)
    if 1 in fielded and 2 in fielded:
        if a == 0 and b == 0:
            return 0, "both sides are out"
        if a == 0 or b == 0:
            w = 2 if a == 0 else 1
            return w, (f"nation {3 - w} has no unit left standing "
                       f"(O.C.U. {a} vs U.S.N. {b})")
    elif fielded and all(counts.get(n, 0) == 0 for n in fielded):
        return 0, "the room emptied before an opponent fielded a unit"
    if limit and limit > 0 and now >= started + limit:
        if a == b:
            return 0, f"time limit, {a} vs {b} standing: a draw (OUR rule)"
        w = 1 if a > b else 2
        return w, f"time limit, O.C.U. {a} vs U.S.N. {b} standing (OUR rule)"
    return None


def member_out(m, bkey, now, online, deaths):
    """Why member `m` is no longer standing, or None."""
    from . import referee
    if m.get("out"):
        return m["out"]
    acct = m.get("account") or bkey
    died = deaths.get(acct)
    if died is not None and died >= m["joined"]:
        return "destroyed"
    st = referee.BATTLE_STATE.get(bkey) or {}
    esc = st.get("escaped")
    if esc and esc[2] >= m["joined"]:
        return "ejected (Emergency Escape)"
    if not online(acct):
        return "dropped (no game session)"
    return None


def units_standing(room, now, online, deaths):
    """({nation: units standing}, [detail], {nations fielded}) -- pilots not
    out plus the live units of every NPC squad the room's pilots were given,
    counted for the squad's nation. A nation is FIELDED once it has had a
    pilot in the room or a squad popped, dead or alive."""
    from . import referee
    counts, detail, squads = {1: 0, 2: 0}, [], []
    fielded = {m["side"] for m in room["members"].values() if m["side"] in (1, 2)}
    for k, m in room["members"].items():
        why = member_out(m, k, now, online, deaths)
        if why and not m.get("out_at") and not why.startswith("dropped"):
            # a drop is judged live (a keepalive gap is not a death); the rest stick
            m["out"] = m.get("out") or why
            m["out_at"] = now
        if why:
            detail.append(f"{m.get('account') or k} out ({why})")
            continue
        counts[m["side"]] = counts.get(m["side"], 0) + 1
    for k, m in room["members"].items():
        sq = (referee.BATTLE_STATE.get(k) or {}).get("squad")
        if sq and not any(s is sq for s in squads):
            squads.append(sq)
    for sq in squads:
        n = sq.get("nation")
        alive = [u for u in sq.get("ids", ()) if u not in sq.get("dead", ())]
        if n in (1, 2) and sq.get("ids"):
            fielded.add(n)
            counts[n] = counts.get(n, 0) + len(alive)
            detail.append(f"NPC squad nation {n}: {len(alive)}/{len(sq.get('ids', ()))} up")
    return counts, detail, fielded


def _limit():
    from . import missionblock
    return missionblock.MISSION_TIME


# --------------------------------------------------------------------------- #
# P4: the Capture & Hold areas (see the block at the top)
# --------------------------------------------------------------------------- #
def ch_inside(zone, pos):
    """Is `pos` inside the area? X and Z only: the y the motion state carries
    is the wanzer's, and the box's y band is drawn round a ground we only
    estimate."""
    return (pos is not None and zone["lo"][0] <= pos[0] <= zone["hi"][0]
            and zone["lo"][2] <= pos[2] <= zone["hi"][2])


def _ch_broadcast(room, rec):
    box = room["ch"]["outbox"]
    for k, m in room["members"].items():
        if not m.get("delivered"):
            q = box.setdefault(k, [])
            if len(q) < 64:
                q.append(rec)


def _ch_send(room, zone, msg=0):
    _ch_broadcast(room, fmoworld.record_zone_state(room["ch"]["group"], zone["index"],
                                                   zone["owner"], msg))


def ch_tick(room, now=None, online=None, deaths=None, hold=None, still=None):
    """Advance every area of a started Capture & Hold room from where its
    pilots stand. Queues cmd 133 for every member; decides the room when one
    nation holds every area. Returns [(area letter, event)] for the log and
    the selftest."""
    from . import battleend, trade
    ch = room.get("ch") if room.get("mode") == "ch" else None
    if not ch or not ch["zones"] or room["started"] is None or room["verdict"] is not None:
        return []
    now = time.time() if now is None else now
    online = online or (lambda a: trade.session_for_account(a) is not None)
    deaths = battleend.PILOT_DEATHS if deaths is None else deaths
    hold = CH_HOLD if hold is None else hold
    still = CH_STILL if still is None else still
    up = {k: m for k, m in room["members"].items()
          if m["side"] in (1, 2) and m.get("pos") is not None
          and not m.get("delivered") and not member_out(m, k, now, online, deaths)}
    events = []
    for z in ch["zones"]:
        name = chr(65 + z["index"])
        cap = z["cap"]
        if cap is not None:
            m = up.get(cap["who"])
            why = None
            if m is None:
                why = "the captor is out (destroyed, ejected, withdrawn or dropped)"
            elif not ch_inside(z, m["pos"]):
                why = "the captor left the area"
            elif still and math.hypot(m["pos"][0] - cap["at"][0],
                                      m["pos"][2] - cap["at"][2]) > still:
                why = f"the captor moved more than {still} (it must hold still, as crouched)"
            if why:
                z["cap"] = None
                events.append((name, "stopped"))
                log(f"PVP ROOM {room['id']}: AREA {name}: nation {cap['side']}'s capture "
                    f"by {cap['who']} STOPPED after {int(now - cap['since'])} s: {why}")
                continue
            left = hold - (now - cap["since"])
            if left <= 0:
                z["owner"], z["cap"] = cap["side"], None
                events.append((name, f"taken by {cap['side']}"))
                _ch_send(room, z, fmoworld.zone_msg("done", z["index"]))
                log(f"PVP ROOM {room['id']}: AREA {name} TAKEN by nation {cap['side']} "
                    f"({cap['who']}, {hold} s); cmd 133 owner {cap['side']} + "
                    f"\"area {name} captured\" to every member")
                continue
            due = [s for s in fmoworld.ZONE_MSG_COUNTDOWN
                   if s < hold and left <= s and s not in cap["said"]]
            if due:
                cap["said"].update(due)
                _ch_send(room, z, fmoworld.zone_msg("count", z["index"], min(due)))
                events.append((name, f"{min(due)} s"))
            continue
        for k, m in sorted(up.items(), key=lambda kv: kv[1].get("joined", 0)):
            if m["side"] != z["owner"] and ch_inside(z, m["pos"]):
                z["cap"] = {"side": m["side"], "who": k, "since": now,
                            "at": m["pos"], "said": set()}
                events.append((name, f"started by {m['side']}"))
                _ch_send(room, z, fmoworld.zone_msg("start", z["index"]))
                log(f"PVP ROOM {room['id']}: AREA {name}: nation {m['side']} "
                    f"({m.get('account') or k}) STARTS the capture at "
                    f"({m['pos'][0]:.0f}, {m['pos'][2]:.0f}); {hold} s, owner now "
                    f"{z['owner'] or 'nobody'}")
                break
    owners = {z["owner"] for z in ch["zones"]}
    if len(owners) == 1 and owners <= {1, 2}:
        w = owners.pop()
        _decide(room, w, f"nation {w} holds all {len(ch['zones'])} areas (Capture & Hold)",
                now)
    return events


def ch_time_verdict(room, counts, fielded, now, limit):
    """At the time limit of a Capture & Hold room: (winner, why). More areas
    held wins (OUR rule; SE names no time-limit winner); a tie goes to the
    units-standing rule, which may draw."""
    zs = room["ch"]["zones"]
    a = sum(1 for z in zs if z["owner"] == 1)
    b = sum(1 for z in zs if z["owner"] == 2)
    if a != b:
        w = 1 if a > b else 2
        return w, f"time limit, areas held O.C.U. {a} vs U.S.N. {b} (OUR rule)"
    v = judge_counts(counts, fielded, room["started"], now, limit)
    if v is None:
        return 0, f"time limit, areas held {a} vs {b}: a draw (OUR rule)"
    return v[0], f"time limit, areas held {a} vs {b}; " + v[1]


def tick(now=None, online=None, deaths=None):
    """Start waiting rooms whose wait ran out, close abandoned ones, judge the
    started ones. Returns the rooms decided by this call."""
    from . import battleend, trade
    now = time.time() if now is None else now
    online = online or (lambda a: trade.session_for_account(a) is not None)
    deaths = battleend.PILOT_DEATHS if deaths is None else deaths
    decided = []
    with _LOCK:
        rooms = [r for r in ROOMS.values() if r["verdict"] is None]
    for room in rooms:
        if room["started"] is None:
            if all(member_out(m, k, now, online, deaths) or m.get("delivered")
                   for k, m in room["members"].items()):
                _decide(room, 0, "everyone left while it waited (no battle)", now,
                        war=False)
                continue
            if MATCHING and PVP_WAIT > 0 and now - room["created"] >= PVP_WAIT:
                start(room, 3, now, why=f"{PVP_WAIT} s without an opponent")
            continue
        if not JUDGE:
            continue
        if room.get("mode") == "ch":
            ch_tick(room, now, online, deaths)
            if room["verdict"] is not None:
                decided.append(room)
                continue
        if room.get("obj"):
            from . import frontobj
            frontobj.tick(room, now, online, deaths)
            if room["verdict"] is not None:
                decided.append(room)
                continue
        counts, detail, fielded = units_standing(room, now, online, deaths)
        room["fielded"] |= fielded
        lim = _limit()
        if room.get("mode") == "ch":
            v = judge_counts(counts, room["fielded"], room["started"], now, 0)
            if v is None and lim and lim > 0 and now >= room["started"] + lim:
                v = ch_time_verdict(room, counts, room["fielded"], now, lim)
        elif room.get("obj"):
            # every enemy disabled wins in every mode (SE); the limit is the mode's
            v = judge_counts(counts, room["fielded"], room["started"], now, 0)
            if v is None and lim and lim > 0 and now >= room["started"] + lim:
                v = frontobj.time_verdict(room, counts, room["fielded"], now, lim)
        else:
            v = judge_counts(counts, room["fielded"], room["started"], now, lim)
        if v is not None:
            _decide(room, v[0], v[1] + (f" [{'; '.join(detail)}]" if detail else ""), now)
            decided.append(room)
    return decided


def _decide(room, winner, why, now, war=True):
    with _LOCK:
        if room["verdict"] is not None:
            return
        room["verdict"] = {"winner": winner, "why": why, "at": now}
        if OPEN.get((room["zone"], room["mapno"])) == room["id"]:
            OPEN.pop((room["zone"], room["mapno"]), None)
    log(f"PVP ROOM {room['id']} (selector {room['zone']} map {room['mapno']}): VERDICT "
        + ({1: "O.C.U. WINS", 2: "U.S.N. WINS"}.get(winner, "DRAW / no winner"))
        + f" -- {why}; each pilot's 0x014C follows on its keepalive")
    if war:
        settle_war(room)
    with _LOCK:
        _gc(room)


# --------------------------------------------------------------------------- #
# P3: THE WAR, ONCE PER BATTLE
# --------------------------------------------------------------------------- #
def settle_war(room, state=None):
    """Feed fmowar.settle the room's verdict once: every nation that had
    PILOTS in the room, won = it is the winner, pvp = both nations had pilots
    (fmowar weight 2). A draw moves nothing. Returns [(nation, what)]."""
    from . import referee, warstate
    if not WAR or room.get("war_done"):
        return []
    room["war_done"] = True
    v = room.get("verdict") or {}
    winner = v.get("winner")
    tile = room.get("tile")
    if not winner:
        log(f"PVP ROOM {room['id']}: WAR: no winner, nothing settled")
        return []
    if tile is None or (state is None and (warstate.WAR == "0" or fmowar is None)):
        log(f"PVP ROOM {room['id']}: WAR: no tile / FMO_WAR off, nothing settled")
        return []
    if int(room["zone"]) // 100 in (1, 3):
        log(f"PVP ROOM {room['id']}: WAR: selector {room['zone']} is a Controlled Zone")
        return []
    st = state if state is not None else warstate.war_state()
    if st is None:
        return []
    if state is None:
        warstate._war_tick(st)
    sides = sorted({m["side"] for m in room["members"].values() if m["side"] in (1, 2)})
    pvp = len(sides) >= 2
    for k in room["members"]:
        bs = referee.BATTLE_STATE.get(k)
        if isinstance(bs, dict):
            bs["pvp"] = pvp
    out = []
    for n in sides:
        s, what = st.settle(int(tile), n, won=(n == winner), pvp=pvp)
        out.append((n, what))
        log(f"PVP ROOM {room['id']}: WAR STATE tile {tile}, nation {n} "
            f"{'WON' if n == winner else 'LOST'}{' (PvP, weight 2)' if pvp else ''} -> {what}; "
            f"sector now nation {s.get('nation')} at {s.get('control')}%")
    return out


def war_by_room(bkey):
    """True when this pilot's room settles the war (its own war_settle must not)."""
    return WAR and room_of(bkey) is not None


def judged(bkey):
    """True when this pilot's battle end comes from its room's judge."""
    return JUDGE and room_of(bkey) is not None


def end_due(sess, conn_id, now=None, online=None, deaths=None):
    """On the keepalive: this pilot's 0x014C from the room verdict, once. A
    destroyed/ejected pilot that has waited FMO_PVP_DEATH_WAIT without a
    verdict ends with a loss. Returns the packets."""
    if not JUDGE:
        return []
    try:
        bkey = sess.battle_key()
    except Exception:
        return []
    room = room_of(bkey)
    if room is None or getattr(sess, "battle_end_done", False):
        return []
    now = time.time() if now is None else now
    tick(now, online, deaths)
    m = room["members"].get(bkey)
    if m is None or m["delivered"]:
        return []
    v = room.get("verdict")
    if v is not None:
        won = bool(v["winner"]) and v["winner"] == m["side"]
        why = f"PvP room {room['id']}: {v['why']}"
    elif (m.get("out") and m.get("out_at") and DEATH_WAIT > 0
          and now - m["out_at"] >= DEATH_WAIT):
        won = False
        why = (f"PvP room {room['id']}: {m['out']} and no verdict after "
               f"{DEATH_WAIT} s (FMO_PVP_DEATH_WAIT)")
    else:
        return []
    with _LOCK:
        m["delivered"] = True
    from . import referee
    outs = []
    sess.battle_end_done = True
    st = referee.BATTLE_STATE.get(bkey)
    if isinstance(st, dict):
        st["ended"] = True
    log(f"{sess.peer}   PVP ROOM {room['id']}: this pilot (nation {m['side']}) "
        f"{'WON' if won else 'LOST'}: {why}")
    rp = sess.battle_result_push(conn_id, f"the battle end ({why})", won=won)
    if rp:
        outs.append(rp)
    pr = sess.penalty_report_push(conn_id)
    if pr:
        outs.append(pr)
    be = sess.battle_end_push(conn_id, why=why, won=won)
    if be:
        outs.append(be)
    with _LOCK:
        _gc(room)
    return outs


def withdrew(bkey, why="withdrew (0x013D)"):
    """The pilot left the battle on its own: out of the room, result paid by
    the withdraw path."""
    room = room_of(bkey)
    if room is None:
        return
    with _LOCK:
        m = room["members"].get(bkey)
        if m is not None:
            m["out"] = m["out"] or why
            m["out_at"] = m["out_at"] or time.time()
            m["delivered"] = True
        _gc(room)
    log(f"PVP ROOM {room['id']}: {bkey} {why}; counted out of the room")


def reset():
    """Forget every room (the selftest)."""
    with _LOCK:
        ROOMS.clear()
        OPEN.clear()
        PILOT_ROOM.clear()
