"""Frontline objectives past Capture & Hold: Capture the Flag, Beacon Delivery, Demolition."""
import math
import os
import struct
import time
from .deps import fmoworld
from .knobs import _env_int
from .wirelog import log


# --------------------------------------------------------------------------- #
# WHAT SE PUBLISHED (www.playonline.com/fmo/topics, 2006)
# --------------------------------------------------------------------------- #
# Capture the Flag (060721): one "flag unit", a classified container, on the
# battle map. It cannot be destroyed, only a wanzer (player or NPC) carries
# it, and it shows on the private and share radar. A wanzer that comes close
# HOLDS it automatically; the holder has no jet backpack, a 40 km/h top speed
# and no defence of any element. Knocked down, or crouching, releases the
# hold. The battle group that carries it to the designated point wins.
# Beacon Delivery (060929): each army has its own guidance beacon somewhere on
# the map (same carry rules), and one target area near the CENTRE of the map.
# The side whose beacon is SET first in the target area (carried in, hold
# released) wins; setting the ENEMY's beacon there makes the enemy win.
# Demolition (060920): five target areas; the attacker ("施設破壊" on the map
# selector) plants a time bomb in any one by crouching 10 s inside it (knocked
# down or destroyed inside the 10 s stops it); one bomb per map; it explodes
# 70 s later and the attacker wins. The defender ("陣地防衛") starts a defuse
# by crouching 5 s in the bomb's area and finishes it with 20 s more: the
# defender wins. No plant before the time limit: the defender wins. The bomb
# is stealthed (no radar). In every mode, disabling every enemy also wins.
#
# --------------------------------------------------------------------------- #
# WHAT THE CLIENT DOES (FrontMissionOnline.dll.unpacked, static 2026-10-08)
# --------------------------------------------------------------------------- #
# The carried object is a battle UNIT of model class 6, subclass 2:
#   * 0x611ED660 picks the model kind from POP body+0x8E (bit 3 + high nibble
#     5 -> kind 6) and passes body+0x8B as the sub; 0x611FC210 kind 6 ->
#     0x611F4490 -> 0x611F1A20(slot, 6, sub) writes unit+0x3B5 = 6 and
#     unit+0x3B9 = sub.
#   * 0x61048E00 = "class 6 and sub 2 or 3": such a unit is never a target
#     (0x61049728, 0x610556F6: SE's "cannot be destroyed").
#   * sub 2 only: the POP arm 0x611ED9E0 copies the POP position (body+0x1C,
#     +0x20, +0x24) into the object's DROP slot (0x611EDA4D..0x611EDA7B), and
#     each frame 0x610648B1 puts an object nobody holds at its drop slot and
#     a held one on its holder (0x61064A29). So the POP position is where it
#     lies until someone takes it.
#   * the holder table, globals+0x218 (0x61002160 clears and refills it): four
#     {u32 object UnitID, u32 holder UnitID} pairs, a pair landing in slot
#     (object & 3) and only when both are non-zero; the drop slots, 12 bytes
#     each, follow at +0x24 + 12 * (object & 3) (0x610020D0 / 0x610020F0).
#     Four objects at most, and their ids must differ in the low two bits.
#   * cmd 140 (arm 0x611EFBBF): +0x00 object, +0x04 holder, +0x08 the whole
#     four-pair table (refilled). Prints 32:17 "%s seized the classified
#     container." when block+0x50 == 10, else 32:19 "%s seized the %s army's
#     guidance beacon." with the army from the OBJECT's unit+0x80 (= POP
#     body+0x7C, the nation; 1/2 -> 105:3/4, anything else reads as O.C.U.).
#   * cmd 141 (arm 0x611EFD1E): +0x00 object, +0x04 the table AFTER the drop,
#     +0x24/+0x28/+0x2C f32 x/y/z -> the object's drop slot. Prints 32:18 /
#     32:20 naming the holder the client's OLD table had.
#   * the holder's limits are the client's own: 0x6106440D refuses the jet
#     with 32:16 (container) or 32:21 (beacon) while the unit holds one;
#     0x610DD4E0 / 0x610DD460 answer "who holds it" for the battle scripts.
#   * block+0x50 (the objective kind): 10 = container (Capture the Flag);
#     any other value makes 140/141 print the BEACON lines. 0x610E692A has
#     briefing lines for 1..5 only (79:12..15, 79:25): 3 = 79:14 "There is a
#     target area. Check the share radar.", 4 = 79:15 "Enemies are approaching
#     the defense base. Check the share radar." 79:26..28 (Safeguard,
#     Territorial Conquest, Capture the Flag) have no reader.
#   * the client sends NOTHING for a hold, a release or a crouch: no sender
#     of 140/141 exists. The battle server authored every hold and drop.
#   * BB/F14/D25.DAT (message res 0x1B335 + 12, riding cmd 133's +0x08 like
#     the D24 capture lines; the arm queues the line whatever the group): 1..5
#     "arrived at blast point A..E, starting to plant" (5 reads D: SE's typo),
#     6..13 the plant countdown 30/15/10/5/4/3/2/1 s, 14 "planted", 15..19
#     "planted at point A..E", 20..24 "found explosives at defense point
#     A..E, starting to defuse", 25..35 the defuse countdown 90/60/45/30/15/
#     10/5/4/3/2/1 s, 36 "defused".
#
# OURS (no SE text or client code decides these):
#   * where things stand: the container on Capture & Hold's middle point; the
#     delivery points on each side's spawn point (rect ids 15/16, unlabelled,
#     coloured by owner); the beacons on Capture & Hold's quarter points
#     (O.C.U.'s on side A's, U.S.N.'s on side B's) and the target area on the
#     middle point (rect id 10, "A"); Demolition's five areas from
#     fmodata/fmo-demolition-points.tsv (tools/fmodatagen/fmoobjectives.py),
#     else five points straight off the spawn row.
#   * the take radius (FMO_PVP_GRAB), and that NPCs do not carry (SE: they did).
#   * crouching is not visible to the server (pvproom's Capture & Hold note):
#     "planted", "defused" and "beacon set" are "inside the area, alive, and
#     still within FMO_PVP_CH_STILL for the time". A beacon SET is its holder
#     standing still inside the target area for FMO_PVP_BEACON_SET seconds;
#     knocked down (転倒) is not visible either, so only destroyed / ejected /
#     dropped / left releases a hold.
#   * the Demolition defender: the nation holding the sector when the room is
#     made (fmowar), else the side that did not create the room.
#   * the time limit: Capture the Flag and Beacon Delivery fall back to the
#     units-standing rule; Demolition with a bomb ticking waits for the fuse.
#   * block+0x50 for Beacon Delivery (FMO_PVP_BEACON_KIND, 11: anything but 10
#     prints the beacon lines; 1..5 would print a wrong briefing line).
#
# WARNING: NOT SEEN LIVE. The class-6 POP was tried on 2026-08-22 in a LOBBY
# (sub 0 greyed the scene, sub 1 did not); sub 2 has never been popped in a
# battle. FMO_PVP_OBJ_POP=0 keeps the objects off the wire: the server still
# runs the rules on positions, the client just has nothing to draw or follow.

CMD_SEIZE = 140
CMD_DROP = 141
SEIZE_LEN = 0x28
DROP_LEN = 0x30
DROP_POS = 0x24
HOLD_SLOTS = 4
KIND_CONTAINER = 10
DEMO_KIND_ATTACK = 3                   # 79:14 "There is a target area."
DEMO_KIND_DEFEND = 4                   # 79:15 "... approaching the defense base."
#: block+0x50 for a Beacon Delivery room (OURS: anything but 10 prints the beacon lines)
BEACON_KIND = _env_int("FMO_PVP_BEACON_KIND", "11")
#: POP body+0x8E / +0x8B for a carried object: model class 6, subclass 2
OBJ_MODEL_FLAGS = 0x58
OBJ_MODEL_SUB = _env_int("FMO_PVP_OBJ_SUB", "2")
#: 1 (default) = POP the container / beacons to every pilot; 0 = rules only
OBJ_POP = _env_int("FMO_PVP_OBJ_POP", "1") != 0
#: the POP's client_kind (1 builds no peer link: nobody's client drives it)
OBJ_CLIENT_KIND = _env_int("FMO_PVP_OBJ_CLIENT_KIND", "1")
#: the objects' UnitIDs: distinct low two bits (the holder table's slots),
#: clear of the room aliases (0x200..), pilots (0x1000 + id) and squads (0x2222..)
CONTAINER_ID = 0x3A00
BEACON_IDS = {1: 0x3A01, 2: 0x3A02}
#: a wanzer this close (x/z, world units) to a free object takes it (OURS)
GRAB = _env_int("FMO_PVP_GRAB", "16")
#: seconds after a drop before anyone can take the object again (OURS)
GRAB_DELAY = _env_int("FMO_PVP_GRAB_DELAY", "2")
#: half the side of a delivery point / the beacon target area (OURS)
POINT_HALF = _env_int("FMO_PVP_POINT_HALF", "40")
#: seconds the beacon's holder stands still in the target area to set it (OURS)
BEACON_SET = _env_int("FMO_PVP_BEACON_SET", "3")
#: Demolition, SE's own numbers (topics 2006-09-20)
DEMO_PLANT = _env_int("FMO_PVP_DEMO_PLANT", "10")
DEMO_FUSE = _env_int("FMO_PVP_DEMO_FUSE", "70")
DEMO_FIND = _env_int("FMO_PVP_DEMO_FIND", "5")
DEMO_DEFUSE = _env_int("FMO_PVP_DEMO_DEFUSE", "20")
DEMO_HALF = _env_int("FMO_PVP_DEMO_HALF", "32")
DEMO_MSG_RES = 12                      # 0x1B335 + 12 = 111425, BB/F14/D25.DAT
DEMO_PLANT_COUNT = (30, 15, 10, 5, 4, 3, 2, 1)
DEMO_DEFUSE_COUNT = (90, 60, 45, 30, 15, 10, 5, 4, 3, 2, 1)
RECT_DELIVER = {1: 15, 2: 16}          # unlabelled ids, drawn in the owner's colour
RECT_TARGET = 10                       # "A"
DEMO_FIRST_ID = 10                     # A..E
GROUP = 1                              # one rect group, type 5 (cmd 133 owner writes)
Y_BELOW, Y_ABOVE = 8.0, 16.0
NATION_SIDE = {1: 0, 2: 1}             # popnation.NATION_SIDE: O.C.U. = side A
MODES = ("ctf", "beacon", "demo")
DEMO_TSV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fmodata",
    "fmo-demolition-points.tsv")


def demo_msg(kind, area=0, left=None):
    """cmd 133's +0x08 word for a Demolition line (D25): 'plant' / 'planted'
    / 'defuse' with the area 0..4, 'count' (plant) or 'dcount' (defuse) with
    the seconds left, 'defused'. Pure."""
    if kind in ("plant", "planted", "defuse") and not 0 <= area <= 4:
        raise ValueError("area %d: D25 names points A..E only" % area)
    if kind == "plant":
        idx = 1 + area
    elif kind == "planted":
        idx = 15 + area
    elif kind == "defuse":
        idx = 20 + area
    elif kind == "count" and left in DEMO_PLANT_COUNT:
        idx = 6 + DEMO_PLANT_COUNT.index(left)
    elif kind == "dcount" and left in DEMO_DEFUSE_COUNT:
        idx = 25 + DEMO_DEFUSE_COUNT.index(left)
    elif kind == "defused":
        idx = 36
    else:
        raise ValueError("demolition line %r/%r has no D25 row" % (kind, left))
    return (DEMO_MSG_RES << 16) | idx


def _table(pairs):
    """32 bytes: the four {object, holder} slots, each pair in slot obj & 3."""
    b = bytearray(8 * HOLD_SLOTS)
    for obj, holder in pairs:
        if obj and holder:
            struct.pack_into("<II", b, 8 * (obj & 3), obj & 0xFFFFFFFF, holder & 0xFFFFFFFF)
    return bytes(b)


def record_seize(obj, holder, pairs):
    """cmd 140: `holder` took `obj`; `pairs` = every (object, holder) after it."""
    return fmoworld.record(CMD_SEIZE, struct.pack("<II", obj & 0xFFFFFFFF,
                                                  holder & 0xFFFFFFFF) + _table(pairs))


def record_drop(obj, pos, pairs):
    """cmd 141: `obj` dropped at `pos`; `pairs` = the table after the drop."""
    b = bytearray(DROP_LEN)
    struct.pack_into("<I", b, 0, obj & 0xFFFFFFFF)
    b[4:4 + 8 * HOLD_SLOTS] = _table(pairs)
    struct.pack_into("<3f", b, DROP_POS, *[float(v) for v in pos[:3]])
    return fmoworld.record(CMD_DROP, bytes(b))


def load_demo_points(path=None):
    """{map: {0: [(x, y, z)] * 5, 1: [...]}} keyed by the DEFENDING side
    (0 = A, 1 = B); {} when the file is absent. '-' = no answer for a side."""
    rows = {}
    try:
        with open(path or DEMO_TSV, encoding="utf-8") as f:
            cols = f.readline().rstrip("\r\n").split("\t")
            for line in f:
                r = dict(zip(cols, line.rstrip("\r\n").split("\t")))
                try:
                    m = int(r["map"])
                except (KeyError, ValueError):
                    continue
                for side, col in ((0, "def_a"), (1, "def_b")):
                    try:
                        pts = [tuple(float(v) for v in tok.split(":"))
                               for tok in (r.get(col) or "").split()]
                    except ValueError:
                        continue
                    if len(pts) == 5 and all(len(p) == 3 for p in pts):
                        rows.setdefault(m, {})[side] = pts
    except OSError:
        return {}
    return rows


DEMO_ROWS = load_demo_points()


def _box(center, half):
    x, y, z = center
    return (x - half, y - Y_BELOW, z - half), (x + half, y + Y_ABOVE, z + half)


def _spawn(mapno, spawn=None):
    if spawn is None:
        from . import battlepop
        spawn = battlepop.spawn_row(mapno)
    return spawn


def demo_points(mapno, defender_side, rows=None, spawn=None):
    """([(x, y, z)] * 5, source) for the defending side 0/1, or ([], why)."""
    rows = DEMO_ROWS if rows is None else rows
    got = (rows.get(int(mapno)) or {}).get(defender_side)
    if got:
        return list(got), "fmo-demolition-points.tsv"
    sp = _spawn(mapno, spawn)
    if not sp:
        return [], "map %s has no demolition row and no spawn row" % mapno
    d, o = (sp["a"], sp["b"]) if defender_side == 0 else (sp["b"], sp["a"])
    dx, dz = o[0] - d[0], o[1] - d[1]
    dab = math.hypot(dx, dz) or 1.0
    px, pz = -dz / dab, dx / dab
    out = []
    for t, lat in ((0.18, -0.1), (0.18, 0.1), (0.3, 0.0), (0.42, -0.1), (0.42, 0.1)):
        s = max(-320.0, min(320.0, lat * dab))
        out.append((round(d[0] + t * dx + s * px, 1), sp["y"] + 4.0,
                    round(d[1] + t * dz + s * pz, 1)))
    return out, "straight off the spawn row (ground under the points NOT checked)"


def inside(lo, hi, pos):
    """x/z containment (the motion y is the wanzer's, the box y a guess)."""
    return (pos is not None and lo[0] <= pos[0] <= hi[0] and lo[2] <= pos[2] <= hi[2])


def new_state(mode, mapno, defender=None, zone_points=None, spawn=None, demo_rows=None):
    """The objective state a room of `mode` starts with, or (None, why) when
    the map has no geometry for it. `defender` (Demolition) = a nation 1/2."""
    from . import pvproom
    st = {"mode": mode, "objects": {}, "rects": [], "outbox": {}, "popped": set(),
          "group": GROUP, "bomb": None, "work": None, "defender": None,
          "attacker": None}
    if mode in ("ctf", "beacon"):
        pts = zone_points
        if pts is None:
            pts = pvproom.ch_zone_points(mapno, spawn=spawn)[0]
        sp = _spawn(mapno, spawn)
        if len(pts) < 3 or (mode == "ctf" and not sp):
            return None, "map %s: no zone points%s" % (
                mapno, "" if sp or mode != "ctf" else " / no spawn row")
        if mode == "ctf":
            st["objects"][CONTAINER_ID] = _obj(CONTAINER_ID, "container", 0, pts[1])
            for n, end in ((1, sp["a"]), (2, sp["b"])):
                c = (end[0], sp["y"] + 4.0, end[1])
                lo, hi = _box(c, POINT_HALF)
                st["rects"].append({"id": RECT_DELIVER[n], "role": "deliver", "nation": n,
                                    "center": c, "lo": lo, "hi": hi, "owner": n})
        else:
            st["objects"][BEACON_IDS[1]] = _obj(BEACON_IDS[1], "beacon", 1, pts[0])
            st["objects"][BEACON_IDS[2]] = _obj(BEACON_IDS[2], "beacon", 2, pts[2])
            lo, hi = _box(pts[1], POINT_HALF)
            st["rects"].append({"id": RECT_TARGET, "role": "target", "nation": 0,
                                "center": tuple(pts[1]), "lo": lo, "hi": hi, "owner": 0})
        return st, None
    if mode == "demo":
        if defender not in (1, 2):
            return None, "no defender"
        pts, src = demo_points(mapno, NATION_SIDE[defender], demo_rows, spawn)
        if len(pts) != 5:
            return None, src
        st["defender"], st["attacker"], st["src"] = defender, 3 - defender, src
        for i, c in enumerate(pts):
            lo, hi = _box(c, DEMO_HALF)
            st["rects"].append({"id": DEMO_FIRST_ID + i, "index": i, "role": "demo",
                                "nation": defender, "center": tuple(c), "lo": lo,
                                "hi": hi, "owner": defender})
        return st, None
    return None, "mode %r has no objective state" % (mode,)


def _obj(uid, kind, nation, pos):
    return {"id": uid, "kind": kind, "nation": nation, "home": tuple(pos),
            "pos": tuple(pos), "holder": None, "free_at": 0.0, "set": None}


def block_knobs(st, side=None):
    """mission_fields() keywords for a pilot of nation `side` in a room
    whose objective state is `st`: the kind, the rects and the group."""
    from . import missionblock
    if st["mode"] == "ctf":
        kind = KIND_CONTAINER
    elif st["mode"] == "beacon":
        kind = BEACON_KIND
    else:
        kind = DEMO_KIND_DEFEND if side == st["defender"] else DEMO_KIND_ATTACK
    out = {"objective_kind": kind}
    if st["rects"]:
        out["rects"] = [missionblock.rect_record(r["id"], r["lo"], r["hi"], r["owner"])
                        for r in st["rects"]]
        out["rect_groups"] = [missionblock.rect_group_record(st["group"])]
    return out


def resync_rects(st):
    from . import missionblock
    return [(r["id"], r["owner"], missionblock.RECT_FLAG_DRAWN) for r in st["rects"]]


# --------------------------------------------------------------------------- #
# per-recipient rendering: a holder is a UnitID, and every client knows the
# other pilots by its OWN alias for them (worldchannel.alias_for)
# --------------------------------------------------------------------------- #
def uid_for(room, bkey, chan):
    """The UnitID by which `chan`'s client knows member `bkey`, or 0."""
    m = room["members"].get(bkey) if bkey is not None else None
    if not m:
        return 0
    if m.get("addr") is not None and m.get("addr") == getattr(chan, "addr", None):
        return m.get("uid") or 0
    a = (getattr(chan, "alias_of", None) or {}).get(m.get("addr"))
    return a or m.get("uid") or 0


def _pairs(room, holders, chan):
    return [(obj, uid_for(room, k, chan)) for obj, k in holders.items() if k is not None]


def holder_pairs(room, chan):
    """[(object, holder UnitID)] for cmd 148's +0x94 table, as `chan` sees it."""
    st = room.get("obj") or {}
    return _pairs(room, {o["id"]: o["holder"] for o in st.get("objects", {}).values()
                         if o["holder"] is not None}, chan)


def render(room, ev, chan):
    """One queued event as a record for `chan`."""
    if ev[0] == "rec":
        return ev[1]
    if ev[0] == "seize":
        _t, obj, k, holders = ev
        return record_seize(obj, uid_for(room, k, chan), _pairs(room, holders, chan))
    _t, obj, pos, holders = ev
    return record_drop(obj, pos, _pairs(room, holders, chan))


def pops_for(room, k):
    """The POP records of every object, once per member `k` (at its
    current place: a held one where its holder stands)."""
    st = room.get("obj")
    if not st or not OBJ_POP or k in st["popped"]:
        return []
    st["popped"].add(k)
    out = []
    for o in st["objects"].values():
        x, y, z = o["pos"]
        try:
            out.append(fmoworld.record_pop(
                o["id"], unit_type=0, pos=(x, y, z, 0.0), client_kind=OBJ_CLIENT_KIND,
                model_flags=OBJ_MODEL_FLAGS, model_sub=OBJ_MODEL_SUB,
                nation=o["nation"] or None,
                name1=("Container" if o["kind"] == "container" else "Beacon"),
                name2=("" if not o["nation"] else {1: "OCU", 2: "USN"}[o["nation"]]),
                battle=True))
        except ValueError as e:
            log(f"PVP ROOM {room['id']}: WARNING: object {o['id']:#x} POP refused "
                f"by our own guard: {e}")
    return out


def _broadcast(room, ev, sides=None):
    box = room["obj"]["outbox"]
    for k, m in room["members"].items():
        if m.get("delivered") or (sides is not None and m["side"] not in sides):
            continue
        q = box.setdefault(k, [])
        if len(q) < 64:
            q.append(ev)


def _holders(st):
    return {o["id"]: o["holder"] for o in st["objects"].values() if o["holder"] is not None}


def _say133(room, r, msg, sides=None):
    st = room["obj"]
    _broadcast(room, ("rec", fmoworld.record_zone_state(st["group"], r.get("index", 0),
                                                        r["owner"], msg)), sides)


def _still(work, pos, still):
    return not still or math.hypot(pos[0] - work["at"][0], pos[2] - work["at"][2]) <= still


def _name(room, k):
    m = room["members"].get(k) or {}
    return m.get("account") or k


def tick(room, now=None, online=None, deaths=None):
    """Advance a started objective room from where its pilots stand. Queues
    the records for every member and decides the room when the objective is
    met. Returns [event text] for the log and the selftest."""
    from . import battleend, pvproom, trade
    st = room.get("obj")
    if not st or room["started"] is None or room["verdict"] is not None:
        return []
    now = time.time() if now is None else now
    online = online or (lambda a: trade.session_for_account(a) is not None)
    deaths = battleend.PILOT_DEATHS if deaths is None else deaths
    up = {k: m for k, m in room["members"].items()
          if m["side"] in (1, 2) and m.get("pos") is not None
          and not m.get("delivered") and not pvproom.member_out(m, k, now, online, deaths)}
    if st["mode"] == "demo":
        return _tick_demo(room, st, up, now)
    events = []
    for o in sorted(st["objects"].values(), key=lambda o: o["id"]):
        if o["holder"] is not None:
            hm = up.get(o["holder"])
            if hm is None:
                who = o["holder"]
                o["holder"], o["set"], o["free_at"] = None, None, now + GRAB_DELAY
                _broadcast(room, ("drop", o["id"], o["pos"], _holders(st)))
                events.append(f"{o['kind']} {o['id']:#x} dropped by {who}")
                log(f"PVP ROOM {room['id']}: {o['kind'].upper()} {o['id']:#x} DROPPED by "
                    f"{_name(room, who)} (out of the battle) at ({o['pos'][0]:.0f}, "
                    f"{o['pos'][2]:.0f}); cmd 141 to every member")
                continue
            o["pos"] = tuple(hm["pos"][:3])
            continue
        if now < o["free_at"]:
            continue
        best = None
        for k, m in up.items():
            d = math.hypot(m["pos"][0] - o["pos"][0], m["pos"][2] - o["pos"][2])
            if d <= GRAB and (best is None or (d, m.get("joined", 0)) < best[0]):
                best = ((d, m.get("joined", 0)), k)
        if best is not None:
            k = best[1]
            o["holder"], o["set"] = k, None
            o["pos"] = tuple(up[k]["pos"][:3])
            _broadcast(room, ("seize", o["id"], k, _holders(st)))
            events.append(f"{o['kind']} {o['id']:#x} seized by {k}")
            log(f"PVP ROOM {room['id']}: {o['kind'].upper()} {o['id']:#x}"
                + (f" (nation {o['nation']}'s)" if o["nation"] else "")
                + f" SEIZED by {_name(room, k)} (nation {room['members'][k]['side']}); "
                f"cmd 140 to every member, the client prints 32:"
                f"{17 if st['mode'] == 'ctf' else 19}")
    # the win: the holder reaches its point
    for o in sorted(st["objects"].values(), key=lambda o: o["id"]):
        k = o["holder"]
        if k is None:
            continue
        m = up[k]
        if st["mode"] == "ctf":
            r = next((r for r in st["rects"] if r["nation"] == m["side"]), None)
            if r is not None and inside(r["lo"], r["hi"], m["pos"]):
                events.append(f"delivered by {m['side']}")
                pvproom._decide(room, m["side"], f"nation {m['side']} delivered the "
                                f"container ({_name(room, k)}; Capture the Flag)", now)
                return events
            continue
        r = next((r for r in st["rects"] if r["role"] == "target"), None)
        if r is None or not inside(r["lo"], r["hi"], m["pos"]):
            o["set"] = None
            continue
        if o["set"] is None or o["set"]["who"] != k or not _still(o["set"], m["pos"], pvproom.CH_STILL):
            o["set"] = {"who": k, "since": now, "at": tuple(m["pos"][:3])}
            continue
        if now - o["set"]["since"] >= BEACON_SET:
            w = o["nation"]
            events.append(f"beacon {o['nation']} set by {m['side']}")
            pvproom._decide(room, w, f"nation {o['nation']}'s beacon was set in the "
                            f"target area by {_name(room, k)} (nation {m['side']}; "
                            f"Beacon Delivery)", now)
            return events
    return events


def _tick_demo(room, st, up, now):
    from . import pvproom
    events = []
    still = pvproom.CH_STILL
    att, dfd = st["attacker"], st["defender"]
    bomb, work = st["bomb"], st["work"]
    if bomb is None:
        # a plant in progress: the planter must stay inside, alive and still
        if work is not None:
            m = up.get(work["who"])
            r = st["rects"][work["area"]]
            if m is None or not inside(r["lo"], r["hi"], m["pos"]) or not _still(work, m["pos"], still):
                st["work"] = None
                events.append("plant stopped")
                log(f"PVP ROOM {room['id']}: DEMOLITION: the plant at "
                    f"{chr(65 + work['area'])} by {_name(room, work['who'])} STOPPED "
                    f"after {int(now - work['since'])} s")
                work = None
            else:
                left = DEMO_PLANT - (now - work["since"])
                if left <= 0:
                    st["bomb"] = {"area": work["area"], "at": now, "by": work["who"]}
                    st["work"] = None
                    _say133(room, r, demo_msg("planted", work["area"]), {att})
                    events.append(f"planted at {chr(65 + work['area'])}")
                    log(f"PVP ROOM {room['id']}: DEMOLITION: BOMB PLANTED at "
                        f"{chr(65 + work['area'])} by {_name(room, work['who'])}; it "
                        f"goes off in {DEMO_FUSE} s (the defenders are not told)")
                    return events
                due = [s for s in DEMO_PLANT_COUNT if s < DEMO_PLANT and left <= s
                       and s not in work["said"]]
                if due:
                    work["said"].update(due)
                    _say133(room, r, demo_msg("count", left=min(due)), {att})
                return events
        for k, m in sorted(up.items(), key=lambda kv: kv[1].get("joined", 0)):
            if m["side"] != att:
                continue
            for r in st["rects"]:
                if inside(r["lo"], r["hi"], m["pos"]):
                    st["work"] = {"who": k, "area": r["index"], "since": now,
                                  "at": tuple(m["pos"][:3]), "said": set()}
                    _say133(room, r, demo_msg("plant", r["index"]), {att})
                    events.append(f"plant started at {chr(65 + r['index'])}")
                    log(f"PVP ROOM {room['id']}: DEMOLITION: {_name(room, k)} (nation "
                        f"{att}) STARTS planting at {chr(65 + r['index'])}; {DEMO_PLANT} s")
                    return events
        return events
    # the bomb ticks
    r = st["rects"][bomb["area"]]
    if now - bomb["at"] >= DEMO_FUSE:
        events.append("exploded")
        pvproom._decide(room, att, f"the bomb at {chr(65 + bomb['area'])} went off "
                        f"(nation {att} attacked; Demolition)", now)
        return events
    if work is not None:
        m = up.get(work["who"])
        if m is None or not inside(r["lo"], r["hi"], m["pos"]) or not _still(work, m["pos"], still):
            st["work"] = None
            events.append("defuse stopped")
            log(f"PVP ROOM {room['id']}: DEMOLITION: the defuse by "
                f"{_name(room, work['who'])} STOPPED after {int(now - work['since'])} s")
            return events
        t = now - work["since"]
        if not work["found"]:
            if t >= DEMO_FIND:
                work["found"], work["since"] = True, now
                _say133(room, r, demo_msg("defuse", bomb["area"]), {dfd})
                events.append("defuse started")
                log(f"PVP ROOM {room['id']}: DEMOLITION: {_name(room, work['who'])} found "
                    f"the bomb at {chr(65 + bomb['area'])}: the defuse runs {DEMO_DEFUSE} s")
            return events
        left = DEMO_DEFUSE - t
        if left <= 0:
            _say133(room, r, demo_msg("defused"), {dfd})
            events.append("defused")
            pvproom._decide(room, dfd, f"the bomb at {chr(65 + bomb['area'])} was "
                            f"defused by {_name(room, work['who'])} (nation {dfd} "
                            f"defended; Demolition)", now)
            return events
        due = [s for s in DEMO_DEFUSE_COUNT if s < DEMO_DEFUSE and left <= s
               and s not in work["said"]]
        if due:
            work["said"].update(due)
            _say133(room, r, demo_msg("dcount", left=min(due)), {dfd})
        return events
    for k, m in sorted(up.items(), key=lambda kv: kv[1].get("joined", 0)):
        if m["side"] == dfd and inside(r["lo"], r["hi"], m["pos"]):
            st["work"] = {"who": k, "since": now, "at": tuple(m["pos"][:3]),
                          "found": False, "said": set()}
            events.append("defuse search")
            break
    return events


def time_verdict(room, counts, fielded, now, limit):
    """(winner, why) at the time limit of an objective room, or None to
    keep waiting (a Demolition bomb still ticking)."""
    from . import pvproom
    st = room["obj"]
    if st["mode"] == "demo":
        if st["bomb"] is not None:
            return None
        return st["defender"], (f"time limit with no bomb planted: nation "
                                f"{st['defender']} defended (Demolition, SE's rule)")
    v = pvproom.judge_counts(counts, fielded, room["started"], now, limit)
    if v is None:
        return 0, "time limit: a draw (OUR rule)"
    return v[0], v[1] + f" ({'Capture the Flag' if st['mode'] == 'ctf' else 'Beacon Delivery'})"


def describe(room):
    """One log line about where the room's objectives stand."""
    st = room["obj"]
    parts = []
    for o in st["objects"].values():
        parts.append(f"{o['kind']} {o['id']:#x}"
                     + (f" (nation {o['nation']})" if o["nation"] else "")
                     + f" at ({o['home'][0]:g}, {o['home'][2]:g}) y {o['home'][1]:g}")
    for r in st["rects"]:
        parts.append(f"{r['role']} rect {r['id']} at ({r['center'][0]:g}, "
                     f"{r['center'][2]:g}) owner {r['owner']}")
    return "; ".join(parts)
